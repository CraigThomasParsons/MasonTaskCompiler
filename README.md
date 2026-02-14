# Mason: Task Compiler + Provider Orchestrator

Mason is **NOT an AI agent**. Mason is a **strategy layer** that:

1. Decomposes Stories into Tasks
2. Selects reasoning backends (providers)
3. Routes tasks to the best available provider
4. Handles provider failover (rate limits ≠ task failure)
5. Maintains system awareness for intelligent scheduling

## Architecture

```
DevBacklog (Stories)
    ↓
Mason (Planner + Framework Selector)
    ↓
Framework Provider (Codex | Claude | Gemini | Goose)
    ↓
Artifacts → QAQueue
    ↓
Vera (Judge)
    ↓
PASS → Piper (Confirm) → WritersRoom
FAIL → Mason (Retry with guidance)
```

## What Mason Does

### 1. Task Compilation

Mason reads a Story and produces **TaskPackets**:
- Atomic execution units
- Clear success criteria
- Explicit constraints
- Provider-agnostic

### 2. Provider Selection

Mason maintains a **Provider Registry** and selects based on:
- Provider availability (not rate-limited)
- Historical success rates for task type
- Retry context (try different provider on retry)
- Queue depth (load balance)

### 3. System Awareness

Mason queries QAQueue API for:
- Queue statistics (pending, running, failed)
- Provider performance (success rates, failures)
- Retry patterns (which providers failed on what)

This enables **intelligent routing**, not random dispatch.

## Key Principle: Two Failure Layers

| Type | Meaning | Action |
|------|---------|--------|
| **Execution Failure** | Task ran, produced wrong output | Increment attempt, send to Vera |
| **Provider Failure** | Rate limit, API error, timeout | DO NOT increment attempt, try next provider |

This is critical. Rate limit ≠ task failure.

## Directory Structure

```
Mason/
├── bin/
│   ├── mason_daemon.py          # Main service
│   ├── task_compiler.py         # Story → Tasks decomposition
│   ├── provider_selector.py     # Provider routing logic
│   └── lib/
│       ├── config.py
│       ├── backlog_client.py    # DevBacklog API client
│       ├── qaqueue_client.py    # QAQueue API client
│       ├── provider_registry.py # Provider definitions
│       └── providers/           # Provider adapters
│           ├── base.py
│           ├── goose.py
│           ├── claude_cli.py
│           ├── copilot_api.py
│           └── ollama.py
├── providers.json               # Provider registry config
├── config.yaml
├── requirements.txt
└── systemd/
    └── mason.service
```

## Provider Registry

```json
{
  "providers": [
    {
      "name": "copilot_codex",
      "priority": 1,
      "type": "api",
      "rate_limit_strategy": "detect_http_429",
      "confidence_weight": 1.0,
      "enabled": true
    },
    {
      "name": "claude_cli",
      "priority": 2,
      "type": "cli",
      "rate_limit_strategy": "error_pattern",
      "confidence_weight": 0.95,
      "enabled": true
    },
    {
      "name": "goose_ollama",
      "priority": 3,
      "type": "local",
      "rate_limit_strategy": "none",
      "confidence_weight": 0.85,
      "enabled": true
    }
  ]
}
```

## Provider Interface

Every provider must implement:

```python
class Provider(ABC):
    @abstractmethod
    def generate(self, task_packet: dict) -> ArtifactBundle:
        """Execute task and return artifacts."""
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider is currently available."""
        pass
    
    @abstractmethod
    def detect_rate_limit(self, error: Exception) -> bool:
        """Detect if error is a rate limit vs real failure."""
        pass
```

## Selection Algorithm

```python
def select_provider(task: TaskPacket, retry_context: RetryContext) -> Provider:
    providers = registry.get_enabled_providers()
    
    # Exclude providers that already failed on this task
    if retry_context:
        providers = [p for p in providers if p.name not in retry_context.failed_providers]
    
    # Sort by: availability, success rate, priority
    for provider in sorted(providers, key=priority_key):
        if provider.is_available():
            return provider
    
    # All providers exhausted
    raise AllProvidersExhausted()
```

## System Awareness Queries

Mason calls QAQueue API to make scheduling decisions:

```python
# Get queue depth
stats = qaqueue.get_stats()
if stats['queued'] > 100:
    # High load - prefer fast local providers
    prefer_local = True

# Get provider performance
provider_stats = qaqueue.get_provider_stats()
if provider_stats['goose']['success_rate'] < 0.5:
    # Goose struggling - boost priority of other providers
    adjust_priorities()

# Get retry context
retry_queue = qaqueue.get_retry_queue()
for task in retry_queue:
    # Route retries to different providers than previous attempts
    exclude_providers = task['providers_tried']
```

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
# Edit config.yaml with your settings
```

## Usage

```bash
# Run once (process one batch)
python bin/mason_daemon.py --once

# Run as daemon
python bin/mason_daemon.py

# With systemd
sudo systemctl start mason
```

## Configuration

See `config.yaml` for full options.

## License

Part of CCDF - Context Controlled Development Factory
