from __future__ import annotations

import asyncio
import sys
from collections import deque
from types import SimpleNamespace

import pytest
from textual.content import Content

from invincat_cli.app_runtime import thread_handlers
from invincat_cli.app_runtime.state import ThreadHistoryPayload
from invincat_cli.app_runtime.thread_runtime import ThreadSwitchSnapshot
from invincat_cli.goal_mode.models import GoalState
from invincat_cli.goal_mode.store import GoalStore
from invincat_cli.widgets.message_store import MessageData, MessageStore, MessageType
from invincat_cli.widgets.messages import AppMessage


class FakeMessagesContainer:
    def __init__(self) -> None:
        self.mounted: list[object] = []

    async def mount(self, *widgets: object) -> None:
        self.mounted.extend(widgets)


class FakeChat:
    def __init__(self) -> None:
        self.scrolled = False

    def scroll_end(self, **_kwargs: object) -> None:
        self.scrolled = True


class FakeChatInput:
    def __init__(self) -> None:
        self.cursor_states: list[bool] = []

    def set_cursor_active(self, *, active: bool) -> None:
        self.cursor_states.append(active)


class FakeStatusBar:
    def __init__(self) -> None:
        self.count: int | None = None
        self.goal_modes: list[bool] = []

    def set_message_count(self, count: int) -> None:
        self.count = count

    def set_goal_mode(self, *, enabled: bool) -> None:
        self.goal_modes.append(enabled)


class ThreadApp:
    def __init__(self) -> None:
        self._agent = object()
        self._session_state = SimpleNamespace(
            thread_id="old-thread",
            goal_mode=False,
            goal=None,
        )
        self._lc_thread_id = "old-lc-thread"
        self._thread_switching = False
        self._chat_input = FakeChatInput()
        self._agent_running = False
        self._pending_messages = deque(["pending"])
        self._queued_widgets = deque(["queued"])
        self._context_tokens = 12
        self._tokens_approximate = True
        self._message_store = MessageStore()
        self._status_bar = FakeStatusBar()
        self.messages_container = FakeMessagesContainer()
        self.chat = FakeChat()
        self.messages: list[object] = []
        self.statuses: list[str] = []
        self.token_updates: list[int] = []
        self.updated_tokens: list[int] = []
        self.cleared = 0
        self.banner_updates: list[tuple[str, str, bool]] = []
        self.loaded: list[tuple[str | None, ThreadHistoryPayload | None]] = []
        self.rolled_back: list[ThreadSwitchSnapshot] = []
        self.restored: list[tuple[ThreadSwitchSnapshot, str]] = []
        self.workers: list[tuple[object, bool]] = []

    def _remote_agent(self) -> bool:
        return False

    def query_one(self, selector: str, *_args: object) -> object:
        if selector == "#messages":
            return self.messages_container
        if selector == "#chat":
            return self.chat
        raise LookupError(selector)

    def set_timer(self, _delay: float, callback: object) -> None:
        callback()

    def run_worker(self, work: object, *, exclusive: bool) -> None:
        self.workers.append((work, exclusive))
        close = getattr(work, "close", None)
        if close is not None:
            close()

    def _on_tokens_update(self, tokens: int) -> None:
        self.token_updates.append(tokens)

    def _update_tokens(self, tokens: int) -> None:
        self.updated_tokens.append(tokens)

    def _update_status(self, status: str) -> None:
        self.statuses.append(status)

    async def _clear_messages(self) -> None:
        self.cleared += 1

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    async def _reset_thread_conversation_view(self) -> None:
        await thread_handlers.reset_thread_conversation_view(self)

    def _apply_thread_switch_ids(self, thread_id: str) -> None:
        thread_handlers.apply_thread_switch_ids(self, thread_id)

    def _rollback_thread_switch_ids(self, snapshot: ThreadSwitchSnapshot) -> None:
        self.rolled_back.append(snapshot)
        thread_handlers.rollback_thread_switch_ids(self, snapshot)

    async def _restore_previous_thread_after_failed_switch(
        self,
        *,
        snapshot: ThreadSwitchSnapshot,
        failed_thread_id: str,
    ) -> bool:
        self.restored.append((snapshot, failed_thread_id))
        return True


def user_message(content: str = "hello") -> MessageData:
    return MessageData(type=MessageType.USER, content=content)


def patch_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    def record_banner(
        app: ThreadApp,
        thread_id: str,
        *,
        missing_message: str,
        warn_if_missing: bool,
    ) -> None:
        app.banner_updates.append((thread_id, missing_message, warn_if_missing))

    monkeypatch.setattr(thread_handlers, "update_welcome_banner", record_banner)


def test_get_thread_state_values_uses_local_fallback_for_empty_remote_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Agent:
        async def aget_state(self, _config: object) -> object:
            return SimpleNamespace(values={"messages": [], "_context_tokens": None})

    app = ThreadApp()
    app._agent = Agent()
    app._remote_agent = lambda: True

    async def read_fallback(_thread_id: str) -> dict[str, object]:
        return {"messages": ["fallback"], "_context_tokens": 20}

    monkeypatch.setattr(
        thread_handlers, "read_channel_values_from_checkpointer", read_fallback
    )

    values = asyncio.run(thread_handlers.get_thread_state_values(app, "thread-1"))

    assert values == {"messages": ["fallback"], "_context_tokens": 20}


def test_get_thread_state_values_handles_no_agent_and_nonempty_remote_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ThreadApp()
    app._agent = None

    assert asyncio.run(thread_handlers.get_thread_state_values(app, "thread-1")) == {}

    class Agent:
        async def aget_state(self, _config: object) -> object:
            return SimpleNamespace(values={"messages": ["remote"], "x": 1})

    async def fail_fallback(_thread_id: str) -> dict[str, object]:
        raise AssertionError("fallback should not be used")

    app._agent = Agent()
    app._remote_agent = lambda: True
    monkeypatch.setattr(
        thread_handlers, "read_channel_values_from_checkpointer", fail_fallback
    )

    values = asyncio.run(thread_handlers.get_thread_state_values(app, "thread-1"))

    assert values == {"messages": ["remote"], "x": 1}


def test_get_thread_state_values_returns_empty_local_state_without_remote() -> None:
    class Agent:
        async def aget_state(self, _config: object) -> object:
            return SimpleNamespace(values={"messages": []})

    app = ThreadApp()
    app._agent = Agent()
    app._remote_agent = lambda: False

    assert asyncio.run(thread_handlers.get_thread_state_values(app, "thread-1")) == {
        "messages": []
    }


def test_read_channel_values_from_checkpointer_success_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTuple:
        checkpoint = {"channel_values": {"messages": ["local"]}}

    class FakeSaver:
        @classmethod
        def from_conn_string(cls, db_path: str) -> object:
            assert db_path == "/tmp/checkpoints.sqlite"
            return cls()

        async def __aenter__(self) -> FakeSaver:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def aget_tuple(self, _config: object) -> object:
            return FakeTuple()

    monkeypatch.setitem(
        sys.modules,
        "langgraph.checkpoint.sqlite.aio",
        SimpleNamespace(AsyncSqliteSaver=FakeSaver),
    )
    monkeypatch.setattr(
        "invincat_cli.sessions.get_db_path", lambda: "/tmp/checkpoints.sqlite"
    )

    assert asyncio.run(thread_handlers.read_channel_values_from_checkpointer("t1")) == {
        "messages": ["local"]
    }

    class OSErrorSaver(FakeSaver):
        @classmethod
        def from_conn_string(cls, _db_path: str) -> object:
            raise OSError("no db")

    monkeypatch.setitem(
        sys.modules,
        "langgraph.checkpoint.sqlite.aio",
        SimpleNamespace(AsyncSqliteSaver=OSErrorSaver),
    )

    assert (
        asyncio.run(thread_handlers.read_channel_values_from_checkpointer("t1")) == {}
    )

    class BrokenSaver(FakeSaver):
        async def aget_tuple(self, _config: object) -> object:
            raise RuntimeError("broken")

    monkeypatch.setitem(
        sys.modules,
        "langgraph.checkpoint.sqlite.aio",
        SimpleNamespace(AsyncSqliteSaver=BrokenSaver),
    )

    assert (
        asyncio.run(thread_handlers.read_channel_values_from_checkpointer("t1")) == {}
    )


def test_fetch_thread_history_data_converts_state_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ThreadApp()

    async def state_values(_app: ThreadApp, thread_id: str) -> dict[str, object]:
        assert thread_id == "thread-1"
        return {"messages": [{"type": "human", "content": "hello"}]}

    monkeypatch.setattr(thread_handlers, "get_thread_state_values", state_values)

    payload = asyncio.run(thread_handlers.fetch_thread_history_data(app, "thread-1"))

    assert len(payload.messages) == 1
    assert payload.messages[0].type == MessageType.USER


def test_upgrade_thread_message_link_updates_mounted_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Widget:
        def __init__(self) -> None:
            self.parent = object()
            self._content: object | None = None
            self.updated: list[object] = []

        def update(self, content: object) -> None:
            self.updated.append(content)

    widget = Widget()
    content = Content("linked")

    async def build_message(_prefix: str, _thread_id: str) -> Content:
        return content

    monkeypatch.setattr(thread_handlers, "build_thread_message", build_message)

    asyncio.run(
        thread_handlers.upgrade_thread_message_link(
            widget,  # type: ignore[arg-type]
            prefix="Resumed thread",
            thread_id="thread-1",
        )
    )

    assert widget._content is content
    assert widget.updated == [content]

    widget.parent = None
    widget.updated.clear()

    asyncio.run(
        thread_handlers.upgrade_thread_message_link(
            widget,  # type: ignore[arg-type]
            prefix="Resumed thread",
            thread_id="thread-1",
        )
    )

    assert widget.updated == []


def test_upgrade_thread_message_link_skips_unresolved_and_logs_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Widget:
        parent = object()
        _content: object | None = None

        def update(self, _content: object) -> None:
            raise AssertionError("should not update")

    async def unresolved_message(_prefix: str, _thread_id: str) -> str:
        return "plain"

    monkeypatch.setattr(thread_handlers, "build_thread_message", unresolved_message)

    asyncio.run(
        thread_handlers.upgrade_thread_message_link(
            Widget(),  # type: ignore[arg-type]
            prefix="Resumed thread",
            thread_id="thread-1",
        )
    )

    async def fail_message(_prefix: str, _thread_id: str) -> Content:
        raise RuntimeError("link failed")

    monkeypatch.setattr(thread_handlers, "build_thread_message", fail_message)

    asyncio.run(
        thread_handlers.upgrade_thread_message_link(
            Widget(),  # type: ignore[arg-type]
            prefix="Resumed thread",
            thread_id="thread-1",
        )
    )


def test_schedule_thread_message_link_uses_background_worker() -> None:
    app = ThreadApp()
    widget = AppMessage("thread")

    thread_handlers.schedule_thread_message_link(
        app,
        widget,
        prefix="Resumed thread",
        thread_id="thread-1",
    )

    assert len(app.workers) == 1
    assert app.workers[0][1] is False


def test_load_thread_history_mounts_visible_messages_and_resume_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ThreadApp()
    payload = ThreadHistoryPayload([user_message("first")], context_tokens=123)
    linked: list[tuple[str, str]] = []

    def schedule_link(
        _app: ThreadApp,
        _widget: AppMessage,
        *,
        prefix: str,
        thread_id: str,
    ) -> None:
        linked.append((prefix, thread_id))

    monkeypatch.setattr(thread_handlers, "schedule_thread_message_link", schedule_link)

    asyncio.run(
        thread_handlers.load_thread_history(
            app,
            thread_id="thread-1",
            preloaded_payload=payload,
        )
    )

    assert app.token_updates == [123]
    assert app._status_bar.count == 1
    assert len(app.messages_container.mounted) == 1
    assert app.messages
    assert linked == [("Resumed thread", "thread-1")]
    assert app.chat.scrolled is True


def test_load_thread_history_logs_assistant_render_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ThreadApp()
    payload = ThreadHistoryPayload([user_message("ignored")], context_tokens=0)

    class FakeAssistant:
        async def set_content(self, _content: str) -> None:
            raise RuntimeError("render failed")

    class FakeMessageData:
        content = "assistant text"

        def to_widget(self) -> FakeAssistant:
            return FakeAssistant()

    class FakeMessageStore:
        total_count = 1

        def bulk_load(self, _messages: object) -> tuple[list[object], list[object]]:
            return ([], [FakeMessageData()])

    app._message_store = FakeMessageStore()  # type: ignore[assignment]
    monkeypatch.setattr(thread_handlers, "AssistantMessage", FakeAssistant)
    monkeypatch.setattr(
        thread_handlers,
        "build_resume_summary",
        lambda _messages, _tokens: "",
    )
    monkeypatch.setattr(
        thread_handlers,
        "schedule_thread_message_link",
        lambda *_args, **_kwargs: None,
    )

    asyncio.run(
        thread_handlers.load_thread_history(
            app,
            thread_id="thread-1",
            preloaded_payload=payload,
        )
    )

    assert len(app.messages_container.mounted) == 1
    assert app.messages


def test_load_thread_history_skips_without_thread_agent_or_messages() -> None:
    app = ThreadApp()
    app._lc_thread_id = None

    asyncio.run(thread_handlers.load_thread_history(app))

    assert app.messages == []

    app = ThreadApp()
    app._agent = None

    asyncio.run(thread_handlers.load_thread_history(app, thread_id="thread-1"))

    assert app.messages == []

    app = ThreadApp()
    payload = ThreadHistoryPayload([], context_tokens=0)

    asyncio.run(
        thread_handlers.load_thread_history(
            app,
            thread_id="thread-1",
            preloaded_payload=payload,
        )
    )

    assert app.messages == []


def test_load_thread_history_returns_when_messages_container_missing() -> None:
    app = ThreadApp()

    def missing_messages(selector: str, *_args: object) -> object:
        if selector == "#messages":
            raise thread_handlers.NoMatches(selector)
        return app.chat

    app.query_one = missing_messages  # type: ignore[method-assign]
    payload = ThreadHistoryPayload([user_message("first")], context_tokens=0)

    asyncio.run(
        thread_handlers.load_thread_history(
            app,
            thread_id="thread-1",
            preloaded_payload=payload,
        )
    )

    assert app.messages == []


def test_load_thread_history_reports_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ThreadApp()

    async def fail_fetch(_app: ThreadApp, _thread_id: str) -> ThreadHistoryPayload:
        raise RuntimeError("history failed")

    monkeypatch.setattr(thread_handlers, "fetch_thread_history_data", fail_fetch)

    asyncio.run(thread_handlers.load_thread_history(app, thread_id="thread-1"))

    assert isinstance(app.messages[-1], AppMessage)
    assert "history failed" in app.messages[-1]._content


def test_reset_thread_conversation_view_clears_pending_ui_state() -> None:
    app = ThreadApp()

    asyncio.run(thread_handlers.reset_thread_conversation_view(app))

    assert not app._pending_messages
    assert not app._queued_widgets
    assert app.cleared == 1
    assert app._context_tokens == 0
    assert app._tokens_approximate is False
    assert app.updated_tokens == [0]
    assert app.statuses == [""]


def test_apply_and_rollback_thread_switch_ids_update_state_and_banner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ThreadApp()
    patch_banner(monkeypatch)

    thread_handlers.apply_thread_switch_ids(app, "new-thread")

    assert app._session_state.thread_id == "new-thread"
    assert app._lc_thread_id == "new-thread"
    assert app.banner_updates[-1][0] == "new-thread"

    snapshot = ThreadSwitchSnapshot(
        lc_thread_id="old-lc",
        session_thread_id="old-session",
    )
    thread_handlers.rollback_thread_switch_ids(app, snapshot)

    assert app._session_state.thread_id == "old-session"
    assert app._lc_thread_id == "old-lc"
    assert app.banner_updates[-1][0] == "old-session"
    assert app.banner_updates[-1][2] is True


def test_apply_and_rollback_thread_switch_ids_sync_goal_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    app = ThreadApp()
    app._goal_store = GoalStore(tmp_path)
    patch_banner(monkeypatch)
    old_goal = GoalState.create(objective="Old goal", thread_id="old-thread")
    new_goal = GoalState.create(objective="New goal", thread_id="new-thread")
    app._goal_store.save(old_goal)
    app._goal_store.save(new_goal)
    app._session_state.goal = old_goal
    app._session_state.goal_mode = True

    thread_handlers.apply_thread_switch_ids(app, "new-thread")

    assert app._session_state.thread_id == "new-thread"
    assert app._session_state.goal == new_goal
    assert app._session_state.goal_mode is True
    assert app._status_bar.goal_modes[-1] is True

    snapshot = ThreadSwitchSnapshot(
        lc_thread_id="old-thread",
        session_thread_id="old-thread",
    )
    thread_handlers.rollback_thread_switch_ids(app, snapshot)

    assert app._session_state.thread_id == "old-thread"
    assert app._session_state.goal == old_goal
    assert app._session_state.goal_mode is True


def test_apply_thread_switch_ids_clears_goal_for_thread_without_active_goal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    app = ThreadApp()
    app._goal_store = GoalStore(tmp_path)
    patch_banner(monkeypatch)
    app._session_state.goal = GoalState.create(
        objective="Old goal",
        thread_id="old-thread",
    )
    app._session_state.goal_mode = True

    thread_handlers.apply_thread_switch_ids(app, "new-thread")

    assert app._session_state.goal is None
    assert app._session_state.goal_mode is False
    assert app._status_bar.goal_modes[-1] is False


def test_restore_previous_thread_after_failed_switch_handles_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ThreadApp()
    snapshot = ThreadSwitchSnapshot(
        lc_thread_id="old-lc",
        session_thread_id="old-session",
    )
    loaded: list[str | None] = []

    async def load_success(
        _app: ThreadApp,
        *,
        thread_id: str | None = None,
        preloaded_payload: ThreadHistoryPayload | None = None,
    ) -> None:
        loaded.append(thread_id)

    monkeypatch.setattr(thread_handlers, "load_thread_history", load_success)

    assert (
        asyncio.run(
            thread_handlers.restore_previous_thread_after_failed_switch(
                app,
                snapshot=snapshot,
                failed_thread_id="new-thread",
            )
        )
        is True
    )
    assert loaded == ["old-session"]

    async def load_failure(
        _app: ThreadApp,
        *,
        thread_id: str | None = None,
        preloaded_payload: ThreadHistoryPayload | None = None,
    ) -> None:
        raise RuntimeError("restore failed")

    monkeypatch.setattr(thread_handlers, "load_thread_history", load_failure)

    assert (
        asyncio.run(
            thread_handlers.restore_previous_thread_after_failed_switch(
                app,
                snapshot=snapshot,
                failed_thread_id="new-thread",
            )
        )
        is False
    )


def test_resume_thread_switches_to_prefetched_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ThreadApp()
    patch_banner(monkeypatch)
    payload = ThreadHistoryPayload([user_message("new")], context_tokens=0)
    loaded: list[tuple[str | None, ThreadHistoryPayload | None]] = []

    async def fetch(_app: ThreadApp, thread_id: str) -> ThreadHistoryPayload:
        assert thread_id == "new-thread"
        return payload

    async def load(
        _app: ThreadApp,
        *,
        thread_id: str | None = None,
        preloaded_payload: ThreadHistoryPayload | None = None,
    ) -> None:
        loaded.append((thread_id, preloaded_payload))

    monkeypatch.setattr(thread_handlers, "fetch_thread_history_data", fetch)
    monkeypatch.setattr(thread_handlers, "load_thread_history", load)

    asyncio.run(thread_handlers.resume_thread(app, "new-thread"))

    assert app._session_state.thread_id == "new-thread"
    assert app._lc_thread_id == "new-thread"
    assert loaded == [("new-thread", payload)]
    assert app._thread_switching is False
    assert app.statuses == ["Loading thread: new-thread", "", ""]
    assert app._chat_input.cursor_states == [False, True]


def test_resume_thread_reports_prefetch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ThreadApp()

    async def fetch(_app: ThreadApp, _thread_id: str) -> ThreadHistoryPayload:
        raise RuntimeError("fetch failed")

    monkeypatch.setattr(thread_handlers, "fetch_thread_history_data", fetch)

    asyncio.run(thread_handlers.resume_thread(app, "new-thread"))

    assert app._session_state.thread_id == "old-thread"
    assert app.messages
    assert "fetch failed" in app.messages[-1]._content
    assert app.rolled_back == []
    assert app._thread_switching is False


def test_resume_thread_rolls_back_after_load_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ThreadApp()
    patch_banner(monkeypatch)

    async def fetch(_app: ThreadApp, _thread_id: str) -> ThreadHistoryPayload:
        return ThreadHistoryPayload([user_message("new")], context_tokens=0)

    async def load(
        _app: ThreadApp,
        *,
        thread_id: str | None = None,
        preloaded_payload: ThreadHistoryPayload | None = None,
    ) -> None:
        raise RuntimeError("load failed")

    monkeypatch.setattr(thread_handlers, "fetch_thread_history_data", fetch)
    monkeypatch.setattr(thread_handlers, "load_thread_history", load)

    asyncio.run(thread_handlers.resume_thread(app, "new-thread"))

    assert app._session_state.thread_id == "old-thread"
    assert app._lc_thread_id == "old-lc-thread"
    assert app.rolled_back
    assert app.restored[0][1] == "new-thread"
    assert "load failed" in app.messages[-1]._content


def test_resume_thread_mounts_block_reason_message() -> None:
    app = ThreadApp()
    app._agent = None

    asyncio.run(thread_handlers.resume_thread(app, "new-thread"))

    assert app.messages
    assert app._thread_switching is False


def test_resume_thread_blocks_current_thread_and_existing_switch() -> None:
    app = ThreadApp()

    asyncio.run(thread_handlers.resume_thread(app, "old-thread"))

    assert app.messages
    assert app._thread_switching is False

    app = ThreadApp()
    app._thread_switching = True

    asyncio.run(thread_handlers.resume_thread(app, "new-thread"))

    assert app.messages
    assert app._thread_switching is True
