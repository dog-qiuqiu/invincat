"""Approval, plan-mode, ask-user, and input routing hooks for the app."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from invincat_cli.app_runtime.approval import (
    TYPING_IDLE_THRESHOLD_SECONDS,
    user_is_typing,
)

if TYPE_CHECKING:
    from langgraph.pregel import Pregel
    from textual.widgets import Static

    from invincat_cli.app_runtime.state import InputMode
    from invincat_cli.core.ask_user_types import AskUserWidgetResult, Question
    from invincat_cli.widgets.approval import ApprovalMenu
    from invincat_cli.widgets.ask_user import AskUserMenu
    from invincat_cli.widgets.chat_input import ChatInput


class AppApprovalPlanMixin:
    """Approval widgets, plan mode, ask_user, and prompt input routing."""

    async def _request_approval(
        self,
        action_requests: Any,
        assistant_id: str | None,
        *,
        bypass_plan_guard: bool = False,
        allow_auto_approve: bool = True,
    ) -> asyncio.Future:
        """Request user approval inline in the messages area."""
        from invincat_cli.app_runtime.approval_handlers import request_approval

        return await request_approval(
            self,
            action_requests,
            assistant_id,
            bypass_plan_guard=bypass_plan_guard,
            allow_auto_approve=allow_auto_approve,
        )

    async def _handle_plan_guard_auto_reject(
        self,
        disallowed_tool_names: list[str],
    ) -> None:
        """Mount the `/plan` guard rejection notice and approval prompt."""
        from invincat_cli.app_runtime.approval_handlers import (
            handle_plan_guard_auto_reject,
        )

        await handle_plan_guard_auto_reject(self, disallowed_tool_names)

    async def _mount_auto_approval_messages(self, commands: list[str]) -> None:
        """Mount system messages for shell commands approved by allow-list."""
        from invincat_cli.app_runtime.approval_handlers import (
            mount_auto_approval_messages,
        )

        await mount_auto_approval_messages(self, commands)

    async def _wait_for_pending_approval_widget(self) -> None:
        """Wait briefly for any active approval widget before showing another."""
        from invincat_cli.app_runtime.approval_handlers import (
            wait_for_pending_approval_widget,
        )

        await wait_for_pending_approval_widget(self)

    async def _mount_approval_widget(
        self,
        menu: ApprovalMenu,
        result_future: asyncio.Future[dict[str, str]],
    ) -> None:
        """Mount the approval menu widget inline in the messages area."""
        from invincat_cli.app_runtime.approval_handlers import mount_approval_widget

        await mount_approval_widget(self, menu, result_future)

    async def _deferred_show_approval(
        self,
        placeholder: Static,
        menu: ApprovalMenu,
        result_future: asyncio.Future[dict[str, str]],
    ) -> None:
        """Wait until the user is idle, then swap placeholder for the real menu."""
        from invincat_cli.app_runtime.approval_handlers import deferred_show_approval

        await deferred_show_approval(self, placeholder, menu, result_future)

    async def _remove_approval_placeholder(self, *, context: str) -> None:
        """Remove any mounted deferred approval placeholder."""
        from invincat_cli.app_runtime.approval_handlers import (
            remove_approval_placeholder,
        )

        await remove_approval_placeholder(self, context=context)

    def _on_auto_approve_enabled(self) -> None:
        """Handle auto-approve being enabled via the HITL approval menu."""
        from invincat_cli.app_runtime.approval_handlers import enable_auto_approve

        enable_auto_approve(self)

    async def _handle_plan_task(self) -> None:
        """Handle `/plan` mode entry."""
        from invincat_cli.app_runtime.plan_handlers import handle_plan_task

        await handle_plan_task(self)

    def _reset_plan_mode_state(self) -> None:
        """Restore main-thread state and clear planner bookkeeping."""
        from invincat_cli.app_runtime.plan_handlers import reset_plan_mode_state

        reset_plan_mode_state(self)

    async def _exit_plan_mode(self) -> None:
        """Exit plan mode, cancel planner work, and restore main thread."""
        from invincat_cli.app_runtime.plan_handlers import exit_plan_mode

        await exit_plan_mode(self)

    async def _run_planner(self, task: str) -> bool:
        """Send a user message to the planner agent session."""
        from invincat_cli.app_runtime.plan_handlers import run_planner

        return await run_planner(self, task)

    async def _ensure_planner_agent(self) -> Pregel | None:
        """Lazily create and cache a planner peer-agent."""
        from invincat_cli.app_runtime.plan_handlers import ensure_planner_agent

        return await ensure_planner_agent(self)

    async def _get_thread_state_values_for_agent(
        self,
        agent: Pregel,
        thread_id: str,
    ) -> dict[str, Any]:
        """Fetch state values from a specific agent/thread pair."""
        from invincat_cli.app_runtime.plan_handlers import (
            get_thread_state_values_for_agent,
        )

        return await get_thread_state_values_for_agent(agent, thread_id)

    async def _after_planner_turn(self) -> None:
        """Check planner turn result and drive plan approval flow."""
        from invincat_cli.app_runtime.plan_handlers import after_planner_turn

        await after_planner_turn(self)

    async def _process_planner_todos_approval(
        self,
        todos: list[dict[str, str]],
    ) -> bool:
        """Approve planner todos and finalize plan mode when approved."""
        from invincat_cli.app_runtime.plan_handlers import (
            process_planner_todos_approval,
        )

        return await process_planner_todos_approval(self, todos)

    async def _maybe_approve_current_planner_todos(self) -> bool:
        """Best-effort immediate approval when planner already has todo state."""
        from invincat_cli.app_runtime.plan_handlers import (
            maybe_approve_current_planner_todos,
        )

        return await maybe_approve_current_planner_todos(self)

    def _invalidate_planner_agent_cache(self) -> None:
        """Invalidate cached planner runtime so it picks up fresh model config."""
        self._planner_agent = None
        self._planner_last_todos_fingerprint = None
        self._planner_prompted_todos_fingerprint = None

    async def _finalize_planner_approval(
        self,
        todos: list[dict[str, str]],
        *,
        planner_state_values: dict[str, Any] | None = None,
    ) -> None:
        """Finalize plan mode after approval and handoff execution to main agent."""
        from invincat_cli.app_runtime.plan_handlers import finalize_planner_approval

        await finalize_planner_approval(
            self,
            todos,
            planner_state_values=planner_state_values,
        )

    async def _execute_plan_handoff(self, prompt: str) -> None:
        """Execute approved plan handoff explicitly on the main agent."""
        from invincat_cli.app_runtime.plan_handlers import execute_plan_handoff

        await execute_plan_handoff(self, prompt)

    async def _remove_ask_user_widget(
        self,
        widget: AskUserMenu,
        *,
        context: str,
    ) -> None:
        """Remove an ask_user widget without surfacing cleanup races."""
        from invincat_cli.app_runtime.approval_handlers import remove_ask_user_widget

        await remove_ask_user_widget(widget, context=context)

    async def _request_ask_user(
        self,
        questions: list[Question],
    ) -> asyncio.Future[AskUserWidgetResult]:
        """Display the ask_user widget and return a Future with user response."""
        from invincat_cli.app_runtime.approval_handlers import request_ask_user

        return await request_ask_user(self, questions)

    async def _wait_for_pending_ask_user_widget(self) -> None:
        """Wait for an active ask_user widget, forcing cleanup on timeout."""
        from invincat_cli.app_runtime.approval_handlers import (
            wait_for_pending_ask_user_widget,
        )

        await wait_for_pending_ask_user_widget(self)

    async def _mount_ask_user_widget(
        self,
        menu: AskUserMenu,
        result_future: asyncio.Future[AskUserWidgetResult],
    ) -> None:
        """Mount the ask_user widget and focus the active field."""
        from invincat_cli.app_runtime.approval_handlers import mount_ask_user_widget

        await mount_ask_user_widget(self, menu, result_future)

    async def on_ask_user_menu_answered(self, event: Any) -> None:  # noqa: ARG002
        """Handle ask_user menu answers."""
        from invincat_cli.app_runtime.approval_handlers import (
            handle_ask_user_menu_answered,
        )

        await handle_ask_user_menu_answered(self)

    async def on_ask_user_menu_cancelled(self, event: Any) -> None:  # noqa: ARG002
        """Handle ask_user menu cancellation."""
        from invincat_cli.app_runtime.approval_handlers import (
            handle_ask_user_menu_cancelled,
        )

        await handle_ask_user_menu_cancelled(self)

    async def _request_approve_plan(
        self,
        todos: list[dict[str, Any]],
    ) -> asyncio.Future[dict[str, Any]]:
        """Display plan approval using the standard ApprovalMenu component."""
        from invincat_cli.app_runtime.approval_handlers import request_approve_plan

        return await request_approve_plan(self, todos)

    async def on_approve_widget_approved(self, event: Any) -> None:  # noqa: ARG002
        """Handle approve widget approval."""
        from invincat_cli.app_runtime.approval_handlers import (
            handle_approve_widget_approved,
        )

        await handle_approve_widget_approved(self)

    async def on_approve_widget_rejected(self, event: Any) -> None:  # noqa: ARG002
        """Handle approve widget rejection."""
        from invincat_cli.app_runtime.approval_handlers import (
            handle_approve_widget_rejected,
        )

        await handle_approve_widget_rejected(self)

    async def _process_message(self, value: str, mode: InputMode) -> None:
        """Route a message to the appropriate handler based on mode."""
        from invincat_cli.app_runtime.input_handlers import process_message

        await process_message(self, value, mode)

    def _can_bypass_queue(self, value: str) -> bool:
        """Check if a slash command can skip the message queue."""
        from invincat_cli.app_runtime.input_handlers import can_bypass_queue

        return can_bypass_queue(self, value)

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        """Handle submitted input from ChatInput widget."""
        from invincat_cli.app_runtime.input_handlers import handle_chat_input_submitted

        await handle_chat_input_submitted(self, event)

    def on_chat_input_mode_changed(self, event: ChatInput.ModeChanged) -> None:
        """Update status bar when input mode changes."""
        if self._status_bar:
            self._status_bar.set_mode(event.mode)

    def on_chat_input_typing(self, event: ChatInput.Typing) -> None:  # noqa: ARG002
        """Record the most recent keystroke time."""
        from invincat_cli import app as app_module

        self._last_typed_at = app_module._monotonic()

    def _is_user_typing(self) -> bool:
        """Return whether the user typed recently."""
        from invincat_cli import app as app_module

        return user_is_typing(
            last_typed_at=self._last_typed_at,
            now=app_module._monotonic(),
            threshold_seconds=TYPING_IDLE_THRESHOLD_SECONDS,
        )

    async def on_approval_menu_decided(self, event: Any) -> None:  # noqa: ARG002
        """Handle approval menu decision, cleanup, and input refocus."""
        await self._remove_approval_placeholder(context="approval cleanup")
        if self._pending_approval_widget:
            await self._pending_approval_widget.remove()
            self._pending_approval_widget = None
        if self._chat_input:
            self.call_after_refresh(self._chat_input.focus_input)

    def action_toggle_auto_approve(self) -> None:
        """Toggle auto-approve mode for the current session."""
        from invincat_cli.app_runtime.action_handlers import toggle_auto_approve

        toggle_auto_approve(self)

    def action_toggle_tool_output(self) -> None:
        """Toggle expand/collapse of the most recent tool output or skill body."""
        from invincat_cli.app_runtime.action_handlers import toggle_tool_output

        toggle_tool_output(self)

    def action_approval_up(self) -> None:
        """Handle up arrow in approval menu."""
        if self._pending_approval_widget and not self._is_input_focused():
            self._pending_approval_widget.action_move_up()

    def action_approval_down(self) -> None:
        """Handle down arrow in approval menu."""
        if self._pending_approval_widget and not self._is_input_focused():
            self._pending_approval_widget.action_move_down()

    def action_approval_select(self) -> None:
        """Handle enter in approval menu."""
        if self._pending_approval_widget and not self._is_input_focused():
            self._pending_approval_widget.action_select()

    def _is_input_focused(self) -> bool:
        """Check if the chat input or its text area has focus."""
        if not self._chat_input:
            return False
        focused = self.focused
        if focused is None:
            return False
        return focused.id == "chat-input" or focused in self._chat_input.walk_children()

    def action_approval_yes(self) -> None:
        """Handle yes/1 in approval menu."""
        if self._pending_approval_widget:
            self._pending_approval_widget.action_select_approve()

    def action_approval_auto(self) -> None:
        """Handle auto/2 in approval menu."""
        if self._pending_approval_widget:
            self._pending_approval_widget.action_select_auto()

    def action_approval_no(self) -> None:
        """Handle no/3 in approval menu."""
        if self._pending_approval_widget:
            self._pending_approval_widget.action_select_reject()

    def action_approval_escape(self) -> None:
        """Handle escape in approval menu."""
        if self._pending_approval_widget:
            self._pending_approval_widget.action_select_reject()
