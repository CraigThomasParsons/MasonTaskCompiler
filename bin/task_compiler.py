"""
Task Compiler for Mason.
Decomposes stories into executable tasks.
"""
import uuid
import structlog
from typing import Any, Dict, List, Optional
from datetime import datetime

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

        if len(criteria) <= 3:
            # Simple story - single task
            return [self._create_task_packet(story, criteria)]

        # Complex story - decompose by criteria groups
        tasks = []
        for i in range(0, len(criteria), 3):
            group = criteria[i:i+3]
            if len(tasks) < self.max_tasks:
                tasks.append(self._create_task_packet(
                    story, group, task_index=i // 3
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
                "style_rules": [],
                "forbidden": [],
            },
            "inputs": {
                "context_files": [],
                "dependencies": [],
                "retry_guidance": [],
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
