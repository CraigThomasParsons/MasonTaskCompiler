"""
QAQueue API client for Mason.
Provides system awareness for intelligent scheduling.
"""
import httpx
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class QueueStats:
    """Queue statistics snapshot."""
    pending: int
    queued: int
    running: int
    awaiting_qa: int
    in_qa: int
    passed: int
    failed: int
    retry: int
    exhausted: int
    escalated: int
    total_active: int
    total_completed: int
    total_failed: int


@dataclass
class ProviderStats:
    """Per-provider performance statistics."""
    name: str
    total_runs: int
    successes: int
    failures: int
    provider_failures: int
    success_rate: float
    avg_duration_ms: int


@dataclass
class RetryTask:
    """Task queued for retry with context."""
    task_id: str
    title: str
    attempt: int
    max_attempts: int
    last_provider: Optional[str]
    last_failure_reason: Optional[str]
    providers_tried: List[str]


class QAQueueClient:
    """
    Client for QAQueue API.
    Provides system awareness for Mason's scheduling decisions.
    """

    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip('/')
        self._client = httpx.Client(timeout=30.0)

    def get_stats(self) -> QueueStats:
        """Get queue statistics."""
        response = self._client.get(f"{self.api_url}/queue/stats")
        response.raise_for_status()
        data = response.json()

        return QueueStats(
            pending=data.get('pending', 0),
            queued=data.get('queued', 0),
            running=data.get('running', 0),
            awaiting_qa=data.get('awaiting_qa', 0),
            in_qa=data.get('in_qa', 0),
            passed=data.get('passed', 0),
            failed=data.get('failed', 0),
            retry=data.get('retry', 0),
            exhausted=data.get('exhausted', 0),
            escalated=data.get('escalated', 0),
            total_active=data.get('total_active', 0),
            total_completed=data.get('total_completed', 0),
            total_failed=data.get('total_failed', 0),
        )

    def get_provider_stats(self) -> Dict[str, ProviderStats]:
        """Get per-provider performance statistics."""
        response = self._client.get(f"{self.api_url}/queue/provider-stats")
        response.raise_for_status()
        data = response.json()

        return {
            name: ProviderStats(
                name=name,
                total_runs=stats.get('total_runs', 0),
                successes=stats.get('successes', 0),
                failures=stats.get('failures', 0),
                provider_failures=stats.get('provider_failures', 0),
                success_rate=stats.get('success_rate', 0.0),
                avg_duration_ms=stats.get('avg_duration_ms', 0),
            )
            for name, stats in data.items()
        }

    def get_retry_queue(self) -> List[RetryTask]:
        """Get tasks queued for retry with failure context."""
        response = self._client.get(f"{self.api_url}/tasks/retry-queue")
        response.raise_for_status()
        data = response.json()

        return [
            RetryTask(
                task_id=task.get('task_id'),
                title=task.get('title'),
                attempt=task.get('attempt', 0),
                max_attempts=task.get('max_attempts', 3),
                last_provider=task.get('last_provider'),
                last_failure_reason=task.get('last_failure_reason'),
                providers_tried=task.get('providers_tried', []),
            )
            for task in data
        ]

    def submit_task(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Submit a new task to the queue."""
        response = self._client.post(
            f"{self.api_url}/tasks",
            json=task_data
        )
        response.raise_for_status()
        return response.json()

    def start_run(
        self,
        task_id: str,
        provider_name: str,
        confidence_weight: float = 1.0
    ) -> Dict[str, Any]:
        """Start an execution run for a task."""
        response = self._client.post(
            f"{self.api_url}/tasks/{task_id}/start-run",
            json={
                'provider_name': provider_name,
                'confidence_weight': confidence_weight,
            }
        )
        response.raise_for_status()
        return response.json()

    def complete_run(
        self,
        task_id: str,
        run_id: str,
        execution_status: str,
        files_modified: List[str] = None,
        diff_summary: str = None,
        logs: str = None,
        duration_ms: int = None,
        artifacts_path: str = None,
    ) -> Dict[str, Any]:
        """Complete an execution run with results."""
        response = self._client.post(
            f"{self.api_url}/tasks/{task_id}/complete-run",
            json={
                'run_id': run_id,
                'execution_status': execution_status,
                'files_modified': files_modified or [],
                'diff_summary': diff_summary,
                'logs': logs,
                'duration_ms': duration_ms,
                'artifacts_path': artifacts_path,
            }
        )
        response.raise_for_status()
        return response.json()

    def is_high_load(self, threshold: int = 50) -> bool:
        """Check if queue is under high load."""
        stats = self.get_stats()
        return stats.total_active > threshold

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
