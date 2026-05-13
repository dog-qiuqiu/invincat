"""App-bound Textual action handlers."""

from __future__ import annotations

import logging
from typing import Any

from textual.screen import ModalScreen

from invincat_cli.i18n import t

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
