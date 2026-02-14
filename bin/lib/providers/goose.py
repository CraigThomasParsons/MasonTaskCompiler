"""
Goose provider adapter for Mason.
Executes tasks using Goose CLI with Ollama backend.
"""
import os
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

from .base import BaseProvider, ArtifactBundle


class GooseProvider(BaseProvider):
    """
    Provider adapter for Goose (local AI coding assistant).
    Uses CLI to execute tasks in isolated directories.
    """

    RATE_LIMIT_PATTERNS = []  # Goose doesn't rate limit

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.executable = config.get('executable', 'goose')
        self.model = config.get('model', 'qwen2.5-coder:14b')
        self.timeout = config.get('timeout_seconds', 300)

    def generate(self, task_packet: Dict[str, Any]) -> ArtifactBundle:
        """Execute task using Goose CLI."""
        task_id = task_packet['identity']['task_id']
        start_time = time.time()

        try:
            # Create working directory
            work_dir = self._create_work_dir(task_id)

            # Write task packet as context
            task_file = work_dir / "task.json"
            with open(task_file, 'w') as f:
                json.dump(task_packet, f, indent=2)

            # Build prompt from task packet
            prompt = self._build_prompt(task_packet)

            # Execute Goose
            result = subprocess.run(
                [self.executable, 'run', '--model', self.model, prompt],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            # Parse result
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
                    logs=result.stdout + "\n" + result.stderr,
                    error=result.stderr,
                    duration_ms=duration_ms,
                    artifacts_path=str(work_dir),
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
        """Check if Goose CLI is available."""
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
        """Goose doesn't have rate limits."""
        return False

    def _create_work_dir(self, task_id: str) -> Path:
        """Create isolated working directory for task."""
        base = Path(tempfile.gettempdir()) / "mason" / "goose"
        base.mkdir(parents=True, exist_ok=True)
        work_dir = base / task_id
        work_dir.mkdir(exist_ok=True)
        return work_dir

    def _build_prompt(self, task_packet: Dict[str, Any]) -> str:
        """Build Goose prompt from task packet."""
        goal = task_packet.get('goal', {})
        constraints = task_packet.get('constraints', {})
        inputs = task_packet.get('inputs', {})

        prompt_parts = [
            f"# Task: {goal.get('title', 'Unknown')}",
            "",
            goal.get('description', ''),
            "",
            "## Success Criteria",
        ]

        for criterion in goal.get('success_criteria', []):
            prompt_parts.append(f"- {criterion}")

        if constraints.get('style_rules'):
            prompt_parts.append("")
            prompt_parts.append("## Style Rules")
            for rule in constraints['style_rules']:
                prompt_parts.append(f"- {rule}")

        if inputs.get('retry_guidance'):
            prompt_parts.append("")
            prompt_parts.append("## Previous Attempt Feedback")
            for guidance in inputs['retry_guidance']:
                prompt_parts.append(f"- {guidance}")

        return "\n".join(prompt_parts)

    def _detect_modified_files(self, work_dir: Path) -> list:
        """Detect files modified in working directory."""
        # In practice, this would use git diff or file timestamps
        # For now, list all non-hidden files
        return [
            str(f.relative_to(work_dir))
            for f in work_dir.rglob('*')
            if f.is_file() and not f.name.startswith('.')
        ]
