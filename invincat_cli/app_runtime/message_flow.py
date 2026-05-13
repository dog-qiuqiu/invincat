"""Message mounting and pruning helpers for the Textual app."""

from __future__ import annotations

import logging
from typing import Any

from textual.containers import Container
from textual.css.query import NoMatches

from invincat_cli.app_runtime.thread_history import (
    is_in_flight_tool_widget,
    should_mark_missing_widget_pruned,
    tool_tracking_keys_for_widget,
)
from invincat_cli.widgets.message_store import MessageData
from invincat_cli.widgets.messages import QueuedUserMessage

logger = logging.getLogger(__name__)


async def mount_message(app: Any, widget: Any) -> None:  # noqa: ANN401
    """Mount a message widget and update the message store."""
    try:
        messages = app.query_one("#messages", Container)
    except NoMatches:
        return

    if not messages.is_attached:
        return

    message_data = MessageData.from_widget(widget)
    if not widget.id:
        widget.id = message_data.id
    app._message_store.append(message_data)

    if app._status_bar:
        app._status_bar.set_message_count(app._message_store.total_count)

    if isinstance(widget, QueuedUserMessage):
        await messages.mount(widget)
    else:
        await app._mount_before_queued(messages, widget)

    await prune_old_messages(app)

    try:
        input_container = app.query_one("#bottom-app-container", Container)
        input_container.scroll_visible()
    except NoMatches:
        pass


async def prune_old_messages(app: Any) -> None:  # noqa: ANN401
    """Prune oldest message widgets when the visible window is exceeded."""
    if not app._message_store.window_exceeded():
        return

    try:
        messages_container = app.query_one("#messages", Container)
    except NoMatches:
        logger.debug("Skipping pruning: #messages container not found")
        return

    to_prune = app._message_store.get_messages_to_prune()
    if not to_prune:
        return

    pruned_ids: list[str] = []
    active_tool_widgets: set[object] = set()
    if app._ui_adapter is not None:
        active_tool_widgets = set(app._ui_adapter._current_tool_messages.values())

    for msg_data in to_prune:
        try:
            widget = messages_container.query_one(f"#{msg_data.id}")

            if is_in_flight_tool_widget(widget, active_tool_widgets):
                logger.debug(
                    "Skipping prune of in-flight tool widget id=%s "
                    "(still awaiting ToolMessage result)",
                    msg_data.id,
                )
                continue

            if msg_data.type == "tool" and app._ui_adapter is not None:
                stale_keys = tool_tracking_keys_for_widget(
                    app._ui_adapter._current_tool_messages,
                    widget,
                )
                for key in stale_keys:
                    app._ui_adapter._current_tool_messages.pop(key, None)
                    logger.debug(
                        "Removed tool message from tracking: key=%s id=%s",
                        key,
                        msg_data.id,
                    )
            try:
                await widget.remove()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to remove widget %s during pruning; "
                    "skipping to keep store/DOM in sync",
                    msg_data.id,
                    exc_info=True,
                )
                continue
            pruned_ids.append(msg_data.id)
        except NoMatches:
            if not should_mark_missing_widget_pruned(
                is_streaming=msg_data.is_streaming,
            ):
                logger.debug(
                    "Widget %s not found but still streaming; skipping prune",
                    msg_data.id,
                )
            else:
                logger.debug(
                    "Widget %s not in DOM and not streaming; "
                    "force-advancing window to prevent unbounded growth",
                    msg_data.id,
                )
                pruned_ids.append(msg_data.id)

    if pruned_ids:
        app._message_store.mark_pruned(pruned_ids)


async def clear_messages(app: Any) -> None:  # noqa: ANN401
    """Clear the messages area and backing message store."""
    app._message_store.clear()
    try:
        messages = app.query_one("#messages", Container)
        await messages.remove_children()
    except NoMatches:
        logger.warning(
            "Messages container (#messages) not found during clear; "
            "UI may be out of sync with message store"
        )
