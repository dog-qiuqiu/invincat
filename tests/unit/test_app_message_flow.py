from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace

from invincat_cli.app_runtime import message_flow
from invincat_cli.widgets.message_store import MessageData, MessageStore, MessageType
from invincat_cli.widgets.messages import AppMessage, QueuedUserMessage


class FakeWidget:
    def __init__(self, widget_id: str | None = None) -> None:
        self.id = widget_id
        self.parent: FakeContainer | None = None
        self.removed = False

    async def remove(self) -> None:
        self.removed = True
        if self.parent is not None and self in self.parent.children:
            self.parent.children.remove(self)
        self.parent = None


class FakeLoading(FakeWidget):
    def __init__(self, widget_id: str | None = "loading") -> None:
        super().__init__(widget_id)
        self.statuses: list[str] = []

    def set_status(self, status: str) -> None:
        self.statuses.append(status)


class FakeContainer:
    def __init__(self, *, attached: bool = True) -> None:
        self.is_attached = attached
        self.children: list[object] = []
        self.scroll_visible_called = False
        self.removed_children = False
        self.fail_before_mount = False
        self.always_fail_mount = False

    async def mount(self, *widgets: object, before: object | None = None) -> None:
        if self.always_fail_mount:
            raise RuntimeError("mount failed")
        if before is not None and self.fail_before_mount:
            self.fail_before_mount = False
            raise RuntimeError("stale before target")
        index = (
            self.children.index(before)
            if before in self.children
            else len(self.children)
        )
        for offset, widget in enumerate(widgets):
            self.children.insert(index + offset, widget)
            if isinstance(widget, FakeWidget):
                widget.parent = self

    def query_one(self, selector: str) -> object:
        wanted_id = selector.removeprefix("#")
        for child in self.children:
            if getattr(child, "id", None) == wanted_id:
                return child
        raise message_flow.NoMatches(selector)

    async def remove_children(self) -> None:
        self.children.clear()
        self.removed_children = True

    def scroll_visible(self) -> None:
        self.scroll_visible_called = True


class FakeChat:
    def __init__(self) -> None:
        self.scroll_y = 0
        self.size = SimpleNamespace(height=20)


class FlowApp:
    def __init__(self) -> None:
        self._queued_widgets = deque()
        self._loading_widget: object | None = None
        self._message_store = MessageStore()
        self._status_bar = SimpleNamespace(count=None)
        self._status_bar.set_message_count = lambda count: setattr(
            self._status_bar, "count", count
        )
        self._ui_adapter = None
        self.messages = FakeContainer()
        self.bottom = FakeContainer()
        self.chat = FakeChat()
        self.later: list[object] = []
        self.missing_selectors: set[str] = set()
        self._hydration_pending = False

    def query_one(self, selector: str, *_args: object) -> object:
        if selector in self.missing_selectors:
            raise message_flow.NoMatches(selector)
        if selector == "#messages":
            return self.messages
        if selector == "#bottom-app-container":
            return self.bottom
        if selector == "#chat":
            return self.chat
        raise message_flow.NoMatches(selector)

    def call_later(self, callback: object) -> None:
        self.later.append(callback)

    async def _hydrate_messages_above(self) -> None:
        await message_flow.hydrate_messages_above(self)


def message_data(index: int, *, msg_type: MessageType = MessageType.APP) -> MessageData:
    return MessageData(type=msg_type, content=f"message {index}", id=f"msg-{index}")


def test_mount_before_queued_inserts_before_visible_queue() -> None:
    app = FlowApp()
    queued = FakeWidget("queued")
    app._queued_widgets.append(queued)
    app.messages.children.append(queued)
    queued.parent = app.messages
    new_widget = FakeWidget("new")

    asyncio.run(message_flow.mount_before_queued(app, app.messages, new_widget))

    assert app.messages.children == [new_widget, queued]


def test_mount_before_queued_appends_when_stale_queue_reference_fails() -> None:
    app = FlowApp()
    queued = FakeWidget("queued")
    queued.parent = app.messages
    app._queued_widgets.append(queued)
    app.messages.fail_before_mount = True
    new_widget = FakeWidget("new")

    asyncio.run(message_flow.mount_before_queued(app, app.messages, new_widget))

    assert app.messages.children == [new_widget]


def test_mount_before_queued_ignores_detached_container() -> None:
    app = FlowApp()
    detached = FakeContainer(attached=False)
    widget = FakeWidget("new")

    asyncio.run(message_flow.mount_before_queued(app, detached, widget))

    assert detached.children == []


def test_spinner_position_detects_queue_and_tail_positions() -> None:
    app = FlowApp()
    spinner = FakeLoading()
    queued = FakeWidget("queued")
    app._loading_widget = spinner
    app._queued_widgets.append(queued)

    app.messages.children = [spinner, queued]
    assert message_flow.is_spinner_at_correct_position(app, app.messages)

    app.messages.children = [queued, spinner]
    assert not message_flow.is_spinner_at_correct_position(app, app.messages)

    app._queued_widgets.clear()
    assert message_flow.is_spinner_at_correct_position(app, app.messages)

    app.messages.children = []
    assert not message_flow.is_spinner_at_correct_position(app, app.messages)

    app._queued_widgets.append(queued)
    app.messages.children = [spinner]
    assert not message_flow.is_spinner_at_correct_position(app, app.messages)


def test_set_spinner_mounts_updates_repositions_and_hides() -> None:
    app = FlowApp()

    asyncio.run(message_flow.set_spinner(app, "Thinking"))

    assert app._loading_widget in app.messages.children

    app.messages.children.clear()
    queued = FakeWidget("queued")
    app._queued_widgets.append(queued)
    app.messages.children.append(queued)
    queued.parent = app.messages
    app._loading_widget = FakeLoading()
    app._loading_widget.parent = app.messages
    app.messages.children.append(app._loading_widget)

    asyncio.run(message_flow.set_spinner(app, "Offloading"))

    assert app._loading_widget.statuses == ["Offloading"]
    assert app.messages.children[-2:] == [app._loading_widget, queued]

    asyncio.run(message_flow.set_spinner(app, None))

    assert app._loading_widget is None


def test_set_spinner_removes_existing_spinner_when_no_status() -> None:
    app = FlowApp()
    spinner = FakeLoading()
    app._loading_widget = spinner
    app.messages.children.append(spinner)
    spinner.parent = app.messages

    asyncio.run(message_flow.set_spinner(app, None))

    assert spinner.removed is True
    assert app._loading_widget is None


def test_mount_message_appends_store_and_scrolls_input_container() -> None:
    app = FlowApp()
    widget = AppMessage("hello")

    asyncio.run(message_flow.mount_message(app, widget))

    assert app._message_store.total_count == 1
    assert app._status_bar.count == 1
    assert widget in app.messages.children
    assert app.bottom.scroll_visible_called is True


def test_mount_message_tolerates_missing_input_container() -> None:
    app = FlowApp()
    app.missing_selectors.add("#bottom-app-container")

    asyncio.run(message_flow.mount_message(app, AppMessage("hello")))

    assert app._message_store.total_count == 1
    assert app.bottom.scroll_visible_called is False


def test_mount_message_returns_when_messages_container_missing() -> None:
    app = FlowApp()
    app.missing_selectors.add("#messages")

    asyncio.run(message_flow.mount_message(app, AppMessage("hello")))

    assert app._message_store.total_count == 0
    assert app.messages.children == []


def test_mount_message_appends_queued_user_message_after_visible_queue() -> None:
    app = FlowApp()
    queued_marker = FakeWidget("queued-marker")
    app._queued_widgets.append(queued_marker)
    app.messages.children.append(queued_marker)
    queued_marker.parent = app.messages
    widget = QueuedUserMessage("queued")

    asyncio.run(message_flow.mount_message(app, widget))

    assert app.messages.children == [queued_marker, widget]


def test_mount_message_skips_detached_messages_container() -> None:
    app = FlowApp()
    app.messages.is_attached = False

    asyncio.run(message_flow.mount_message(app, AppMessage("hello")))

    assert app._message_store.total_count == 0
    assert app.messages.children == []


def test_prune_old_messages_removes_oldest_visible_widgets() -> None:
    app = FlowApp()
    app._message_store.bulk_load([message_data(i) for i in range(52)])
    for msg in app._message_store.get_visible_messages():
        widget = FakeWidget(msg.id)
        widget.parent = app.messages
        app.messages.children.append(widget)

    asyncio.run(message_flow.prune_old_messages(app))

    assert app._message_store.visible_count == MessageStore.WINDOW_SIZE
    assert [getattr(child, "id", None) for child in app.messages.children[:2]] == [
        "msg-2",
        "msg-3",
    ]


def test_prune_old_messages_skips_missing_container_and_empty_prune_list() -> None:
    app = FlowApp()
    asyncio.run(message_flow.prune_old_messages(app))

    app._message_store.bulk_load([message_data(i) for i in range(50)])
    app._message_store.append(message_data(50))
    app.missing_selectors.add("#messages")

    asyncio.run(message_flow.prune_old_messages(app))

    assert app._message_store.visible_count == 51

    app = FlowApp()
    app._message_store.bulk_load([message_data(i) for i in range(50)])
    app._message_store.append(message_data(50))
    app._message_store.get_messages_to_prune = lambda: []  # type: ignore[method-assign]

    asyncio.run(message_flow.prune_old_messages(app))

    assert app._message_store.visible_count == 51


def test_prune_old_messages_advances_window_for_missing_non_streaming_widget() -> None:
    app = FlowApp()
    app._message_store.bulk_load([message_data(i) for i in range(50)])
    app._message_store.append(message_data(50))

    asyncio.run(message_flow.prune_old_messages(app))

    assert app._message_store.visible_count == MessageStore.WINDOW_SIZE


def test_prune_old_messages_skips_in_flight_tool_widget() -> None:
    app = FlowApp()
    messages = [
        MessageData(
            type=MessageType.TOOL,
            content="",
            id="tool-0",
            tool_name="shell",
            tool_call_id="call-0",
        )
    ]
    messages.extend(message_data(i) for i in range(1, 50))
    app._message_store.bulk_load(messages)
    app._message_store.append(message_data(50))
    tool_widget = FakeWidget("tool-0")
    tool_widget.parent = app.messages
    app.messages.children.append(tool_widget)
    for msg in app._message_store.get_visible_messages()[1:]:
        widget = FakeWidget(msg.id)
        widget.parent = app.messages
        app.messages.children.append(widget)
    app._ui_adapter = SimpleNamespace(_current_tool_messages={"call-0": tool_widget})

    asyncio.run(message_flow.prune_old_messages(app))

    assert tool_widget in app.messages.children
    assert app._message_store.visible_count == MessageStore.WINDOW_SIZE + 1


def test_prune_old_messages_removes_stale_tool_tracking_key() -> None:
    app = FlowApp()
    messages = [
        MessageData(
            type=MessageType.TOOL,
            content="",
            id="tool-0",
            tool_name="shell",
            tool_call_id="call-0",
        )
    ]
    messages.extend(message_data(i) for i in range(1, 50))
    app._message_store.bulk_load(messages)
    app._message_store.append(message_data(50))
    tool_widget = FakeWidget("tool-0")
    tool_widget.parent = app.messages
    app.messages.children.append(tool_widget)
    for msg in app._message_store.get_visible_messages()[1:]:
        widget = FakeWidget(msg.id)
        widget.parent = app.messages
        app.messages.children.append(widget)

    class StaleTracking(dict):
        def values(self) -> list[object]:
            return []

    app._ui_adapter = SimpleNamespace(
        _current_tool_messages=StaleTracking({"call-0": tool_widget})
    )

    asyncio.run(message_flow.prune_old_messages(app))

    assert "call-0" not in app._ui_adapter._current_tool_messages
    assert tool_widget.removed is True


def test_prune_old_messages_keeps_window_when_widget_remove_fails() -> None:
    class FailingRemoveWidget(FakeWidget):
        async def remove(self) -> None:
            raise RuntimeError("remove failed")

    app = FlowApp()
    app._message_store.bulk_load([message_data(i) for i in range(50)])
    app._message_store.append(message_data(50))
    first = FailingRemoveWidget("msg-0")
    first.parent = app.messages
    app.messages.children.append(first)
    for msg in app._message_store.get_visible_messages()[1:]:
        widget = FakeWidget(msg.id)
        widget.parent = app.messages
        app.messages.children.append(widget)

    asyncio.run(message_flow.prune_old_messages(app))

    assert app._message_store.visible_count == MessageStore.WINDOW_SIZE + 1
    assert first in app.messages.children


def test_prune_old_messages_skips_missing_streaming_widget() -> None:
    app = FlowApp()
    messages = [
        MessageData(
            type=MessageType.TOOL,
            content="",
            id="tool-streaming",
            tool_name="shell",
            is_streaming=True,
        )
    ]
    messages.extend(message_data(i) for i in range(1, 50))
    app._message_store.bulk_load(messages)
    app._message_store.append(message_data(50))

    asyncio.run(message_flow.prune_old_messages(app))

    assert app._message_store.visible_count == 51


def test_check_hydration_needed_schedules_when_near_top() -> None:
    app = FlowApp()
    app._message_store.bulk_load([message_data(i) for i in range(60)])
    app.chat.scroll_y = 5
    app.chat.size.height = 20

    message_flow.check_hydration_needed(app)
    message_flow.check_hydration_needed(app)

    assert app.later == [app._hydrate_messages_above]
    assert app._hydration_pending is True


def test_check_hydration_needed_skips_without_messages_above_or_chat() -> None:
    app = FlowApp()

    message_flow.check_hydration_needed(app)

    assert app.later == []

    app._message_store.bulk_load([message_data(i) for i in range(60)])
    app.missing_selectors.add("#chat")

    message_flow.check_hydration_needed(app)

    assert app.later == []


def test_hydrate_messages_above_mounts_archived_widgets_and_preserves_scroll() -> None:
    app = FlowApp()
    app._message_store.bulk_load([message_data(i) for i in range(60)])
    first_visible = FakeWidget("first-visible")
    app.messages.children.append(first_visible)
    app.chat.scroll_y = 7

    asyncio.run(message_flow.hydrate_messages_above(app))

    assert app._message_store.has_messages_above is False
    assert app._message_store.visible_count == 60
    assert app.chat.scroll_y == 57
    assert app.messages.children[-1] is first_visible
    assert app._hydration_pending is False


def test_hydrate_messages_above_skips_missing_or_empty_sources() -> None:
    app = FlowApp()
    asyncio.run(message_flow.hydrate_messages_above(app))

    app._message_store.bulk_load([message_data(i) for i in range(60)])
    app.missing_selectors.add("#chat")
    asyncio.run(message_flow.hydrate_messages_above(app))

    assert app._message_store.has_messages_above is True

    app = FlowApp()
    app._message_store.bulk_load([message_data(i) for i in range(60)])
    app.missing_selectors.add("#messages")
    asyncio.run(message_flow.hydrate_messages_above(app))

    assert app._message_store.has_messages_above is True

    app = FlowApp()
    app._message_store.bulk_load([message_data(i) for i in range(60)])
    app._message_store.get_messages_to_hydrate = lambda: []  # type: ignore[method-assign]
    asyncio.run(message_flow.hydrate_messages_above(app))

    assert app._message_store.has_messages_above is True


def test_hydrate_messages_above_handles_widget_and_mount_failures(
    monkeypatch,
) -> None:
    app = FlowApp()

    class BadMessageData:
        id = "bad-widget"
        content = ""

        def to_widget(self) -> object:
            raise RuntimeError("widget failed")

    class AssistantWidget(FakeWidget):
        async def set_content(self, _content: str) -> None:
            raise RuntimeError("content failed")

    class AssistantData:
        id = "assistant"
        content = "assistant text"

        def to_widget(self) -> AssistantWidget:
            return AssistantWidget(self.id)

    class PlainData:
        id = "plain"
        content = ""

        def to_widget(self) -> FakeWidget:
            return FakeWidget(self.id)

    app._message_store.bulk_load([message_data(i) for i in range(60)])
    app._message_store.get_messages_to_hydrate = lambda: [  # type: ignore[method-assign]
        BadMessageData(),
        AssistantData(),
        PlainData(),
    ]
    app.messages.always_fail_mount = True
    monkeypatch.setattr(message_flow, "AssistantMessage", AssistantWidget)

    asyncio.run(message_flow.hydrate_messages_above(app))

    assert app._message_store.has_messages_above is True
    assert app._message_store.get_visible_range() == (10, 60)
    assert app.chat.scroll_y == 0
    assert app.messages.children == []


def test_hydrate_messages_above_falls_back_to_sequential_mount() -> None:
    app = FlowApp()

    class FlakyContainer(FakeContainer):
        def __init__(self) -> None:
            super().__init__()
            self.batch_failed = False

        async def mount(self, *widgets: object, before: object | None = None) -> None:
            if len(widgets) > 1 and not self.batch_failed:
                self.batch_failed = True
                raise RuntimeError("batch failed")
            await super().mount(*widgets, before=before)

    app.messages = FlakyContainer()
    app._message_store.bulk_load([message_data(i) for i in range(60)])

    asyncio.run(message_flow.hydrate_messages_above(app))

    assert app.messages.batch_failed is True
    assert app._message_store.has_messages_above is False
    assert app._message_store.visible_count == 60
    assert [widget.id for widget in app.messages.children] == [
        *(f"msg-{index}" for index in range(10)),
    ]


def test_hydrate_messages_above_keeps_sequential_fallback_order_before_anchor() -> None:
    app = FlowApp()

    class FlakyContainer(FakeContainer):
        def __init__(self) -> None:
            super().__init__()
            self.batch_failed = False

        async def mount(self, *widgets: object, before: object | None = None) -> None:
            if len(widgets) > 1 and not self.batch_failed:
                self.batch_failed = True
                raise RuntimeError("batch failed")
            await super().mount(*widgets, before=before)

    app.messages = FlakyContainer()
    anchor = FakeWidget("msg-10")
    app.messages.children.append(anchor)
    anchor.parent = app.messages
    app._message_store.bulk_load([message_data(i) for i in range(60)])

    asyncio.run(message_flow.hydrate_messages_above(app))

    assert [widget.id for widget in app.messages.children[:11]] == [
        *(f"msg-{index}" for index in range(10)),
        "msg-10",
    ]


def test_mount_message_keeps_contiguous_window_after_history_hydration() -> None:
    app = FlowApp()
    app._message_store.bulk_load([message_data(i) for i in range(60)])
    app._message_store.mark_hydrated(10)
    for index in range(60):
        widget = FakeWidget(f"msg-{index}")
        widget.parent = app.messages
        app.messages.children.append(widget)

    asyncio.run(message_flow.mount_message(app, FakeWidget("msg-60")))

    assert app._message_store.get_visible_range() == (11, 61)
    assert [getattr(child, "id", None) for child in app.messages.children] == [
        *(f"msg-{index}" for index in range(11, 61)),
    ]


def test_hydrate_messages_above_rolls_back_mount_failure() -> None:
    app = FlowApp()

    class FailingSecondContainer(FakeContainer):
        async def mount(self, *widgets: object, before: object | None = None) -> None:
            if len(widgets) > 1:
                raise RuntimeError("batch failed")
            if getattr(widgets[0], "id", None) == "msg-1":
                raise RuntimeError("second failed")
            await super().mount(*widgets, before=before)

    class FakeData:
        def __init__(self, index: int) -> None:
            self.id = f"msg-{index}"
            self.content = ""

        def to_widget(self) -> FakeWidget:
            return FakeWidget(self.id)

    app.messages = FailingSecondContainer()
    app._message_store.bulk_load([message_data(i) for i in range(60)])
    app._message_store.get_messages_to_hydrate = lambda: [  # type: ignore[method-assign]
        FakeData(i) for i in range(10)
    ]

    asyncio.run(message_flow.hydrate_messages_above(app))

    assert app.messages.children == []
    assert app._message_store.get_visible_range() == (10, 60)


def test_clear_messages_resets_store_and_container() -> None:
    app = FlowApp()
    app._message_store.append(message_data(1))
    app.messages.children.append(FakeWidget("msg-1"))

    asyncio.run(message_flow.clear_messages(app))

    assert app._message_store.total_count == 0
    assert app.messages.children == []
    assert app.messages.removed_children is True


def test_clear_messages_handles_missing_messages_container() -> None:
    app = FlowApp()
    app._message_store.append(message_data(1))
    app.missing_selectors.add("#messages")

    asyncio.run(message_flow.clear_messages(app))

    assert app._message_store.total_count == 0
