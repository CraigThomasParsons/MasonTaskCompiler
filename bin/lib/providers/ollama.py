"""
Ollama direct provider adapter for Mason.
Executes tasks directly against local Ollama API.
"""
import json
import time
from pathlib import Path
from typing import Any, Dict

try:
    import ollama
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False

from .base import BaseProvider, ArtifactBundle


class OllamaProvider(BaseProvider):
    """
    Provider adapter for direct Ollama API access.
    No rate limits - fully local execution.
    """

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.model = config.get('model', 'qwen2.5-coder:14b')
        self.host = config.get('host', 'http://localhost:11434')
        self.timeout = config.get('timeout_seconds', 300)

    def generate(self, task_packet: Dict[str, Any]) -> ArtifactBundle:
        """Execute task using Ollama API."""
        if not HAS_OLLAMA:
            return ArtifactBundle(
                task_id=task_packet['identity']['task_id'],
                provider=self.name,
                execution_status='provider_failure',
                error='ollama package not installed',
            )

        task_id = task_packet['identity']['task_id']
        start_time = time.time()

        try:
            client = ollama.Client(host=self.host)
            prompt = self._build_prompt(task_packet)

            response = client.generate(
                model=self.model,
                prompt=prompt,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            return ArtifactBundle(
                task_id=task_id,
                provider=self.name,
                execution_status='success',
                logs=response.get('response', ''),
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ArtifactBundle(
                task_id=task_id,
                provider=self.name,
                execution_status='failure',
                error=str(e),
                duration_ms=duration_ms,
            )

    def is_available(self) -> bool:
        """Check if Ollama API is available."""
        if not HAS_OLLAMA:
            return False

        try:
            client = ollama.Client(host=self.host)
            models = client.list()
            return any(m.get('name', '').startswith(self.model.split(':')[0])
                      for m in models.get('models', []))
        except Exception:
            return False

    def detect_rate_limit(self, error: Exception) -> bool:
        """Ollama doesn't have rate limits."""
        return False

    def _build_prompt(self, task_packet: Dict[str, Any]) -> str:
        """Build Ollama prompt from task packet."""
        goal = task_packet.get('goal', {})
        constraints = task_packet.get('constraints', {})
        inputs = task_packet.get('inputs', {})

        prompt = f"""You are a senior software developer. Complete the following task:

# Task: {goal.get('title', 'Unknown')}

{goal.get('description', '')}

## Success Criteria
"""
        for criterion in goal.get('success_criteria', []):
            prompt += f"- {criterion}\n"

        if constraints.get('file_scope'):
            prompt += f"\n## File Scope\n"
            for f in constraints['file_scope']:
                prompt += f"- {f}\n"

        if constraints.get('style_rules'):
            prompt += f"\n## Style Rules\n"
            for rule in constraints['style_rules']:
                prompt += f"- {rule}\n"

        if inputs.get('retry_guidance'):
            prompt += f"\n## Previous Attempt Feedback\n"
            for guidance in inputs['retry_guidance']:
                prompt += f"- {guidance}\n"

        prompt += """
## Instructions
1. Write the complete code solution
2. Explain your approach briefly
3. List any assumptions made
"""
        return prompt
