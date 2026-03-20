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
    status: Optional[str]
    story_type: Optional[str]
    priority: int
    est_points: Optional[int]
    project: Optional[Dict[str, Any]] = None
    sprint: Optional[Dict[str, Any]] = None


class DevBacklogClient:
    """
    Client for DevBacklog API.
    Fetches stories ready for task decomposition.
    """

    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip('/')
        self._client = httpx.Client(timeout=30.0)

    def get_ready_stories(self) -> List[Story]:
        """Get stories ready for decomposition (status: ready)."""
        response = self._client.get(
            f"{self.api_url}/stories",
            params={'status': 'ready'}
        )
        response.raise_for_status()
        data = response.json()

        return self._parse_story_list(data)

    def get_current_sprint_stories(self, project_id: Optional[int] = None) -> List[Story]:
        """Get stories from current sprint board order, optionally filtered by project."""
        params: Dict[str, Any] = {'scope': 'current_sprint'}
        if project_id is not None:
            params['project_id'] = project_id

        response = self._client.get(
            f"{self.api_url}/stories",
            params=params,
        )
        response.raise_for_status()
        data = response.json()
        sprint = data.get('sprint')

        stories = self._parse_story_list(data)
        for story in stories:
            story.sprint = sprint
        return stories

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
            status=story.get('status'),
            story_type=story.get('story_type'),
            priority=story.get('priority', 0),
            est_points=story.get('est_points'),
            project=story.get('project'),
        )

    def mark_in_progress(self, story_id: int) -> bool:
        """Mark a story as in progress (being decomposed)."""
        try:
            response = self._client.post(
                f"{self.api_url}/stories/{story_id}/claim"
            )
            return response.status_code == 200
        except Exception:
            return False

    def mark_completed(self, story_id: int) -> bool:
        """Mark story completed after implementation tasks pass."""
        try:
            response = self._client.post(
                f"{self.api_url}/stories/{story_id}/complete"
            )
            return response.status_code == 200
        except Exception:
            return False

    def release_story(self, story_id: int) -> bool:
        """Release an in-progress story back to ready."""
        try:
            response = self._client.post(
                f"{self.api_url}/stories/{story_id}/release"
            )
            return response.status_code == 200
        except Exception:
            return False

    def update_priority(self, story_id: int, priority: int) -> bool:
        """Update story priority for sprint ordering correction."""
        try:
            response = self._client.post(
                f"{self.api_url}/stories/{story_id}/priority",
                json={'priority': int(priority)},
            )
            return response.status_code == 200
        except Exception:
            return False

    def submit_tasks(
        self,
        story_id: int,
        tasks: List[Dict[str, Any]],
        agent: str = "mason"
    ) -> Dict[str, Any]:
        """Persist and submit compiled tasks through DevBacklog."""
        response = self._client.post(
            f"{self.api_url}/stories/{story_id}/tasks",
            json={
                "agent": agent,
                "tasks": tasks,
            },
        )
        response.raise_for_status()
        return response.json()

    def update_story_task_state(
        self,
        story_id: int,
        external_task_id: str,
        state: str,
        last_provider: Optional[str] = None,
        last_run_status: Optional[str] = None,
        last_duration_ms: Optional[int] = None,
    ) -> bool:
        """Update a single story task state in DevBacklog."""
        payload: Dict[str, Any] = {'state': state}
        if last_provider is not None:
            payload['last_provider'] = last_provider
        if last_run_status is not None:
            payload['last_run_status'] = last_run_status
        if last_duration_ms is not None:
            payload['last_duration_ms'] = int(last_duration_ms)

        try:
            response = self._client.post(
                f"{self.api_url}/stories/{story_id}/tasks/{external_task_id}/state",
                json=payload,
            )
            return response.status_code == 200
        except Exception:
            return False

    def get_mason_run_state(self) -> Dict[str, Any]:
        """Fetch Mason run state snapshot from DevBacklog."""
        try:
            response = self._client.get(f"{self.api_url}/mason/run-state")
            response.raise_for_status()
            payload = response.json()
            return payload.get('state', {}) if isinstance(payload, dict) else {}
        except Exception:
            # Backward-compatible default: allow processing if endpoint is unavailable.
            return {'run_control': {'is_running': True, 'heartbeat_fresh': False}}

    def post_mason_heartbeat(
        self,
        current_story_id: Optional[int] = None,
        status_message: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Post Mason heartbeat to DevBacklog for run-state visibility."""
        try:
            response = self._client.post(
                f"{self.api_url}/mason/run-state/heartbeat",
                json={
                    'current_story_id': current_story_id,
                    'status_message': status_message,
                    'payload': payload or {},
                },
            )
            return response.status_code == 200
        except Exception:
            return False

    def _parse_story_list(self, payload: Dict[str, Any]) -> List[Story]:
        raw_stories = payload.get('stories', payload.get('data', payload))
        if not isinstance(raw_stories, list):
            return []

        return [
            Story(
                id=story.get('id'),
                title=story.get('title'),
                narrative=story.get('narrative', ''),
                acceptance_criteria=story.get('acceptance_criteria', ''),
                epic_id=story.get('epic_id'),
                status=story.get('status'),
                story_type=story.get('story_type'),
                priority=story.get('priority', 0),
                est_points=story.get('est_points'),
                project=story.get('project'),
            )
            for story in raw_stories
        ]

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
