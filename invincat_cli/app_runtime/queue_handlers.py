"""App-bound pending-message queue handlers."""

from __future__ import annotations

import logging
from typing import Any

from invincat_cli.app_runtime.agent import (
    queued_scheduled_run_state,
    should_continue_queue_after_sync_message,
    should_process_next_from_queue,
)
from invincat_cli.i18n import t
from invincat_cli.widgets.messages import ErrorMessage

logger = logging.getLogger(__name__)


async def process_next_from_queue(app: Any) -> None:  # noqa: ANN401
    """Process the next pending message in FIFO order."""
    if not should_process_next_from_queue(
        processing_pending=app._processing_pending,
        has_pending_messages=bool(app._pending_messages),
        exiting=app._exit,
    ):
        return

    app._processing_pending = True
    msg = None
    try:
        msg = app._pending_messages.popleft()

        scheduled_state = queued_scheduled_run_state(
            msg,
            message_offset=app._message_store.total_count,
        )
        app._active_scheduled_run = scheduled_state.active_run
        if scheduled_state.message_offset is not None:
            app._scheduled_run_message_offset = scheduled_state.message_offset
        app._scheduled_turn_status = scheduled_state.turn_status
        app._scheduled_turn_error = scheduled_state.turn_error
        app._scheduled_turn_retry_used = scheduled_state.retry_used

        if app._queued_widgets:
            widget = app._queued_widgets.popleft()
            await widget.remove()

        await app._process_message(msg.text, msg.mode)
    except Exception as queue_exc:
        logger.exception("Failed to process queued message")
        app._finish_active_scheduled_run_as_failed(str(queue_exc))
        preview = msg.text[:60] if msg is not None else ""
        await app._mount_message(
            ErrorMessage(t("queue.process_failed").format(message=preview))
        )
    finally:
        app._processing_pending = False

    if should_continue_queue_after_sync_message(
        agent_running=app._agent_running,
        shell_running=app._shell_running,
        has_pending_messages=bool(app._pending_messages),
    ):
        await app._process_next_from_queue()


def pop_last_queued_message(app: Any) -> None:  # noqa: ANN401
    """Remove the most recently queued message (LIFO)."""
    if not app._pending_messages:
        return

    if len(app._pending_messages) != len(app._queued_widgets):
        logger.error(
            "_pending_messages (%d) and _queued_widgets (%d) are out of sync; "
            "skipping pop to avoid mismatched removal. "
            "Call _discard_queue() to reset both deques.",
            len(app._pending_messages),
            len(app._queued_widgets),
        )
        return

    msg = app._pending_messages.pop()
    widget = app._queued_widgets.pop()
    widget.remove()

    if not app._chat_input:
        logger.warning(
            "Chat input unavailable during queue pop; "
            "message text cannot be restored: %s",
            msg.text[:60],
        )
        app.notify(t("queue.discarded"), timeout=2)
        return

    if not app._chat_input.value.strip():
        app._chat_input.value = msg.text
        app.notify(t("queue.moved_to_input"), timeout=2)
    else:
        app.notify(t("queue.discarded_input_not_empty"), timeout=3)


def discard_queue(app: Any) -> None:  # noqa: ANN401
    """Clear pending messages, deferred actions, and queued widgets."""
    app._pending_messages.clear()
    for widget in app._queued_widgets:
        widget.remove()
    app._queued_widgets.clear()
    app._deferred_actions.clear()
