"""
Mason Daemon - Main orchestration loop.
Polls DevBacklog, compiles tasks, routes to providers, reports to QAQueue.
"""
import signal
import time
import sys
import structlog
from typing import Optional

from lib.config import Config
from lib.backlog_client import DevBacklogClient
from lib.qaqueue_client import QAQueueClient
from lib.provider_registry import ProviderRegistry
from lib.providers.base import ArtifactBundle
from lib.providers.goose import GooseProvider
from lib.providers.claude_cli import ClaudeCLIProvider
from lib.providers.ollama import OllamaProvider
from task_compiler import TaskCompiler
from provider_selector import ProviderSelector, SelectionContext

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()


class MasonDaemon:
    """
    Mason Daemon - Strategy layer over execution backends.
    
    Main loop:
    1. Poll DevBacklog for ready stories
    2. Compile stories into TaskPackets
    3. Check QAQueue for system load / retry queue
    4. Select best provider for each task
    5. Execute task via provider
    6. Report results to QAQueue
    7. Handle provider failures with failover
    """

    def __init__(self, config_path: Optional[str] = None):
        self.config = Config(config_path)
        self.devbacklog = DevBacklogClient(self.config.devbacklog_api_url)
        self.qaqueue = QAQueueClient(self.config.qaqueue_api_url)
        self.registry = ProviderRegistry(self.config)
        self.compiler = TaskCompiler(self.config)
        self.selector = ProviderSelector(self.config, self.registry, self.qaqueue)
        self._providers = {}
        self._running = False

        self._init_providers()
        self._setup_signals()

    def _init_providers(self) -> None:
        """Initialize provider adapters."""
        provider_classes = {
            'goose': GooseProvider,
            'claude_cli': ClaudeCLIProvider,
            'ollama': OllamaProvider,
        }

        for defn in self.registry.get_enabled_providers():
            adapter_name = defn.adapter
            if adapter_name in provider_classes:
                cls = provider_classes[adapter_name]
                self._providers[defn.name] = cls(defn.name, defn.config)
                logger.info("provider_initialized", provider=defn.name)

    def _setup_signals(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame) -> None:
        """Handle shutdown signal."""
        logger.info("shutdown_requested", signal=signum)
        self._running = False

    def run(self) -> None:
        """Main daemon loop."""
        self._running = True
        logger.info("mason_daemon_started")

        while self._running:
            try:
                self._process_cycle()
            except Exception as e:
                logger.error("cycle_error", error=str(e))

            # Wait before next poll
            for _ in range(self.config.poll_interval):
                if not self._running:
                    break
                time.sleep(1)

        self._cleanup()
        logger.info("mason_daemon_stopped")

    def _process_cycle(self) -> None:
        """Single processing cycle."""
        # 1. Process retry queue first
        self._process_retry_queue()

        # 2. Process new stories from DevBacklog
        self._process_new_stories()

    def _process_retry_queue(self) -> None:
        """Process tasks queued for retry."""
        try:
            retry_tasks = self.qaqueue.get_retry_queue()
        except Exception as e:
            logger.warning("retry_queue_fetch_failed", error=str(e))
            return

        for task in retry_tasks:
            if not self._running:
                break

            context = SelectionContext(
                task_id=task.task_id,
                attempt=task.attempt,
                max_attempts=task.max_attempts,
                providers_tried=task.providers_tried,
                last_failure_reason=task.last_failure_reason,
                is_retry=True,
            )

            self._execute_task(context, task_packet=None, is_retry=True)

    def _process_new_stories(self) -> None:
        """Process new stories from DevBacklog."""
        try:
            stories = self.devbacklog.get_ready_stories()
        except Exception as e:
            logger.warning("story_fetch_failed", error=str(e))
            return

        for story in stories:
            if not self._running:
                break

            logger.info("processing_story", story_id=story.id, title=story.title)

            # Mark story as in progress
            self.devbacklog.mark_in_progress(story.id)

            # Compile story into tasks
            task_packets = self.compiler.compile(story)

            for task_packet in task_packets:
                if not self._running:
                    break

                # Submit to QAQueue
                try:
                    self.qaqueue.submit_task(task_packet)
                except Exception as e:
                    logger.error(
                        "task_submit_failed",
                        task_id=task_packet['identity']['task_id'],
                        error=str(e)
                    )
                    continue

                # Execute immediately
                context = SelectionContext(
                    task_id=task_packet['identity']['task_id'],
                    attempt=0,
                    max_attempts=task_packet['execution']['max_attempts'],
                    providers_tried=[],
                    last_failure_reason=None,
                    is_retry=False,
                )

                self._execute_task(context, task_packet)

    def _execute_task(
        self,
        context: SelectionContext,
        task_packet: Optional[dict],
        is_retry: bool = False
    ) -> None:
        """Execute a task with provider failover."""
        while context.attempt < context.max_attempts:
            # Select provider
            provider_defn = self.selector.select(context)
            if not provider_defn:
                logger.error("no_provider_available", task_id=context.task_id)
                break

            provider = self._providers.get(provider_defn.name)
            if not provider:
                logger.error(
                    "provider_not_initialized",
                    provider=provider_defn.name
                )
                context.providers_tried.append(provider_defn.name)
                continue

            # Start run in QAQueue
            try:
                run_info = self.qaqueue.start_run(
                    context.task_id,
                    provider_defn.name,
                    provider_defn.confidence_weight
                )
                run_id = run_info.get('run_id')
            except Exception as e:
                logger.error("start_run_failed", error=str(e))
                break

            logger.info(
                "executing_task",
                task_id=context.task_id,
                provider=provider_defn.name,
                attempt=context.attempt,
            )

            # Execute with provider
            if task_packet:
                result = provider.generate(task_packet)
            else:
                # For retries, we'd need to fetch task packet from QAQueue
                # Simplified: skip if no packet
                logger.warning("no_task_packet_for_retry", task_id=context.task_id)
                break

            # Report result to QAQueue
            try:
                self.qaqueue.complete_run(
                    task_id=context.task_id,
                    run_id=run_id,
                    execution_status=result.execution_status,
                    files_modified=result.files_modified,
                    diff_summary=result.diff_summary,
                    logs=result.logs,
                    duration_ms=result.duration_ms,
                    artifacts_path=result.artifacts_path,
                )
            except Exception as e:
                logger.error("complete_run_failed", error=str(e))

            # Handle result
            if result.execution_status == 'success':
                self.selector.report_result(provider_defn.name, success=True)
                logger.info(
                    "task_succeeded",
                    task_id=context.task_id,
                    provider=provider_defn.name,
                )
                return

            elif result.execution_status == 'provider_failure':
                # Rate limit or provider issue - try next provider
                self.selector.report_result(
                    provider_defn.name,
                    success=False,
                    is_rate_limit=result.is_rate_limit
                )
                context.providers_tried.append(provider_defn.name)
                logger.warning(
                    "provider_failure_failover",
                    task_id=context.task_id,
                    provider=provider_defn.name,
                    is_rate_limit=result.is_rate_limit,
                )
                # Don't increment attempt - try next provider
                continue

            else:
                # Real execution failure - increment attempt
                self.selector.report_result(provider_defn.name, success=False)
                context.providers_tried.append(provider_defn.name)
                context.attempt += 1
                logger.warning(
                    "task_failed",
                    task_id=context.task_id,
                    provider=provider_defn.name,
                    attempt=context.attempt,
                )

        logger.error(
            "task_exhausted",
            task_id=context.task_id,
            attempts=context.attempt,
            providers_tried=context.providers_tried,
        )

    def _cleanup(self) -> None:
        """Cleanup resources."""
        self.devbacklog.close()
        self.qaqueue.close()


def main():
    """Main entry point."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    daemon = MasonDaemon(config_path)
    daemon.run()


if __name__ == '__main__':
    main()
