"""App-bound chat input handlers."""

from __future__ import annotations

import logging
from typing import Any

from invincat_cli.app_runtime.queueing import can_bypass_busy_queue
from invincat_cli.app_runtime.state import InputMode, QueuedMessage
from invincat_cli.i18n import t
from invincat_cli.widgets.messages import QueuedUserMessage

logger = logging.getLogger(__name__)


async def process_message(app: Any, value: str, mode: InputMode) -> None:  # noqa: ANN401
    """Route a message to the appropriate handler based on mode."""
    if mode == "shell":
        await app._handle_shell_command(value.removeprefix("!"))
    elif mode == "command":
        await app._handle_command(value)
    elif mode == "normal":
        await app._handle_user_message(value)
    else:
        logger.warning("Unrecognized input mode %r, treating as normal", mode)
        await app._handle_user_message(value)


def can_bypass_queue(app: Any, value: str) -> bool:  # noqa: ANN401
    """Return whether a slash command can skip the message queue."""
    return can_bypass_busy_queue(
        value,
        connecting=app._connecting,
        agent_running=app._agent_running,
        shell_running=app._shell_running,
    )


async def handle_chat_input_submitted(app: Any, event: Any) -> None:  # noqa: ANN401
    """Handle submitted input from the chat input widget."""
    value = event.value
    mode: InputMode = event.mode

    app._quit_pending = False

    from invincat_cli.hooks import dispatch_hook

    await dispatch_hook("user.prompt", {})

    from invincat_cli.command_registry import ALWAYS_IMMEDIATE

    if mode == "command" and value.lower().strip() in ALWAYS_IMMEDIATE:
        app.exit()
        return

    if app._thread_switching:
        app.notify(
            t("app.thread_switch_in_progress"),
            severity="warning",
            timeout=3,
        )
        return

    if app._agent_running or app._shell_running or app._connecting:
        if mode == "command" and app._can_bypass_queue(value.lower().strip()):
            await app._process_message(value, mode)
            return
        app._pending_messages.append(QueuedMessage(text=value, mode=mode))
        queued_widget = QueuedUserMessage(value)
        app._queued_widgets.append(queued_widget)
        await app._mount_message(queued_widget)
        return

    await app._process_message(value, mode)
