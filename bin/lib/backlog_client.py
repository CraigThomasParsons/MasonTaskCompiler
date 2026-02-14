"""
DevBacklog API client for Mason.
Fetches stories ready for decomposition into tasks.
"""
import httpx
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class Story:
    """Story from DevBacklog."""
    id: int
    title: str
    narrative: str
    acceptance_criteria: str
    epic_id: Optional[int]
    priority: int
    est_points: Optional[int]


class DevBacklogClient:
    """
    Client for DevBacklog API.
    Fetches stories ready for task decomposition.
    """

    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip('/')
        self._client = httpx.Client(timeout=30.0)

    def get_ready_stories(self) -> List[Story]:
        """Get stories ready for decomposition (status: ready_for_dev)."""
        response = self._client.get(
            f"{self.api_url}/stories",
            params={'status': 'ready_for_dev'}
        )
        response.raise_for_status()
        data = response.json()

        return [
            Story(
                id=story.get('id'),
                title=story.get('title'),
                narrative=story.get('narrative', ''),
                acceptance_criteria=story.get('acceptance_criteria', ''),
                epic_id=story.get('epic_id'),
                priority=story.get('priority', 0),
                est_points=story.get('est_points'),
            )
            for story in data.get('data', data)
        ]

    def get_story(self, story_id: int) -> Story:
        """Get a specific story by ID."""
        response = self._client.get(f"{self.api_url}/stories/{story_id}")
        response.raise_for_status()
        story = response.json()

        return Story(
            id=story.get('id'),
            title=story.get('title'),
            narrative=story.get('narrative', ''),
            acceptance_criteria=story.get('acceptance_criteria', ''),
            epic_id=story.get('epic_id'),
            priority=story.get('priority', 0),
            est_points=story.get('est_points'),
        )

    def mark_in_progress(self, story_id: int) -> bool:
        """Mark a story as in progress (being decomposed)."""
        try:
            response = self._client.post(
                f"{self.api_url}/stories/{story_id}/in-progress"
            )
            return response.status_code == 200
        except Exception:
            return False

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
