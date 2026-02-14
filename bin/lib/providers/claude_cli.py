"""
Claude CLI provider adapter for Mason.
Executes tasks using Claude CLI.
"""
import os
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

from .base import BaseProvider, ArtifactBundle


class ClaudeCLIProvider(BaseProvider):
    """
    Provider adapter for Claude CLI.
    Uses CLI to execute tasks with Claude API.
    """

    RATE_LIMIT_PATTERNS = [
        'rate limit',
        'too many requests',
        'quota exceeded',
        '429',
        'overloaded',
    ]

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.executable = config.get('executable', 'claude')
        self.timeout = config.get('timeout_seconds', 180)
        self.patterns = config.get('rate_limit_patterns', self.RATE_LIMIT_PATTERNS)

    def generate(self, task_packet: Dict[str, Any]) -> ArtifactBundle:
        """Execute task using Claude CLI."""
        task_id = task_packet['identity']['task_id']
        start_time = time.time()

        try:
            # Create working directory
            work_dir = self._create_work_dir(task_id)

            # Build prompt
            prompt = self._build_prompt(task_packet)

            # Execute Claude CLI
            result = subprocess.run(
                [self.executable, prompt],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            # Check for rate limiting
            combined_output = result.stdout + result.stderr
            if self._is_rate_limited(combined_output):
                return ArtifactBundle(
                    task_id=task_id,
                    provider=self.name,
                    execution_status='provider_failure',
                    logs=combined_output,
                    error='Rate limited',
                    is_rate_limit=True,
                    duration_ms=duration_ms,
                )

            if result.returncode == 0:
                files_modified = self._detect_modified_files(work_dir)
                return ArtifactBundle(
                    task_id=task_id,
                    provider=self.name,
                    execution_status='success',
                    files_modified=files_modified,
                    logs=result.stdout,
                    duration_ms=duration_ms,
                    artifacts_path=str(work_dir),
                )
            else:
                return ArtifactBundle(
                    task_id=task_id,
                    provider=self.name,
                    execution_status='failure',
                    logs=combined_output,
                    error=result.stderr,
                    duration_ms=duration_ms,
                )

        except subprocess.TimeoutExpired:
            return ArtifactBundle(
                task_id=task_id,
                provider=self.name,
                execution_status='failure',
                error=f"Timeout after {self.timeout}s",
                duration_ms=self.timeout * 1000,
            )
        except Exception as e:
            if self.detect_rate_limit(e):
                return ArtifactBundle(
                    task_id=task_id,
                    provider=self.name,
                    execution_status='provider_failure',
                    error=str(e),
                    is_rate_limit=True,
                )
            return ArtifactBundle(
                task_id=task_id,
                provider=self.name,
                execution_status='failure',
                error=str(e),
            )

    def is_available(self) -> bool:
        """Check if Claude CLI is available."""
        try:
            result = subprocess.run(
                [self.executable, '--version'],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def detect_rate_limit(self, error: Exception) -> bool:
        """Detect if error indicates rate limiting."""
        error_str = str(error).lower()
        return any(pattern in error_str for pattern in self.patterns)

    def _is_rate_limited(self, output: str) -> bool:
        """Check if output indicates rate limiting."""
        output_lower = output.lower()
        return any(pattern in output_lower for pattern in self.patterns)

    def _create_work_dir(self, task_id: str) -> Path:
        """Create isolated working directory for task."""
        base = Path(tempfile.gettempdir()) / "mason" / "claude"
        base.mkdir(parents=True, exist_ok=True)
        work_dir = base / task_id
        work_dir.mkdir(exist_ok=True)
        return work_dir

    def _build_prompt(self, task_packet: Dict[str, Any]) -> str:
        """Build Claude prompt from task packet."""
        goal = task_packet.get('goal', {})
        return f"""
Task: {goal.get('title', 'Unknown')}

{goal.get('description', '')}

Success Criteria:
{chr(10).join('- ' + c for c in goal.get('success_criteria', []))}
"""

    def _detect_modified_files(self, work_dir: Path) -> list:
        """Detect files modified in working directory."""
        return [
            str(f.relative_to(work_dir))
            for f in work_dir.rglob('*')
            if f.is_file() and not f.name.startswith('.')
        ]
