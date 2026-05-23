"""Textual UI adapter state and callbacks for agent execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from deepagents.backends import DEFAULT_EXECUTE_TIMEOUT

from invincat_cli.core.session_stats import SpinnerStatus
from invincat_cli.i18n import t
from invincat_cli.textual_adapter.subagent_activity import SubagentActivityTracker
from invincat_cli.widgets.messages import ToolCallMessage

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from invincat_cli.core.ask_user_types import AskUserWidgetResult, Question

    class _TokensUpdateCallback(Protocol):
        def __call__(self, count: int, *, approximate: bool = False) -> None: ...

    class _TokensShowCallback(Protocol):
        def __call__(self, *, approximate: bool = False) -> None: ...


_EXECUTE_WATCHDOG_GRACE_SECONDS = 5
_MAX_EXECUTE_WATCHDOG_SECONDS = 3600


def _coerce_execute_timeout(value: object) -> int | None:
    if value is None:
        return DEFAULT_EXECUTE_TIMEOUT
    if type(value) is int:
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return DEFAULT_EXECUTE_TIMEOUT
        try:
            return int(stripped)
        except ValueError:
            return DEFAULT_EXECUTE_TIMEOUT
    return DEFAULT_EXECUTE_TIMEOUT


def _execute_watchdog_delay(args: dict[str, Any]) -> int | None:
    timeout = _coerce_execute_timeout(args.get("timeout"))
    if timeout is None or timeout <= 0:
        return None
    return min(timeout, _MAX_EXECUTE_WATCHDOG_SECONDS) + _EXECUTE_WATCHDOG_GRACE_SECONDS


class TextualUIAdapter:
    """Adapter for rendering agent output to Textual widgets.

    This adapter provides an abstraction layer between the agent execution and the
    Textual UI, allowing streaming output to be rendered as widgets.
    """

    def __init__(
        self,
        mount_message: Callable[..., Awaitable[None]],
        update_status: Callable[[str], None],
        request_approval: Callable[..., Awaitable[Any]],
        on_auto_approve_enabled: Callable[[], None] | None = None,
        set_spinner: Callable[[SpinnerStatus], Awaitable[None]] | None = None,
        set_active_message: Callable[[str | None], None] | None = None,
        sync_message_content: Callable[[str, str], None] | None = None,
        request_ask_user: (
            Callable[
                [list[Question]],
                Awaitable[asyncio.Future[AskUserWidgetResult] | None],
            ]
            | None
        ) = None,
        request_approve_plan: (
            Callable[
                [list[dict[str, Any]]],
                Awaitable[asyncio.Future[dict[str, Any]] | None],
            ]
            | None
        ) = None,
        on_execute_watchdog_timeout: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Initialize the adapter."""
        self._mount_message = mount_message
        """Async callback to mount a message widget to the chat."""

        self._update_status = update_status
        """Callback to update the status bar text."""

        self._request_approval = request_approval
        """Async callback that returns a Future for HITL approval."""

        self._on_auto_approve_enabled = on_auto_approve_enabled
        """Callback invoked when auto-approve is enabled via the HITL approval
        menu.

        Fired when the user selects "Auto-approve all" from an approval dialog,
        allowing the app to sync its status bar and session state.
        """

        self._set_spinner = set_spinner
        """Callback to show/hide loading spinner."""

        self._set_active_message = set_active_message
        """Callback to set the active streaming message ID (pass `None` to clear)."""

        self._sync_message_content = sync_message_content
        """Callback to sync final message content back to the store after streaming."""

        self._request_ask_user = request_ask_user
        """Async callback for `ask_user` interrupts.

        When awaited, returns a `Future` that resolves to user answers.
        """

        self._request_approve_plan = request_approve_plan
        """Async callback for `approve_plan` interrupts.

        When awaited, returns a `Future` that resolves to user approval decision.
        """

        # State tracking
        # FIX: keys are always normalized str via _normalize_tool_id to avoid
        # int/str mismatches when chunk ordering delivers id after name.
        self._current_tool_messages: dict[str, ToolCallMessage] = {}
        """Map of tool call IDs (normalized str) to their message widgets."""

        self._tool_watchdog_tasks: dict[str, asyncio.Task[None]] = {}
        """Per-tool watchdog tasks used to keep stale running tools from hanging UI."""

        self._timed_out_tool_messages: dict[str, ToolCallMessage] = {}
        """Tool widgets already marked timed out but still eligible for late results."""

        self._on_execute_watchdog_timeout = on_execute_watchdog_timeout
        """Called after an execute watchdog fires so the app can cancel the turn."""

        # Token display callbacks (set by the app after construction)
        self._on_tokens_update: _TokensUpdateCallback | None = None
        """Called with total context tokens after each LLM response."""

        self._on_tokens_hide: Callable[[], None] | None = None
        """Called to hide the token display during streaming."""

        self._on_tokens_show: _TokensShowCallback | None = None
        """Called to restore the token display with the cached value."""

        self._message_store: Any = None
        """Reference to MessageStore for updating tool messages after pruning."""

        self._subagent_activity = SubagentActivityTracker()
        """Tracks subagent stream activity for task tool progress display."""

    def start_execute_watchdog(
        self,
        tool_call_id: str,
        tool_msg: ToolCallMessage,
        args: dict[str, Any],
    ) -> None:
        """Start a UI watchdog for an execute tool call.

        The backend should normally return a ToolMessage when command execution
        succeeds, fails, or times out.  If that final message never arrives, the
        widget would otherwise stay in "running" forever.
        """
        delay = _execute_watchdog_delay(args)
        if delay is None:
            return

        self.cancel_tool_watchdog(tool_call_id)
        self._timed_out_tool_messages.pop(str(tool_call_id), None)
        self._tool_watchdog_tasks[tool_call_id] = asyncio.create_task(
            self._execute_watchdog(tool_call_id, tool_msg, delay)
        )

    def cancel_tool_watchdog(self, tool_call_id: str | int | None) -> None:
        """Cancel the watchdog associated with a completed/removed tool call."""
        if tool_call_id is None:
            return
        task = self._tool_watchdog_tasks.pop(str(tool_call_id), None)
        if task is not None:
            task.cancel()

    def cancel_all_tool_watchdogs(self) -> None:
        """Cancel all pending tool watchdogs."""
        for task in self._tool_watchdog_tasks.values():
            task.cancel()
        self._tool_watchdog_tasks.clear()
        self._timed_out_tool_messages.clear()

    async def _execute_watchdog(
        self,
        tool_call_id: str,
        tool_msg: ToolCallMessage,
        delay: int,
    ) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        active = self._current_tool_messages.get(tool_call_id)
        if active is not tool_msg:
            return

        error = t("tool.execute_watchdog_timeout", seconds=delay)
        try:
            tool_msg.set_error(error)
        except Exception:  # noqa: BLE001
            pass

        self._current_tool_messages.pop(tool_call_id, None)
        self._tool_watchdog_tasks.pop(tool_call_id, None)
        self._timed_out_tool_messages[tool_call_id] = tool_msg
        self._update_tool_message_in_store(tool_call_id, "error", error)

        if self._set_spinner and not self._current_tool_messages:
            await self._set_spinner(t("status.thinking"))

        if self._on_execute_watchdog_timeout is not None:
            try:
                await self._on_execute_watchdog_timeout(tool_call_id)
            except Exception:
                logger.warning(
                    "Execute watchdog timeout callback failed for tool_call_id=%s",
                    tool_call_id,
                    exc_info=True,
                )

    def pop_timed_out_tool_message(
        self, tool_call_id: str | int | None
    ) -> ToolCallMessage | None:
        """Return a timed-out tool widget for a late ToolMessage result, if any."""
        if tool_call_id is None:
            return None
        return self._timed_out_tool_messages.pop(str(tool_call_id), None)

    def set_message_store(self, message_store: Any) -> None:
        """Set the message store reference.

        Args:
            message_store: The MessageStore instance from the app.
        """
        self._message_store = message_store

    def _update_tool_message_in_store(
        self, tool_call_id: str | int, status: str, output: str
    ) -> bool:
        """Update tool message data in the store when no live widget is available.

        Args:
            tool_call_id: The tool call ID to find (str or int, normalized internally).
            status: The tool status (success/error).
            output: The tool output.

        Returns:
            True if the message was found and updated.
        """
        if self._message_store is None:
            return False

        from invincat_cli.widgets.message_store import ToolStatus

        # MessageStore.get_message_by_tool_call_id normalizes to str internally,
        # so a single call handles both int and str IDs.
        msg_data = self._message_store.get_message_by_tool_call_id(tool_call_id)
        if msg_data is None:
            return False

        try:
            tool_status = ToolStatus(status)
        except ValueError:
            tool_status = None

        if tool_status:
            self._message_store.update_message(
                msg_data.id, tool_status=tool_status, tool_output=output
            )
            return True
        return False

    def finalize_pending_tools_with_error(self, error: str) -> None:
        """Mark all pending/running tool widgets as error and clear tracking.

        This is used as a safety net when an unexpected exception aborts
        streaming before matching `ToolMessage` results are received.

        Args:
            error: Error text to display in each pending tool widget.
        """
        for tool_msg in list(self._current_tool_messages.values()):
            tool_msg.set_error(error)
        self._current_tool_messages.clear()
        self.cancel_all_tool_watchdogs()

        # Clear active streaming message to avoid stale "active" state in the store.
        if self._set_active_message:
            self._set_active_message(None)
