"""
Task Compiler for Mason.
Decomposes stories into executable tasks.
"""
import uuid
import structlog
from typing import Any, Dict, List, Optional
from datetime import datetime
from pathlib import Path

from lib.config import Config
from lib.backlog_client import Story

logger = structlog.get_logger()


class TaskCompiler:
    """
    Compiles stories into TaskPackets.
    
    TaskPacket v1 structure:
    {
        "identity": { task_id, story_id, epic_id },
        "goal": { title, description, success_criteria },
        "constraints": { file_scope, style_rules, forbidden },
        "inputs": { context_files, dependencies, retry_guidance },
        "execution": { max_attempts, current_attempt, timeout },
        "provider_context": { preferred_model, complexity_hint },
        "metadata": { created_at, source_domain }
    }
    """

    def __init__(self, config: Config):
        self.config = config
        self.max_tasks = config.max_tasks_per_story
        self.default_max_attempts = config.default_max_attempts

    def compile(self, story: Story) -> List[Dict[str, Any]]:
        """
        Compile a story into one or more TaskPackets.
        
        For simple stories, produces a single task.
        For complex stories with multiple acceptance criteria,
        may decompose into multiple tasks.
        """
        criteria = self._parse_acceptance_criteria(story.acceptance_criteria)

        if not criteria:
            return [self._create_task_packet(story, ["Implement story behavior and verify expected result."])]

        # Always decompose into low-effort tasks: one acceptance criterion per task.
        tasks = []
        for i, criterion in enumerate(criteria):
            if len(tasks) >= self.max_tasks:
                break
            tasks.append(self._create_task_packet(
                story, [criterion], task_index=i
            ))

        logger.info(
            "story_decomposed",
            story_id=story.id,
            task_count=len(tasks),
            criteria_count=len(criteria),
        )

        return tasks

    def _create_task_packet(
        self,
        story: Story,
        criteria: List[str],
        task_index: int = 0
    ) -> Dict[str, Any]:
        """Create a TaskPacket from story and criteria."""
        task_id = str(uuid.uuid4())

        title = story.title
        if task_index > 0:
            title = f"{story.title} (Part {task_index + 1})"

        return {
            "identity": {
                "task_id": task_id,
                "story_id": story.id,
                "epic_id": story.epic_id,
            },
            "goal": {
                "title": title,
                "description": story.narrative,
                "success_criteria": criteria,
            },
            "constraints": {
                "file_scope": self._infer_file_scope(story),
                "style_rules": self._resolve_style_rules(story),
                "forbidden": [],
            },
            "inputs": {
                "context_files": self._resolve_context_files(story),
                "dependencies": [],
                "retry_guidance": [],
                "project_context": story.project or {},
            },
            "execution": {
                "max_attempts": self.default_max_attempts,
                "current_attempt": 0,
                "timeout_seconds": 300,
            },
            "provider_context": {
                "preferred_model": None,
                "complexity_hint": self._estimate_complexity(story),
            },
            "metadata": {
                "created_at": datetime.utcnow().isoformat() + "Z",
                "source_domain": "devbacklog",
                "priority": story.priority,
                "est_points": story.est_points,
            },
        }

    def _parse_acceptance_criteria(self, criteria_text: str) -> List[str]:
        """Parse acceptance criteria from text."""
        if not criteria_text:
            return []

        lines = criteria_text.strip().split('\n')
        criteria = []

        for line in lines:
            line = line.strip()
            # Remove bullet markers
            if line.startswith(('-', '*', '•', '✓')):
                line = line[1:].strip()
            # Remove numbered markers
            if line and line[0].isdigit() and '.' in line[:3]:
                line = line.split('.', 1)[1].strip()

            if line:
                criteria.append(line)

        return criteria

    def _infer_file_scope(self, story: Story) -> List[str]:
        """Infer likely file scope from story content."""
        # This would use NLP/patterns in production
        # For now, return empty
        return []

    def _resolve_context_files(self, story: Story) -> List[str]:
        project = story.project or {}
        local_location = project.get('local_location') or project.get('code_folder')
        if not local_location:
            return []

        base = Path(local_location)
        context_path = base / 'context.md'
        goals_path = base / 'goals.md'
        files: List[str] = []
        if context_path.exists():
            files.append(str(context_path))
        if goals_path.exists():
            files.append(str(goals_path))
        return files

    def _resolve_style_rules(self, story: Story) -> List[str]:
        project = story.project or {}
        local_location = project.get('local_location') or project.get('code_folder')
        candidates: List[Path] = []

        if local_location:
            base = Path(local_location)
            candidates.extend([
                base / 'php_style.md',
                base / 'docs' / 'php_style.md',
            ])

        candidates.extend([
            Path('/home/craigpar/Code/ChatProjects/docs/php_style.md'),
            Path('/home/craigpar/Code/RTSColonyTerrainGenerator/docs/php_style.md'),
        ])

        for path in candidates:
            if path.exists():
                return [
                    f'Follow PHP style guide in {path}',
                    'Apply TYS loop: build one small behavior, test, fix if failing, repeat.',
                    'Log each iteration in docs/thoughts.md in the target project.',
                ]

        return [
            'Use clean Laravel + Blade conventions with consistent naming and guard clauses.',
            'Apply TYS loop: build one small behavior, test, fix if failing, repeat.',
            'Log each iteration in docs/thoughts.md in the target project.',
        ]

    def _estimate_complexity(self, story: Story) -> str:
        """Estimate task complexity."""
        if story.est_points is None:
            return "medium"

        if story.est_points <= 2:
            return "low"
        elif story.est_points <= 5:
            return "medium"
        else:
            return "high"

    def enrich_for_retry(
        self,
        task_packet: Dict[str, Any],
        guidance: List[str],
        attempt: int
    ) -> Dict[str, Any]:
        """Enrich task packet with retry guidance."""
        enriched = task_packet.copy()
        enriched['inputs'] = task_packet.get('inputs', {}).copy()
        enriched['inputs']['retry_guidance'] = guidance

        enriched['execution'] = task_packet.get('execution', {}).copy()
        enriched['execution']['current_attempt'] = attempt

        return enriched
