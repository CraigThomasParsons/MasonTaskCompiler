"""
Codex CLI provider adapter for Mason.
Executes tasks non-interactively via `codex exec`.
"""
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

from .base import BaseProvider, ArtifactBundle


class CodexCLIProvider(BaseProvider):
    RATE_LIMIT_PATTERNS = [
        'rate limit',
        'too many requests',
        'quota',
        '429',
    ]

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.executable = config.get('executable', 'codex')
        self.timeout = config.get('timeout_seconds', 600)
        self.model = config.get('model')
        self.patterns = config.get('rate_limit_patterns', self.RATE_LIMIT_PATTERNS)

    def generate(self, task_packet: Dict[str, Any]) -> ArtifactBundle:
        task_id = task_packet['identity']['task_id']
        start = time.time()
        work_dir = self._resolve_work_dir(task_packet, task_id)
        before = self._git_status(work_dir)
        prompt = self._build_prompt(task_packet)

        cmd = [
            self.executable,
            'exec',
            '--full-auto',
            '--sandbox',
            'workspace-write',
            '--cd',
            str(work_dir),
            prompt,
        ]
        if self.model:
            cmd.extend(['--model', self.model])

        try:
            result = subprocess.run(
                cmd,
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            duration_ms = int((time.time() - start) * 1000)
            logs = (result.stdout or '') + "\n" + (result.stderr or '')

            if result.returncode == 0:
                return ArtifactBundle(
                    task_id=task_id,
                    provider=self.name,
                    execution_status='success',
                    files_modified=self._detect_modified_files(work_dir, before),
                    logs=logs,
                    duration_ms=duration_ms,
                    artifacts_path=str(work_dir),
                )

            if self._is_rate_limited(logs):
                return ArtifactBundle(
                    task_id=task_id,
                    provider=self.name,
                    execution_status='provider_failure',
                    error='Rate limited',
                    logs=logs,
                    is_rate_limit=True,
                    duration_ms=duration_ms,
                )

            return ArtifactBundle(
                task_id=task_id,
                provider=self.name,
                execution_status='failure',
                error=f'codex exit {result.returncode}',
                logs=logs,
                duration_ms=duration_ms,
                artifacts_path=str(work_dir),
            )
        except subprocess.TimeoutExpired:
            return ArtifactBundle(
                task_id=task_id,
                provider=self.name,
                execution_status='failure',
                error=f'Timeout after {self.timeout}s',
                duration_ms=self.timeout * 1000,
                artifacts_path=str(work_dir),
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
        try:
            result = subprocess.run(
                [self.executable, '--version'],
                capture_output=True,
                timeout=8,
            )
            return result.returncode == 0
        except Exception:
            return False

    def detect_rate_limit(self, error: Exception) -> bool:
        err = str(error).lower()
        return any(pattern in err for pattern in self.patterns)

    def _resolve_work_dir(self, task_packet: Dict[str, Any], task_id: str) -> Path:
        project = task_packet.get('inputs', {}).get('project_context', {}) or {}
        target = project.get('local_location') or project.get('code_folder')
        if target and Path(target).exists():
            return Path(target)

        base = Path(tempfile.gettempdir()) / 'mason' / 'codex'
        base.mkdir(parents=True, exist_ok=True)
        fallback = base / task_id
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    def _build_prompt(self, task_packet: Dict[str, Any]) -> str:
        goal = task_packet.get('goal', {})
        constraints = task_packet.get('constraints', {})
        inputs = task_packet.get('inputs', {})
        project = inputs.get('project_context', {}) or {}

        criteria = "\n".join(f"- {item}" for item in goal.get('success_criteria', []))
        style = "\n".join(f"- {item}" for item in constraints.get('style_rules', []))
        if not style:
            style = "- Follow existing project conventions."

        return f"""Implement this task directly in the current working directory.

Task: {goal.get('title', 'Unknown')}
Description:
{goal.get('description', '')}

Success Criteria:
{criteria}

Project Context:
- Local Location: {project.get('local_location') or project.get('code_folder')}
- GitHub Repo: {project.get('github_repo')}
- Framework: {project.get('framework_description')}
- Languages: {project.get('languages')}

Style Rules:
{style}

Required TYS loop:
1) Build one small behavior.
2) Run the most relevant test command available.
3) If failing, fix smallest root cause and re-run.
4) Append brief notes to docs/thoughts.md with intent/result/changes.

Only do this task. Keep changes scoped.
"""

    def _is_rate_limited(self, output: str) -> bool:
        lowered = output.lower()
        return any(pattern in lowered for pattern in self.patterns)

    def _git_status(self, work_dir: Path) -> str:
        try:
            result = subprocess.run(
                ['git', 'status', '--porcelain'],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout
        except Exception:
            return ''

    def _detect_modified_files(self, work_dir: Path, before: str) -> list:
        try:
            result = subprocess.run(
                ['git', 'status', '--porcelain'],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )
            before_set = set(line.strip() for line in before.splitlines() if line.strip())
            after_set = set(line.strip() for line in result.stdout.splitlines() if line.strip())
            changed = sorted(after_set - before_set)
            return [line[3:] for line in changed if len(line) > 3]
        except Exception:
            return []
