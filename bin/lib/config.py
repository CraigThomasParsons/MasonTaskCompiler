"""
Configuration loader for Mason.
"""
import os
import json
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional


class Config:
    """Load and access Mason configuration."""

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            config_path = self._find_config()

        self._config = self._load_yaml(config_path)
        self._providers = self._load_providers()

    def _find_config(self) -> str:
        """Find config.yaml in standard locations."""
        locations = [
            Path(__file__).parent.parent.parent / "config.yaml",
            Path("/opt/mason/config.yaml"),
            Path.home() / ".mason" / "config.yaml",
        ]

        for loc in locations:
            if loc.exists():
                return str(loc)

        raise FileNotFoundError("config.yaml not found")

    def _load_yaml(self, path: str) -> Dict[str, Any]:
        """Load YAML config."""
        with open(path, 'r') as f:
            config = yaml.safe_load(f)
        return config.get('mason', {})

    def _load_providers(self) -> Dict[str, Any]:
        """Load providers.json."""
        locations = [
            Path(__file__).parent.parent.parent / "providers.json",
            Path("/opt/mason/providers.json"),
        ]

        for loc in locations:
            if loc.exists():
                with open(loc, 'r') as f:
                    return json.load(f)

        return {"providers": []}

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value by dot-notation key."""
        keys = key.split('.')
        value = self._config

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    @property
    def devbacklog_api_url(self) -> str:
        return self.get('devbacklog.api_url', 'http://localhost:8485/api')

    @property
    def qaqueue_api_url(self) -> str:
        return self.get('qaqueue.api_url', 'http://localhost:8008/api')

    @property
    def poll_interval(self) -> int:
        return self.get('devbacklog.poll_interval_seconds', 60)

    @property
    def max_tasks_per_story(self) -> int:
        return self.get('decomposition.max_tasks_per_story', 10)

    @property
    def default_max_attempts(self) -> int:
        return self.get('decomposition.default_max_attempts', 3)

    @property
    def selection_strategy(self) -> str:
        return self.get('provider_selection.strategy', 'smart')

    @property
    def rate_limit_cooldown(self) -> int:
        return self.get('provider_selection.rate_limit_cooldown', 300)

    @property
    def high_load_threshold(self) -> int:
        return self.get('provider_selection.high_load_threshold', 50)

    @property
    def artifacts_root(self) -> Path:
        return Path(self.get('artifacts.root', './artifacts'))

    @property
    def providers(self) -> List[Dict[str, Any]]:
        return self._providers.get('providers', [])

    @property
    def enabled_providers(self) -> List[Dict[str, Any]]:
        return [p for p in self.providers if p.get('enabled', True)]
