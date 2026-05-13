"""App-bound Textual action handlers."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

from textual.css.query import NoMatches
from textual.screen import ModalScreen

from invincat_cli.i18n import t
from invincat_cli.widgets.messages import SkillMessage, ToolCallMessage

logger = logging.getLogger(__name__)


def quit_or_interrupt(app: Any) -> None:  # noqa: ANN401
    """Handle Ctrl+C: interrupt active work, reject prompts, or quit on double press."""
    if app._shell_running and app._shell_worker:
        app._cancel_worker(app._shell_worker)
        app._quit_pending = False
        return

    if app._pending_approval_widget:
        app._pending_approval_widget.action_select_reject()
        app._quit_pending = False
        return

    if app._pending_ask_user_widget:
        app._pending_ask_user_widget.action_cancel()
        app._quit_pending = False
        return

    if app._agent_running and app._agent_worker:
        app._cancel_worker(app._agent_worker)
        app._quit_pending = False
        return

    if app._quit_pending:
        app.exit()
    else:
        app._arm_quit_pending("Ctrl+C")


def interrupt(app: Any) -> None:  # noqa: ANN401
    """Handle escape key."""
    from invincat_cli.widgets.thread_selector import ThreadSelectorScreen

    if (
        isinstance(app.screen, ThreadSelectorScreen)
        and app.screen.is_delete_confirmation_open
    ):
        app.screen.action_cancel()
        return

    if isinstance(app.screen, ModalScreen):
        cancel = getattr(app.screen, "action_cancel", None)
        if cancel is not None:
            cancel()
        else:
            app.screen.dismiss(None)
        return

    if app._chat_input:
        if app._chat_input.dismiss_completion():
            return
        if app._chat_input.exit_mode():
            return

    if app._shell_running and app._shell_worker:
        app._cancel_worker(app._shell_worker)
        return

    if app._pending_approval_widget:
        app._pending_approval_widget.action_select_reject()
        return

    if app._pending_ask_user_widget:
        app._pending_ask_user_widget.action_cancel()
        return

    if app._pending_messages:
        app._pop_last_queued_message()
        return

    if app._agent_running and app._agent_worker:
        app._cancel_worker(app._agent_worker)


def quit_app(app: Any) -> None:  # noqa: ANN401
    """Handle quit action (Ctrl+D)."""
    from invincat_cli.widgets.thread_selector import (
        DeleteThreadConfirmScreen,
        ThreadSelectorScreen,
    )

    if isinstance(app.screen, ThreadSelectorScreen):
        app.screen.action_delete_thread()
        return
    if isinstance(app.screen, DeleteThreadConfirmScreen):
        if app._quit_pending:
            app.exit()
            return
        app._arm_quit_pending("Ctrl+D")
        return
    app.exit()


def toggle_auto_approve(app: Any) -> None:  # noqa: ANN401
    """Toggle auto-approve mode or route shift-tab inside active overlays."""
    from invincat_cli.widgets.thread_selector import ThreadSelectorScreen

    if isinstance(app.screen, ThreadSelectorScreen):
        app.screen.action_focus_previous_filter()
        return
    if isinstance(app.screen, ModalScreen):
        return
    if app._pending_ask_user_widget is not None:
        app._pending_ask_user_widget.action_previous_question()
        return
    app._auto_approve = not app._auto_approve
    if app._status_bar:
        app._status_bar.set_auto_approve(enabled=app._auto_approve)
    if app._session_state:
        app._session_state.auto_approve = app._auto_approve


def toggle_tool_output(app: Any) -> None:  # noqa: ANN401
    """Toggle expand/collapse of the most recent tool output or skill body."""
    with suppress(NoMatches):
        skill_messages = list(app.query(SkillMessage))
        for skill_msg in reversed(skill_messages):
            if skill_msg._stripped_body.strip():
                skill_msg.toggle_body()
                return

    with suppress(NoMatches):
        tool_messages = list(app.query(ToolCallMessage))
        for tool_msg in reversed(tool_messages):
            if tool_msg.has_output:
                tool_msg.toggle_output()
                return


async def open_editor(app: Any) -> None:  # noqa: ANN401
    """Open the current prompt text in an external editor."""
    from invincat_cli.io.editor import open_in_editor

    chat_input = app._chat_input
    if not chat_input or not chat_input._text_area:
        return

    current_text = chat_input._text_area.text or ""

    edited: str | None = None
    try:
        with app.suspend():
            edited = open_in_editor(current_text)
    except Exception:
        logger.warning("External editor failed", exc_info=True)
        app.notify(
            t("app.external_editor_failed"),
            severity="error",
            timeout=5,
        )
        chat_input.focus_input()
        return

    if edited is not None:
        chat_input._text_area.text = edited
        lines = edited.split("\n")
        chat_input._text_area.move_cursor((len(lines) - 1, len(lines[-1])))
    chat_input.focus_input()
