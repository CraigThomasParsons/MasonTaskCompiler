"""
Base provider interface for Mason.
All provider adapters must implement this interface.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ArtifactBundle:
    """
    Result of a provider execution.
    This is what providers return to Mason.
    """
    task_id: str
    provider: str
    execution_status: str  # "success" | "failure" | "provider_failure"
    files_modified: List[str] = field(default_factory=list)
    diff_summary: Optional[str] = None
    logs: Optional[str] = None
    duration_ms: Optional[int] = None
    artifacts_path: Optional[str] = None
    error: Optional[str] = None
    is_rate_limit: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            'task_id': self.task_id,
            'provider': self.provider,
            'execution_status': self.execution_status,
            'files_modified': self.files_modified,
            'diff_summary': self.diff_summary,
            'logs': self.logs,
            'duration_ms': self.duration_ms,
            'artifacts_path': self.artifacts_path,
            'error': self.error,
        }


class BaseProvider(ABC):
    """
    Abstract base class for provider adapters.
    
    Every provider must implement:
    - generate(): Execute task and return artifacts
    - is_available(): Check if provider is currently available
    - detect_rate_limit(): Detect if error is rate limit vs real failure
    """

    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config

    @abstractmethod
    def generate(self, task_packet: Dict[str, Any]) -> ArtifactBundle:
        """
        Execute task and return artifacts.
        
        Args:
            task_packet: TaskPacket v1 JSON structure
            
        Returns:
            ArtifactBundle with execution results
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if provider is currently available.
        
        Returns:
            True if provider can accept tasks
        """
        pass

    @abstractmethod
    def detect_rate_limit(self, error: Exception) -> bool:
        """
        Detect if error is a rate limit vs real failure.
        
        Args:
            error: Exception from execution
            
        Returns:
            True if error indicates rate limiting
        """
        pass

    def get_confidence_weight(self) -> float:
        """Get provider's confidence weight for Vera."""
        return self.config.get('confidence_weight', 1.0)

    def get_timeout(self) -> int:
        """Get execution timeout in seconds."""
        return self.config.get('timeout_seconds', 300)
