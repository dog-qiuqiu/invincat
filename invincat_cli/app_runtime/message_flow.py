"""Message mounting and pruning helpers for the Textual app."""

from __future__ import annotations

import logging
from typing import Any

from textual.containers import Container, VerticalScroll
from textual.css.query import NoMatches
from textual.widget import Widget

from invincat_cli.app_runtime.thread_history import (
    is_in_flight_tool_widget,
    should_mark_missing_widget_pruned,
    tool_tracking_keys_for_widget,
)
from invincat_cli.core.session_stats import SpinnerStatus
from invincat_cli.widgets.loading import LoadingWidget
from invincat_cli.widgets.message_store import MessageData
from invincat_cli.widgets.messages import AssistantMessage, QueuedUserMessage

logger = logging.getLogger(__name__)


async def mount_before_queued(app: Any, container: Container, widget: Widget) -> None:
    """Mount a widget before queued messages, or append when no queue is visible."""
    if not container.is_attached:
        return
    first_queued = app._queued_widgets[0] if app._queued_widgets else None
    if first_queued is not None and first_queued.parent is container:
        try:
            await container.mount(widget, before=first_queued)
        except Exception:
            logger.warning(
                "Stale queued-widget reference; appending at end",
                exc_info=True,
            )
        else:
            return
    await container.mount(widget)


def is_spinner_at_correct_position(app: Any, container: Container) -> bool:
    """Return whether the loading spinner is correctly positioned."""
    children = list(container.children)
    if not children or app._loading_widget not in children:
        return False

    if app._queued_widgets:
        first_queued = app._queued_widgets[0]
        if first_queued not in children:
            return False
        return children.index(app._loading_widget) == (
            children.index(first_queued) - 1
        )

    return children[-1] == app._loading_widget


async def set_spinner(app: Any, status: SpinnerStatus) -> None:
    """Show, update, or hide the loading spinner."""
    if status is None:
        if app._loading_widget:
            await app._loading_widget.remove()
            app._loading_widget = None
        return

    messages = app.query_one("#messages", Container)

    if app._loading_widget is None:
        app._loading_widget = LoadingWidget(status)
        await mount_before_queued(app, messages, app._loading_widget)
    else:
        app._loading_widget.set_status(status)
        if not is_spinner_at_correct_position(app, messages):
            await app._loading_widget.remove()
            await mount_before_queued(app, messages, app._loading_widget)
    # Don't anchor here; streaming shouldn't pull a reader back to bottom.


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
        await mount_before_queued(app, messages, widget)

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


def check_hydration_needed(app: Any) -> None:  # noqa: ANN401
    """Schedule hydration when the user scrolls near the top."""
    if not app._message_store.has_messages_above:
        return

    try:
        chat = app.query_one("#chat", VerticalScroll)
    except NoMatches:
        logger.debug("Skipping hydration check: #chat container not found")
        return

    scroll_y = chat.scroll_y
    viewport_height = chat.size.height

    if app._message_store.should_hydrate_above(scroll_y, viewport_height):
        app.call_later(app._hydrate_messages_above)


async def hydrate_messages_above(app: Any) -> None:  # noqa: ANN401
    """Hydrate older messages when the user scrolls near the top."""
    if not app._message_store.has_messages_above:
        return

    try:
        chat = app.query_one("#chat", VerticalScroll)
    except NoMatches:
        logger.debug("Skipping hydration: #chat not found")
        return

    try:
        messages_container = app.query_one("#messages", Container)
    except NoMatches:
        logger.debug("Skipping hydration: #messages not found")
        return

    to_hydrate = app._message_store.get_messages_to_hydrate()
    if not to_hydrate:
        return

    old_scroll_y = chat.scroll_y
    first_child = (
        messages_container.children[0] if messages_container.children else None
    )

    hydrated_count = 0
    hydrated_widgets: list[tuple[Widget, MessageData]] = []
    for msg_data in to_hydrate:
        try:
            widget = msg_data.to_widget()
            hydrated_widgets.append((widget, msg_data))
        except Exception:
            logger.warning(
                "Failed to create widget for message %s",
                msg_data.id,
                exc_info=True,
            )

    widgets_to_mount = [widget for widget, _ in hydrated_widgets]
    try:
        if first_child:
            await messages_container.mount(*widgets_to_mount, before=first_child)
        else:
            await messages_container.mount(*widgets_to_mount)
        hydrated_count = len(widgets_to_mount)
    except Exception:
        logger.warning(
            "Batch hydration mount failed; falling back to sequential",
            exc_info=True,
        )
        for widget, _ in hydrated_widgets:
            try:
                if first_child:
                    await messages_container.mount(widget, before=first_child)
                else:
                    await messages_container.mount(widget)
                first_child = widget
                hydrated_count += 1
            except Exception:
                logger.warning(
                    "Failed to mount hydrated widget %s",
                    widget.id,
                    exc_info=True,
                )

    for widget, msg_data in hydrated_widgets:
        if isinstance(widget, AssistantMessage) and msg_data.content:
            try:
                await widget.set_content(msg_data.content)
            except Exception:
                logger.warning(
                    "Failed to set content for hydrated widget",
                    exc_info=True,
                )

    if hydrated_count > 0:
        app._message_store.mark_hydrated(hydrated_count)

    estimated_height_per_message = 5
    added_height = hydrated_count * estimated_height_per_message
    chat.scroll_y = old_scroll_y + added_height


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
