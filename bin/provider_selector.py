"""
Provider Selector for Mason.
Implements provider selection strategy with system awareness.
"""
import structlog
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from lib.config import Config
from lib.provider_registry import ProviderRegistry, ProviderDefinition
from lib.qaqueue_client import QAQueueClient, ProviderStats

logger = structlog.get_logger()


@dataclass
class SelectionContext:
    """Context for provider selection decision."""
    task_id: str
    attempt: int
    max_attempts: int
    providers_tried: List[str]
    last_failure_reason: Optional[str]
    is_retry: bool


class ProviderSelector:
    """
    Selects the best provider for a task based on:
    - Provider availability and rate limit status
    - Historical success rates from QAQueue
    - Current system load
    - Retry context (avoid previously failed providers)
    - Provider confidence weights
    """

    def __init__(
        self,
        config: Config,
        registry: ProviderRegistry,
        qaqueue: QAQueueClient
    ):
        self.config = config
        self.registry = registry
        self.qaqueue = qaqueue
        self._cached_stats: Dict[str, ProviderStats] = {}
        self._stats_ttl = 60  # Cache stats for 60s

    def select(self, context: SelectionContext) -> Optional[ProviderDefinition]:
        """
        Select best provider for the task.
        
        Selection algorithm:
        1. Filter to available providers not yet tried
        2. If high load, prefer local providers
        3. Sort by score: priority * success_rate * confidence
        4. Return best provider
        """
        available = self.registry.get_available_providers()

        # Filter out providers already tried for this task
        candidates = [
            p for p in available
            if p.name not in context.providers_tried
        ]

        if not candidates:
            # All providers tried, allow retry of best performer
            logger.warning(
                "all_providers_exhausted",
                task_id=context.task_id,
                providers_tried=context.providers_tried
            )
            candidates = available

        if not candidates:
            logger.error("no_providers_available", task_id=context.task_id)
            return None

        # Check system load
        try:
            if self.qaqueue.is_high_load(self.config.high_load_threshold):
                local = self.registry.get_local_providers()
                if local:
                    logger.info(
                        "high_load_local_preferred",
                        task_id=context.task_id
                    )
                    candidates = [p for p in candidates if p in local] or candidates
        except Exception as e:
            logger.warning("load_check_failed", error=str(e))

        # Score and sort providers
        scored = self._score_providers(candidates)
        scored.sort(key=lambda x: x[1], reverse=True)

        selected = scored[0][0]
        logger.info(
            "provider_selected",
            task_id=context.task_id,
            provider=selected.name,
            score=scored[0][1],
            candidates=len(candidates),
        )

        return selected

    def _score_providers(
        self,
        providers: List[ProviderDefinition]
    ) -> List[tuple]:
        """Score providers based on priority, success rate, confidence."""
        self._refresh_stats()
        scored = []

        for p in providers:
            # Base score from priority (lower is better, invert)
            priority_score = 1.0 / p.priority

            # Success rate from historical data
            stats = self._cached_stats.get(p.name)
            if stats and stats.total_runs > 0:
                success_rate = stats.success_rate
            else:
                success_rate = 0.5  # Neutral for new providers

            # Confidence weight
            confidence = p.confidence_weight

            # Combined score
            score = priority_score * success_rate * confidence

            scored.append((p, score))

        return scored

    def _refresh_stats(self) -> None:
        """Refresh provider stats from QAQueue."""
        try:
            self._cached_stats = self.qaqueue.get_provider_stats()
        except Exception as e:
            logger.warning("stats_refresh_failed", error=str(e))

    def report_result(
        self,
        provider_name: str,
        success: bool,
        is_rate_limit: bool = False
    ) -> None:
        """Report execution result to update provider state."""
        if success:
            self.registry.mark_success(provider_name)
        else:
            self.registry.mark_failure(provider_name, is_rate_limit=is_rate_limit)
