"""Agent turn, message flow, memory, and exit delegates for the Textual app."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from invincat_cli.app_runtime.terminal import restore_cursor_guide
from invincat_cli.i18n import t

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langgraph.pregel import Pregel
    from textual.widgets import Static
    from textual.worker import Worker

    from invincat_cli.app_runtime.agent import AgentTurnRequest
    from invincat_cli.app_runtime.state import DeferredAction
    from invincat_cli.scheduler.runner import SchedulerRunner
    from invincat_cli.widgets.message_store import MessageStore
    from invincat_cli.widgets.messages import (
        AssistantMessage,
        SkillMessage,
        ToolCallMessage,
    )


class AppTurnFlowMixin:
    """Turn execution, memory/offload, queue, and message lifecycle hooks."""

    if TYPE_CHECKING:
        _active_turn_is_planner: bool
        _agent_running: bool
        _agent_worker: Worker[None] | None
        _message_store: MessageStore
        _quit_pending: bool
        _scheduler_runner: SchedulerRunner | None
        _shell_running: bool

        def notify(
            self,
            message: object,
            *,
            severity: str = "information",
            timeout: float | None = None,
            markup: bool = True,
        ) -> None: ...

        def set_timer(self, delay: float, callback: object) -> object: ...

    async def _get_conversation_token_count(self) -> int | None:
        """Return the approximate conversation-only token count."""
        from invincat_cli.app_runtime.memory_handlers import (
            get_conversation_token_count,
        )

        return await get_conversation_token_count(self)

    async def _maybe_auto_offload(self) -> None:
        """Trigger offload automatically when the context window is nearly full."""
        from invincat_cli.app_runtime.memory_handlers import maybe_auto_offload

        await maybe_auto_offload(self)

    async def _maybe_notify_memory_update(self) -> None:
        """Show a status bar notification when memory files were updated."""
        from invincat_cli.app_runtime.memory_handlers import (
            maybe_notify_memory_update,
        )

        await maybe_notify_memory_update(self)

    def _on_memory_update_done(self, msg: str) -> None:
        """Transition from in-progress to success memory status."""
        from invincat_cli.app_runtime.memory_handlers import on_memory_update_done

        on_memory_update_done(self, msg)

    def _clear_memory_status(self) -> None:
        """Clear the memory-update status bar message."""
        from invincat_cli.app_runtime.memory_handlers import clear_memory_status

        clear_memory_status(self)

    def _resolve_offload_budget_str(self) -> str | None:
        """Resolve the offload retention budget as a human-readable string."""
        from invincat_cli.app_runtime.memory_handlers import (
            resolve_offload_budget_str,
        )

        return resolve_offload_budget_str(self)

    async def _handle_offload(self) -> None:
        """Offload older messages to free context window space."""
        from invincat_cli.app_runtime.memory_handlers import handle_offload

        await handle_offload(self)

    async def _send_to_agent(
        self,
        message: str,
        *,
        message_kwargs: dict[str, Any] | None = None,
        agent_override: Pregel | None = None,
        thread_id_override: str | None = None,
        post_turn_hook: Callable[[], Awaitable[None]] | None = None,
        on_text_delta: Callable[[str, str], Awaitable[None]] | None = None,
        on_wecom_file_request: Callable[[dict[str, Any]], Awaitable[None]]
        | None = None,
    ) -> bool:
        """Send a message to the agent and start execution."""
        from invincat_cli.app_runtime.agent_handlers import send_to_agent

        return await send_to_agent(
            self,
            message,
            message_kwargs=message_kwargs,
            agent_override=agent_override,
            thread_id_override=thread_id_override,
            post_turn_hook=post_turn_hook,
            on_text_delta=on_text_delta,
            on_wecom_file_request=on_wecom_file_request,
        )

    def _finish_active_scheduled_run_as_failed(self, error: str) -> None:
        """Finish the active scheduled run as failed, if one is active."""
        from invincat_cli.app_runtime.agent_handlers import (
            finish_active_scheduled_run_as_failed,
        )

        finish_active_scheduled_run_as_failed(self, error)

    async def _run_agent_task(self, request: AgentTurnRequest) -> None:
        """Run the agent task in a background worker."""
        from invincat_cli.app_runtime.agent_handlers import run_agent_task

        await run_agent_task(self, request)

    async def _handle_agent_task_exception(self, exc: BaseException) -> bool:
        """Handle a failed agent turn and return whether it should retry."""
        from invincat_cli.app_runtime.agent_handlers import (
            handle_agent_task_exception,
        )

        return await handle_agent_task_exception(self, exc)

    def _agent_error_detail_with_server_log(self, exc: BaseException) -> str:
        """Build agent error detail, including server log tail when useful."""
        from invincat_cli.app_runtime.agent_handlers import (
            agent_error_detail_with_server_log,
        )

        return agent_error_detail_with_server_log(self, exc)

    async def _process_next_from_queue(self) -> None:
        """Process the next message from the queue if any exist."""
        from invincat_cli.app_runtime.queue_handlers import process_next_from_queue

        await process_next_from_queue(self)

    async def _cleanup_agent_task(self, *, generation: int = 0) -> None:
        """Clean up after agent task completes or is cancelled."""
        from invincat_cli.app_runtime.agent_handlers import cleanup_agent_task

        await cleanup_agent_task(self, generation=generation)

    def _handle_stale_agent_cleanup(self, *, generation: int) -> None:
        """Handle cleanup for an older worker generation."""
        from invincat_cli.app_runtime.agent_handlers import handle_stale_agent_cleanup

        handle_stale_agent_cleanup(self, generation=generation)

    async def _run_post_agent_cleanup_side_effects(self) -> None:
        """Run cleanup side effects after deferred actions have settled."""
        from invincat_cli.app_runtime.agent_handlers import (
            run_post_agent_cleanup_side_effects,
        )

        await run_post_agent_cleanup_side_effects(self)

    async def _drain_scheduler_if_idle(self) -> None:
        """Drain scheduler fire-now queue when no foreground task is running."""
        if self._scheduler_runner is None or self._agent_running or self._shell_running:
            return
        await self._scheduler_runner.drain_pending_now()

    async def _mount_message(
        self, widget: Static | AssistantMessage | ToolCallMessage | SkillMessage
    ) -> None:
        """Mount a message widget to the messages area."""
        from invincat_cli.app_runtime.message_flow import mount_message

        await mount_message(self, widget)

    def _set_active_message(self, message_id: str | None) -> None:
        """Set the active streaming message."""
        self._message_store.set_active_message(message_id)

    def _sync_message_content(self, message_id: str, content: str) -> None:
        """Sync final message content back to the store after streaming."""
        self._message_store.update_message(
            message_id,
            content=content,
            is_streaming=False,
        )

    async def _clear_messages(self) -> None:
        """Clear the messages area and message store."""
        from invincat_cli.app_runtime.message_flow import clear_messages

        await clear_messages(self)

    def _pop_last_queued_message(self) -> None:
        """Remove the most recently queued message."""
        from invincat_cli.app_runtime.queue_handlers import pop_last_queued_message

        pop_last_queued_message(self)

    def _discard_queue(self) -> None:
        """Clear pending messages, deferred actions, and queued widgets."""
        from invincat_cli.app_runtime.queue_handlers import discard_queue

        discard_queue(self)

    def _defer_action(self, action: DeferredAction) -> None:
        """Queue a deferred action, replacing any existing action of the same kind."""
        from invincat_cli.app_runtime.deferred_handlers import defer_action

        defer_action(self, action)

    async def _maybe_drain_deferred(self) -> None:
        """Drain deferred actions unless a server connection is still in progress."""
        from invincat_cli.app_runtime.deferred_handlers import maybe_drain_deferred

        await maybe_drain_deferred(self)

    async def _drain_deferred_actions(self) -> None:
        """Execute deferred actions queued while busy."""
        from invincat_cli.app_runtime.deferred_handlers import drain_deferred_actions

        await drain_deferred_actions(self)

    def _cancel_worker(self, worker: Worker[None] | None) -> None:
        """Discard the message queue and cancel an active worker."""
        self._discard_queue()
        if worker is not None:
            worker.cancel()
        self._agent_running = False
        self._agent_worker = None
        self._active_turn_is_planner = False

    def action_quit_or_interrupt(self) -> None:
        """Handle Ctrl+C - interrupt active work or arm quit."""
        from invincat_cli.app_runtime.action_handlers import quit_or_interrupt

        quit_or_interrupt(self)

    def _arm_quit_pending(self, shortcut: str) -> None:
        """Set the pending-quit flag and show a matching hint."""
        self._quit_pending = True
        quit_timeout = 3
        self.notify(
            t("app.press_to_quit", shortcut=shortcut),
            timeout=quit_timeout,
            markup=False,
        )
        self.set_timer(quit_timeout, lambda: setattr(self, "_quit_pending", False))

    def action_interrupt(self) -> None:
        """Handle escape key."""
        from invincat_cli.app_runtime.action_handlers import interrupt

        interrupt(self)

    def action_quit_app(self) -> None:
        """Handle quit action (Ctrl+D)."""
        from invincat_cli.app_runtime.action_handlers import quit_app

        quit_app(self)

    def exit(
        self,
        result: Any = None,
        return_code: int = 0,
        message: Any = None,
    ) -> None:
        """Exit the app after preparing runtime cleanup."""
        from invincat_cli.app_runtime.exit_handlers import prepare_exit

        prepare_exit(
            self,
            restore_cursor_guide=restore_cursor_guide,
        )
        cast(Any, super()).exit(result=result, return_code=return_code, message=message)
