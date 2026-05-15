"""Command and external integration delegates for the Textual app."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from invincat_cli.widgets.schedule_manager import ScheduleAction


class AppCommandIntegrationMixin:
    """Slash-command, scheduler, WeCom, and skill command hooks."""

    async def _handle_shell_command(self, command: str) -> None:
        """Handle a shell command (`!` prefix)."""
        from invincat_cli.app_runtime.shell_handlers import handle_shell_command

        await handle_shell_command(self, command)

    async def _run_interactive_shell_task(self, command: str) -> None:
        """Run an interactive shell command using Textual suspend."""
        from invincat_cli.app_runtime.shell_handlers import run_interactive_shell_task

        await run_interactive_shell_task(self, command)

    async def _run_shell_task(self, command: str) -> None:
        """Run a shell command in a background worker."""
        from invincat_cli.app_runtime.shell_handlers import run_shell_task

        await run_shell_task(self, command)

    async def _cleanup_shell_task(self) -> None:
        """Clean up after shell command task completes or is cancelled."""
        from invincat_cli.app_runtime.shell_handlers import cleanup_shell_task

        await cleanup_shell_task(self)

    async def _kill_shell_process(self) -> None:
        """Terminate the running shell command process."""
        from invincat_cli.app_runtime.shell_handlers import kill_shell_process

        await kill_shell_process(self)

    async def _open_url_command(self, command: str, cmd: str) -> None:
        """Open a configured URL command in the browser."""
        from invincat_cli.app_runtime.command_handlers import handle_url_command

        await handle_url_command(self, command, cmd)

    async def _handle_trace_command(self, command: str) -> None:
        """Open the current thread in LangSmith."""
        from invincat_cli.app_runtime.command_handlers import handle_trace_command

        await handle_trace_command(self, command)

    async def _handle_command(self, command: str) -> None:
        """Handle a slash command."""
        from invincat_cli.app_runtime.command_handlers import handle_app_command

        await handle_app_command(self, command)

    def _start_scheduler(self) -> None:
        """Create SchedulerRunner and start the tick interval."""
        from invincat_cli.app_runtime.scheduled_delivery import start_scheduler

        start_scheduler(self)

    async def _scheduler_tick(self) -> None:
        from invincat_cli.app_runtime.scheduled_delivery import scheduler_tick

        await scheduler_tick(self)

    async def _handle_scheduled_timeout(self, run_id: str, task_id: str) -> None:
        from invincat_cli.app_runtime.scheduled_delivery import (
            handle_scheduled_timeout,
        )

        await handle_scheduled_timeout(self, run_id, task_id)

    def _cancel_timed_out_scheduled_turn(self, run_id: str, task_id: str) -> None:
        """Cancel or dequeue a scheduled turn after SchedulerRunner timeout."""
        from invincat_cli.app_runtime.scheduled_delivery import (
            cancel_timed_out_scheduled_turn,
        )

        cancel_timed_out_scheduled_turn(self, run_id, task_id)

    async def _handle_schedule_tool_payload(self, payload: dict) -> None:
        """Handle a structured schedule tool payload from the agent."""
        from invincat_cli.app_runtime.schedule_handlers import (
            handle_schedule_tool_payload,
        )

        await handle_schedule_tool_payload(self, payload)

    async def _handle_schedule_command(self, command: str) -> None:
        """Open the schedule manager modal screen."""
        await self._show_schedule_manager()

    async def _show_schedule_manager(self) -> None:
        """Push the ScheduleManagerScreen modal."""
        from invincat_cli.app_runtime.schedule_handlers import show_schedule_manager

        await show_schedule_manager(self)

    async def _execute_schedule_action(self, action: ScheduleAction) -> None:
        """Execute a schedule action returned by the manager modal."""
        from invincat_cli.app_runtime.schedule_handlers import execute_schedule_action

        await execute_schedule_action(self, action)

    async def _handle_wecombot_command(self, command: str, *, action: str) -> None:
        """Manage WeCom bridge lifecycle in current CLI session."""
        from invincat_cli.app_runtime.wecom_handlers import handle_wecombot_command

        await handle_wecombot_command(self, command, action=action)

    async def _run_wecombot_bridge(self) -> None:
        """Run WeCom long-connection client and bridge to current session."""
        from invincat_cli.app_runtime.wecom_handlers import run_wecombot_bridge

        await run_wecombot_bridge(self)

    async def _wecom_handle_inbound_message(
        self,
        *,
        frame: dict[str, Any],
    ) -> None:
        """Process one inbound WeCom message and deliver a streaming reply."""
        from invincat_cli.app_runtime.wecom_handlers import (
            wecom_handle_inbound_message,
        )

        await wecom_handle_inbound_message(self, frame=frame)

    def _wecom_enqueue(self, payload: dict[str, Any]) -> None:
        from invincat_cli.app_runtime.wecom_handlers import wecom_enqueue

        wecom_enqueue(self, payload)

    async def _wecom_flush_outbox(self) -> bool:
        """Flush pending outbound replies using the current live WS connection."""
        from invincat_cli.app_runtime.wecom_handlers import wecom_flush_outbox

        return await wecom_flush_outbox(self)

    async def _wecom_send_request(
        self,
        payload: dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Send a WeCom request frame and wait for its matching response."""
        from invincat_cli.app_runtime.wecom_handlers import wecom_send_request

        return await wecom_send_request(self, payload, timeout=timeout)

    async def _handle_skill_command(self, command: str) -> None:
        """Handle a `/skill:<name>` command."""
        from invincat_cli.app_runtime.skill_handlers import handle_skill_command

        await handle_skill_command(self, command)
