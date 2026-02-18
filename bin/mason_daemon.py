"""
Mason Daemon - Main orchestration loop.
Polls DevBacklog, compiles tasks, routes to providers, reports to QAQueue.
"""
import signal
import time
import sys
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import structlog
from typing import Dict, List, Optional

from lib.config import Config
from lib.backlog_client import DevBacklogClient
from lib.qaqueue_client import QAQueueClient
from lib.provider_registry import ProviderRegistry
from lib.providers.base import ArtifactBundle
from lib.providers.goose import GooseProvider
from lib.providers.claude_cli import ClaudeCLIProvider
from lib.providers.ollama import OllamaProvider
from lib.providers.codex_cli import CodexCLIProvider
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
        self._last_task_result: Dict[str, object] = {}

        self._init_providers()
        self._setup_signals()

    def _init_providers(self) -> None:
        """Initialize provider adapters."""
        provider_classes = {
            'codex_cli': CodexCLIProvider,
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
                did_work = self._process_cycle()
            except Exception as e:
                logger.error("cycle_error", error=str(e))
                did_work = False

            # Wait before next poll
            wait_seconds = 2 if did_work else self.config.poll_interval
            for _ in range(wait_seconds):
                if not self._running:
                    break
                time.sleep(1)

        self._cleanup()
        logger.info("mason_daemon_stopped")

    def _process_cycle(self) -> bool:
        """Single processing cycle."""
        run_state = self.devbacklog.get_mason_run_state()
        run_control = run_state.get('run_control', {}) if isinstance(run_state, dict) else {}
        if not bool(run_control.get('is_running', True)):
            self.devbacklog.post_mason_heartbeat(
                current_story_id=None,
                status_message='Idle: waiting for Start Sprint.',
                payload={'phase': 'paused'},
            )
            return False

        self.devbacklog.post_mason_heartbeat(
            current_story_id=run_control.get('current_story_id'),
            status_message='Mason cycle running.',
            payload={'phase': 'cycle_start'},
        )

        # 1. Process retry queue first
        self._process_retry_queue()

        # 2. Process new stories from DevBacklog
        return self._process_new_stories()

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

    def _process_new_stories(self) -> bool:
        """Process new stories from DevBacklog."""
        try:
            if self.config.prefer_current_sprint:
                stories = self.devbacklog.get_current_sprint_stories(
                    project_id=self.config.devbacklog_project_id
                )
                if not stories:
                    stories = self.devbacklog.get_ready_stories()
            else:
                stories = self.devbacklog.get_ready_stories()
        except Exception as e:
            logger.warning("story_fetch_failed", error=str(e))
            return False

        eligible_stories = [
            story for story in stories
            if story.status in ['ready', 'in_progress']
        ]
        if not eligible_stories:
            if stories:
                incomplete = [
                    story for story in stories
                    if story.status not in ['completed', 'done', 'passed', 'archived']
                ]
                if not incomplete:
                    logger.info("sprint_completed", sprint_id=(stories[0].sprint or {}).get('id'))
            return False

        selected_candidates = self._select_stories_to_process(eligible_stories)
        if not selected_candidates:
            return False

        self._rebalance_priorities(selected_candidates)
        story = selected_candidates[0]
        self._enforce_wip_limit(eligible_stories, story.id)

        logger.info("processing_story", story_id=story.id, title=story.title)
        self.devbacklog.post_mason_heartbeat(
            current_story_id=story.id,
            status_message=f'Processing story #{story.id}',
            payload={'phase': 'processing_story', 'story_id': story.id},
        )

        # Mark story as in progress
        self.devbacklog.mark_in_progress(story.id)
        self._prepare_story_context_files(story)

        # Compile story into tasks
        task_packets = self.compiler.compile(story)
        self._publish_story_tasks(story.id, task_packets)
        all_tasks_succeeded = True

        for task_packet in task_packets:
            if not self._running:
                break

            task_id = task_packet['identity']['task_id']
            self.devbacklog.update_story_task_state(
                story.id,
                task_id,
                state='in_progress',
            )

            # Submit to QAQueue
            try:
                self.qaqueue.submit_task(task_packet)
            except Exception as e:
                logger.error(
                    "task_submit_failed",
                    task_id=task_id,
                    error=str(e)
                )

            # Execute immediately
            context = SelectionContext(
                task_id=task_id,
                attempt=0,
                max_attempts=task_packet['execution']['max_attempts'],
                providers_tried=[],
                last_failure_reason=None,
                is_retry=False,
                complexity_hint=task_packet.get('provider_context', {}).get('complexity_hint'),
            )

            task_success = self._execute_task(context, task_packet, story=story)
            task_result = self._last_task_result or {}
            self.devbacklog.update_story_task_state(
                story.id,
                task_id,
                state='completed' if task_success else 'failed',
                last_provider=task_result.get('provider'),
                last_run_status=task_result.get('execution_status'),
                last_duration_ms=task_result.get('duration_ms'),
            )
            if not task_success:
                all_tasks_succeeded = False

        if all_tasks_succeeded and task_packets:
            self.devbacklog.mark_completed(story.id)
            self.devbacklog.post_mason_heartbeat(
                current_story_id=story.id,
                status_message=f'Completed story #{story.id}',
                payload={'phase': 'story_completed', 'story_id': story.id},
            )
        elif task_packets:
            self.devbacklog.post_mason_heartbeat(
                current_story_id=story.id,
                status_message=f'Story #{story.id} had task failures',
                payload={'phase': 'story_failed', 'story_id': story.id},
            )

        return True

    def _select_stories_to_process(self, stories: List) -> List:
        """
        Enforce project readiness gate and prioritize enablers.
        - If project is not ready, only enabler stories are processed.
        - Feature stories are blocked until framework bootstrap exists.
        """
        grouped: Dict[int, List] = {}
        for story in stories:
            project_id = int((story.project or {}).get('id') or 0)
            grouped.setdefault(project_id, []).append(story)

        selected: List = []
        for project_stories in grouped.values():
            project_stories.sort(key=lambda story: (0 if self._is_enabler_story(story) else 1, -int(story.priority or 0), story.id))

            readiness = self._project_readiness(project_stories[0])
            if readiness['ready']:
                selected.extend(project_stories)
                continue

            enablers = [story for story in project_stories if self._is_enabler_story(story)]
            if enablers:
                selected.extend(enablers)
                for blocked_story in project_stories:
                    if self._is_enabler_story(blocked_story):
                        continue
                    self._append_project_blocker(
                        blocked_story,
                        f"Blocked until enabler stories complete: {readiness['reason']}",
                    )
                continue

            for blocked_story in project_stories:
                self._append_project_blocker(
                    blocked_story,
                    f"Blocked: project not ready and no enabler stories available. {readiness['reason']}",
                )

        return sorted(selected, key=lambda story: (0 if self._is_enabler_story(story) else 1, -int(story.priority or 0), story.id))

    def _enforce_wip_limit(self, stories: List, selected_story_id: int) -> None:
        """
        Keep WIP at 1 by releasing any other in-progress stories.
        """
        for story in stories:
            if story.id == selected_story_id:
                continue
            if story.status == 'in_progress':
                released = self.devbacklog.release_story(story.id)
                if released:
                    logger.info("story_released_for_wip_limit", story_id=story.id)

    def _rebalance_priorities(self, stories: List) -> None:
        """
        Ensure actionable ordering matches what a developer would expect.
        Enablers must lead, then highest priority feature work.
        """
        ordered = sorted(
            stories,
            key=lambda story: (0 if self._is_enabler_story(story) else 1, -int(story.priority or 0), story.id)
        )
        base_priority = 1000
        for index, story in enumerate(ordered):
            target = base_priority - index
            current = int(story.priority or 0)
            if current == target:
                continue
            if self.devbacklog.update_priority(story.id, target):
                story.priority = target
                logger.info("story_priority_rebalanced", story_id=story.id, old=current, new=target)

    def _publish_story_tasks(self, story_id: int, task_packets: List[Dict]) -> None:
        """Publish Mason's decomposed tasks back to DevBacklog for visibility."""
        tasks_payload = []
        for index, packet in enumerate(task_packets):
            goal = packet.get('goal', {})
            execution = packet.get('execution', {})
            provider_context = packet.get('provider_context', {})
            metadata = packet.get('metadata', {})

            tasks_payload.append({
                'external_task_id': packet.get('identity', {}).get('task_id'),
                'title': goal.get('title') or f"Task {index + 1}",
                'description': goal.get('description') or '',
                'success_criteria': goal.get('success_criteria') or ['Task completed successfully'],
                'constraints': packet.get('constraints') or {},
                'inputs': packet.get('inputs') or {},
                'mode': 'modify_existing',
                'expected_outputs': [],
                'priority': int(metadata.get('priority') or 0),
                'sort_order': index,
                'max_attempts': int(execution.get('max_attempts') or 3),
                'state': 'queued',
                'last_provider': None,
                'last_run_status': provider_context.get('complexity_hint'),
            })

        try:
            self.devbacklog.submit_tasks(story_id=story_id, tasks=tasks_payload)
        except Exception as e:
            logger.warning("publish_story_tasks_failed", story_id=story_id, error=str(e))

    def _prepare_story_context_files(self, story) -> None:
        """
        Write context.md and goals.md in the target project folder when known.
        This keeps code context physically close to implementation work.
        """
        project = story.project or {}
        local_location = project.get('local_location') or project.get('code_folder')
        if not local_location:
            return

        project_path = Path(local_location)
        if not project_path.exists() or not project_path.is_dir():
            logger.warning(
                "project_path_missing",
                story_id=story.id,
                path=str(project_path),
            )
            return

        sprint = story.sprint or {}
        context_lines = [
            f"# Mason Context - Story {story.id}",
            "",
            f"- Story: {story.title}",
            f"- Epic ID: {story.epic_id}",
            f"- Project: {project.get('name')}",
            f"- Framework: {project.get('framework_description')}",
            f"- Languages: {project.get('languages')}",
            f"- GitHub: {project.get('github_repo')}",
            "",
            "## Narrative",
            story.narrative or "(none)",
            "",
            "## Acceptance Criteria",
            story.acceptance_criteria or "(none)",
            "",
        ]
        goals_lines = [
            "# Sprint Goals",
            "",
            f"- Sprint: {sprint.get('title') or '(unknown)'}",
            f"- Sprint Goal: {sprint.get('goal') or '(none)'}",
            f"- Story In Focus: {story.title}",
            "",
        ]

        context_path = project_path / "context.md"
        goals_path = project_path / "goals.md"
        context_path.write_text("\n".join(context_lines), encoding="utf-8")
        goals_path.write_text("\n".join(goals_lines), encoding="utf-8")

    def _is_enabler_story(self, story) -> bool:
        story_type = (story.story_type or '').strip().lower()
        if story_type == 'enabler':
            return True

        title = (story.title or '').strip().lower()
        return title.startswith('[enabler]') or title.startswith('enabler:')

    def _project_readiness(self, story) -> Dict[str, object]:
        project = story.project or {}
        framework = (project.get('framework_description') or '').lower()
        local_location = project.get('local_location') or project.get('code_folder')

        if not local_location:
            return {'ready': False, 'reason': 'Project has no local_location/code_folder.'}

        project_path = Path(local_location)
        if not project_path.exists() or not project_path.is_dir():
            return {'ready': False, 'reason': f'Project path does not exist: {project_path}'}

        if 'laravel' in framework:
            if not (project_path / 'composer.json').exists():
                return {'ready': False, 'reason': 'Laravel expected but composer.json is missing.'}
            if not (project_path / 'artisan').exists():
                return {'ready': False, 'reason': 'Laravel expected but artisan is missing.'}

        return {'ready': True, 'reason': 'Project readiness checks passed.'}

    def _append_project_blocker(self, story, reason: str) -> None:
        project = story.project or {}
        local_location = project.get('local_location') or project.get('code_folder')
        if not local_location:
            return

        project_path = Path(local_location)
        if not project_path.exists() or not project_path.is_dir():
            return

        docs_path = project_path / 'docs'
        docs_path.mkdir(parents=True, exist_ok=True)
        thoughts_path = docs_path / 'thoughts.md'

        lines = [
            f"## {datetime.now(timezone.utc).isoformat()} - Story {story.id} BLOCKED",
            f"- Reason: {reason}",
            f"- Story: {story.title}",
            "",
        ]
        with thoughts_path.open('a', encoding='utf-8') as handle:
            handle.write("\n".join(lines))

    def _execute_task(
        self,
        context: SelectionContext,
        task_packet: Optional[dict],
        is_retry: bool = False,
        story=None,
    ) -> bool:
        """Execute a task with provider failover."""
        self._last_task_result = {}

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
            run_id = None
            try:
                run_info = self.qaqueue.start_run(
                    context.task_id,
                    provider_defn.name,
                    provider_defn.confidence_weight
                )
                run_id = run_info.get('run_id')
            except Exception as e:
                logger.warning("start_run_failed", error=str(e))

            logger.info(
                "executing_task",
                task_id=context.task_id,
                provider=provider_defn.name,
                attempt=context.attempt,
            )
            self.devbacklog.post_mason_heartbeat(
                current_story_id=getattr(story, 'id', None) if story else None,
                status_message=f"Executing task via {provider_defn.name}",
                payload={
                    'phase': 'executing_task',
                    'task_id': context.task_id,
                    'provider': provider_defn.name,
                    'attempt': context.attempt,
                },
            )

            # Execute with provider
            if task_packet:
                try:
                    result = provider.generate(task_packet)
                except Exception as e:
                    logger.error("provider_generate_failed", provider=provider_defn.name, error=str(e))
                    result = ArtifactBundle(
                        task_id=context.task_id,
                        provider=provider_defn.name,
                        execution_status='failure',
                        error=f'Provider execution error: {e}',
                    )
            else:
                # For retries, we'd need to fetch task packet from QAQueue
                # Simplified: skip if no packet
                logger.warning("no_task_packet_for_retry", task_id=context.task_id)
                break

            test_result = self._run_story_tests(task_packet)
            if result.execution_status == 'success' and not test_result['success']:
                result.execution_status = 'failure'
                result.error = test_result['message']
                result.logs = (result.logs or '') + f"\n\n[tests]\n{test_result['message']}\n{test_result['output']}"

            self._last_task_result = {
                'provider': provider_defn.name,
                'execution_status': result.execution_status,
                'duration_ms': result.duration_ms,
            }

            if run_id:
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
                    logger.warning("complete_run_failed", error=str(e))

            self._append_thought_log(story, task_packet, provider_defn.name, result, test_result)

            # Handle result
            if result.execution_status == 'success':
                self.selector.report_result(provider_defn.name, success=True)
                logger.info(
                    "task_succeeded",
                    task_id=context.task_id,
                    provider=provider_defn.name,
                )
                return True

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
        return False

    def _cleanup(self) -> None:
        """Cleanup resources."""
        self.devbacklog.close()
        self.qaqueue.close()

    def _append_thought_log(self, story, task_packet: Optional[dict], provider_name: str, result: ArtifactBundle, test_result: Dict[str, object]) -> None:
        if not story:
            return

        project = story.project or {}
        local_location = project.get('local_location') or project.get('code_folder')
        if not local_location:
            return

        project_path = Path(local_location)
        if not project_path.exists() or not project_path.is_dir():
            return

        docs_path = project_path / 'docs'
        docs_path.mkdir(parents=True, exist_ok=True)
        thoughts_path = docs_path / 'thoughts.md'

        task_title = task_packet.get('goal', {}).get('title') if task_packet else f"Task {result.task_id}"
        lines = [
            f"## {datetime.now(timezone.utc).isoformat()} - Story {story.id} - {task_title}",
            f"- Provider: {provider_name}",
            f"- Result: {result.execution_status}",
            f"- Files modified: {', '.join(result.files_modified) if result.files_modified else '(none detected)'}",
            f"- Error: {result.error or '(none)'}",
            f"- Test result: {test_result['message']}",
            "",
        ]
        with thoughts_path.open('a', encoding='utf-8') as handle:
            handle.write("\n".join(lines))

    def _run_story_tests(self, task_packet: Optional[dict]) -> Dict[str, object]:
        if not task_packet:
            return {'success': True, 'message': 'No task packet provided.', 'output': ''}

        project = task_packet.get('inputs', {}).get('project_context', {}) or {}
        local_location = project.get('local_location') or project.get('code_folder')
        if not local_location:
            return {'success': True, 'message': 'No project path; skipped tests.', 'output': ''}

        project_path = Path(local_location)
        if not project_path.exists():
            return {'success': False, 'message': f'Project path missing: {project_path}', 'output': ''}

        command: Optional[List[str]] = None
        if (project_path / 'artisan').exists():
            command = ['php', 'artisan', 'test', '--stop-on-failure']
        elif (project_path / 'package.json').exists():
            command = ['npm', 'test', '--', '--runInBand']

        if command is None:
            return {'success': True, 'message': 'No known test command; skipped tests.', 'output': ''}

        try:
            result = subprocess.run(
                command,
                cwd=str(project_path),
                capture_output=True,
                text=True,
                timeout=600,
            )
            output = (result.stdout or '') + "\n" + (result.stderr or '')
            if result.returncode == 0:
                return {'success': True, 'message': f'Tests passed: {" ".join(command)}', 'output': output}
            return {'success': False, 'message': f'Tests failed ({result.returncode}): {" ".join(command)}', 'output': output}
        except Exception as e:
            return {'success': False, 'message': f'Test execution error: {e}', 'output': ''}


def main():
    """Main entry point."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    daemon = MasonDaemon(config_path)
    daemon.run()


if __name__ == '__main__':
    main()
