"""Message mounting helpers for the Textual app."""

from __future__ import annotations

import logging
from typing import Any

from textual.containers import Container
from textual.css.query import NoMatches
from textual.widget import Widget

from invincat_cli.core.session_stats import SpinnerStatus
from invincat_cli.widgets.loading import LoadingWidget
from invincat_cli.widgets.message_store import MessageData
from invincat_cli.widgets.messages import QueuedUserMessage

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
        return children.index(app._loading_widget) == (children.index(first_queued) - 1)

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

    try:
        input_container = app.query_one("#bottom-app-container", Container)
        input_container.scroll_visible()
    except NoMatches:
        pass


async def mount_message_after(app: Any, anchor: Any, widget: Any) -> None:  # noqa: ANN401
    """Mount a message widget immediately after an existing message widget."""
    try:
        messages = app.query_one("#messages", Container)
    except NoMatches:
        return

    if not messages.is_attached:
        return

    message_data = MessageData.from_widget(widget)
    if not widget.id:
        widget.id = message_data.id
    anchor_id = getattr(anchor, "id", None)
    app._message_store.insert_after(anchor_id, message_data)

    if app._status_bar:
        app._status_bar.set_message_count(app._message_store.total_count)

    mounted = False
    if getattr(anchor, "parent", None) is messages:
        children = list(messages.children)
        if anchor in children:
            anchor_index = children.index(anchor)
            before = (
                children[anchor_index + 1]
                if anchor_index + 1 < len(children)
                else None
            )
            if before is not None:
                try:
                    await messages.mount(widget, before=before)
                except Exception:
                    logger.warning(
                        "Failed to mount message after anchor; appending",
                        exc_info=True,
                    )
                else:
                    mounted = True
            if not mounted:
                await messages.mount(widget)
                mounted = True

    if not mounted:
        await mount_before_queued(app, messages, widget)

    try:
        input_container = app.query_one("#bottom-app-container", Container)
        input_container.scroll_visible()
    except NoMatches:
        pass


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
