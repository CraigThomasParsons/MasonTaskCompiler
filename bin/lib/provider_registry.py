"""
Provider Registry for Mason.
Manages provider definitions, availability, and selection.
"""
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from .config import Config


@dataclass
class ProviderState:
    """Runtime state for a provider."""
    name: str
    available: bool = True
    rate_limited_until: Optional[datetime] = None
    consecutive_failures: int = 0
    last_success: Optional[datetime] = None
    last_failure: Optional[datetime] = None


@dataclass
class ProviderDefinition:
    """Provider definition from providers.json."""
    name: str
    priority: int
    type: str  # "api" | "cli" | "local"
    adapter: str
    rate_limit_strategy: str
    confidence_weight: float
    enabled: bool
    config: Dict[str, Any] = field(default_factory=dict)


class ProviderRegistry:
    """
    Manages provider definitions and runtime state.
    Handles availability tracking and selection.
    """

    def __init__(self, config: Config):
        self.config = config
        self._definitions: Dict[str, ProviderDefinition] = {}
        self._states: Dict[str, ProviderState] = {}
        self._load_providers()

    def _load_providers(self) -> None:
        """Load provider definitions from config."""
        for p in self.config.providers:
            defn = ProviderDefinition(
                name=p['name'],
                priority=p.get('priority', 99),
                type=p.get('type', 'cli'),
                adapter=p.get('adapter', p['name']),
                rate_limit_strategy=p.get('rate_limit_strategy', 'none'),
                confidence_weight=p.get('confidence_weight', 1.0),
                enabled=p.get('enabled', True),
                config=p.get('config', {}),
            )
            self._definitions[defn.name] = defn
            self._states[defn.name] = ProviderState(name=defn.name)

    def get_definition(self, name: str) -> Optional[ProviderDefinition]:
        """Get provider definition by name."""
        return self._definitions.get(name)

    def get_state(self, name: str) -> Optional[ProviderState]:
        """Get provider runtime state."""
        return self._states.get(name)

    def get_enabled_providers(self) -> List[ProviderDefinition]:
        """Get all enabled providers sorted by priority."""
        providers = [
            p for p in self._definitions.values()
            if p.enabled
        ]
        return sorted(providers, key=lambda p: p.priority)

    def get_available_providers(self) -> List[ProviderDefinition]:
        """Get providers that are currently available."""
        now = datetime.now()
        available = []

        for defn in self.get_enabled_providers():
            state = self._states[defn.name]

            # Check rate limit cooldown
            if state.rate_limited_until and state.rate_limited_until > now:
                continue

            # Check if explicitly marked unavailable
            if not state.available:
                continue

            available.append(defn)

        return available

    def mark_rate_limited(self, name: str, cooldown_seconds: int = None) -> None:
        """Mark a provider as rate limited."""
        if name not in self._states:
            return

        cooldown = cooldown_seconds or self.config.rate_limit_cooldown
        state = self._states[name]
        state.rate_limited_until = datetime.now() + timedelta(seconds=cooldown)
        state.consecutive_failures += 1

    def mark_success(self, name: str) -> None:
        """Mark a successful execution."""
        if name not in self._states:
            return

        state = self._states[name]
        state.last_success = datetime.now()
        state.consecutive_failures = 0
        state.rate_limited_until = None

    def mark_failure(self, name: str, is_rate_limit: bool = False) -> None:
        """Mark a failed execution."""
        if name not in self._states:
            return

        state = self._states[name]
        state.last_failure = datetime.now()
        state.consecutive_failures += 1

        if is_rate_limit:
            self.mark_rate_limited(name)

    def get_local_providers(self) -> List[ProviderDefinition]:
        """Get local providers (for high-load situations)."""
        return [
            p for p in self.get_available_providers()
            if p.type == 'local'
        ]

    def reset_cooldowns(self) -> None:
        """Reset all rate limit cooldowns."""
        for state in self._states.values():
            state.rate_limited_until = None
            state.consecutive_failures = 0
