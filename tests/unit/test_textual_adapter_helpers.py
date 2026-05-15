from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from rich.console import Console

from invincat_cli.core.session_stats import SessionStats
from invincat_cli.textual_adapter import (
    TextualUIAdapter,
    _build_interrupted_ai_message,
    _flush_assistant_text_ns,
    _get_approve_plan_adapter,
    _get_ask_user_adapter,
    _get_hitl_request_adapter,
    _handle_interrupt_cleanup,
    _is_internal_model_chunk,
    _is_summarization_chunk,
    _is_transient_stream_error,
    _normalize_tool_id,
    _persist_context_tokens,
    _read_mentioned_file,
    _report_and_persist_tokens,
    execute_task_textual,
    print_usage_table,
)


class _CacheModel(BaseModel):
    value: str


@dataclass
class _ToolData:
    status: str | None = None
    output: str | None = None


class _MessageStore:
    def __init__(self, data: _ToolData | None) -> None:
        self.data = data
        self.updated: list[tuple[str, dict[str, object]]] = []

    def get_message_by_tool_call_id(self, tool_call_id: str | int) -> _ToolData | None:
        self.last_lookup = tool_call_id
        if self.data is None:
            return None
        self.data.id = "msg-1"  # type: ignore[attr-defined]
        return self.data

    def update_message(self, message_id: str, **kwargs: object) -> None:
        self.updated.append((message_id, kwargs))


class _ToolWidget:
    def __init__(self, name: str = "shell") -> None:
        self._tool_name = name
        self._args = {"command": "pwd"}
        self.errors: list[str] = []
        self.rejected = False

    def set_error(self, error: str) -> None:
        self.errors.append(error)

    def set_rejected(self) -> None:
        self.rejected = True


def test_type_adapters_are_cached() -> None:
    first = _get_hitl_request_adapter(_CacheModel)
    second = _get_hitl_request_adapter(_CacheModel)
    ask_first = _get_ask_user_adapter()
    ask_second = _get_ask_user_adapter()
    approve_first = _get_approve_plan_adapter()
    approve_second = _get_approve_plan_adapter()

    assert first is second
    assert ask_first is ask_second
    assert approve_first is approve_second
    assert first.validate_python({"value": "ok"}).value == "ok"


def test_usage_table_prints_multi_model_and_wall_time() -> None:
    stats = SessionStats()
    stats.record_request("model-a", 1200, 300)
    stats.record_request("model-b", 10, 20)
    console = Console(record=True, width=100)

    print_usage_table(stats, 2.5, console)
    output = console.export_text()

    assert "Usage Stats" in output
    assert "model-a" in output
    assert "Total" in output
    assert "Agent active" in output

    empty = Console(record=True)
    print_usage_table(SessionStats(), 0.01, empty)
    assert empty.export_text() == ""

    single = SessionStats()
    single.record_request("model-one", 1, 2)
    single_console = Console(record=True, width=80)
    print_usage_table(single, 0.0, single_console)
    single_output = single_console.export_text()
    assert "model-one" in single_output
    assert "Total" not in single_output

    time_only = Console(record=True)
    print_usage_table(SessionStats(), 0.2, time_only)
    assert "Agent active" in time_only.export_text()


def test_chunk_classification_id_normalization_and_transient_errors() -> None:
    assert _is_summarization_chunk({"lc_source": "summarization"})
    assert not _is_summarization_chunk(None)
    assert _is_internal_model_chunk({"lc_source": "memory_agent"})
    assert not _is_internal_model_chunk(None)
    assert not _is_internal_model_chunk({"lc_source": "other"})
    assert _normalize_tool_id(123) == "123"
    assert _normalize_tool_id(None) is None

    assert _is_transient_stream_error(RuntimeError("HTTP 429 rate limit"))
    assert _is_transient_stream_error(TimeoutError("timed out"))
    assert _is_transient_stream_error(ConnectionError("reset by peer"))
    assert not _is_transient_stream_error(ValueError("bad input"))


def test_textual_ui_adapter_updates_store_and_finalizes_pending_tools() -> None:
    active: list[object] = []
    adapter = TextualUIAdapter(
        mount_message=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        set_active_message=lambda value: active.append(value),
    )

    store = _MessageStore(_ToolData())
    adapter.set_message_store(store)
    assert adapter._update_tool_message_in_store("call-1", "success", "done")
    assert store.last_lookup == "call-1"
    assert store.updated[0][0] == "msg-1"
    assert store.updated[0][1]["tool_output"] == "done"

    assert not adapter._update_tool_message_in_store("call-1", "future", "done")
    store_without_data = _MessageStore(None)
    adapter.set_message_store(store_without_data)
    assert not adapter._update_tool_message_in_store("missing", "success", "done")
    missing = TextualUIAdapter(
        mount_message=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
    )
    assert not missing._update_tool_message_in_store(1, "success", "done")

    first = _ToolWidget()
    second = _ToolWidget("read_file")
    adapter._current_tool_messages = {"1": first, "2": second}  # type: ignore[assignment,dict-item]
    adapter.finalize_pending_tools_with_error("boom")

    assert first.errors == ["boom"]
    assert second.errors == ["boom"]
    assert adapter._current_tool_messages == {}
    assert active == [None]


def test_build_interrupted_ai_message_from_text_and_tools() -> None:
    assert _build_interrupted_ai_message({}, {}) is None

    message = _build_interrupted_ai_message(
        {(): " hello "},
        {"call-1": _ToolWidget()},
    )

    assert message is not None
    assert message.content == "hello"
    assert message.tool_calls == [
        {
            "name": "shell",
            "args": {"command": "pwd"},
            "id": "call-1",
            "type": "tool_call",
        }
    ]

    tool_only = _build_interrupted_ai_message({}, {"call-2": _ToolWidget("grep")})
    assert tool_only is not None
    assert tool_only.content == ""
    assert tool_only.tool_calls[0]["name"] == "grep"


def test_read_mentioned_file_embeds_small_and_references_large(tmp_path: Path) -> None:
    small = tmp_path / "small.txt"
    small.write_text("hello", encoding="utf-8")
    large = tmp_path / "large.txt"
    large.write_text("x" * 2048, encoding="utf-8")

    embedded = _read_mentioned_file(small, max_embed_bytes=100)
    referenced = _read_mentioned_file(large, max_embed_bytes=100)

    assert "```" in embedded
    assert "hello" in embedded
    assert "too large to embed" in referenced
    assert f"Path: `{large}`" in referenced


def test_read_mentioned_file_propagates_read_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"

    with pytest.raises(OSError):
        _read_mentioned_file(missing, max_embed_bytes=100)


def test_flush_assistant_text_creates_and_finalizes_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    mounted: list[object] = []
    synced: list[tuple[str, str]] = []
    active: list[str | None] = []

    class FakeAssistantMessage:
        def __init__(self, content: str = "", *, id: str) -> None:
            self.id = id
            self._content = content
            self.initial_written = False
            self.stopped = False

        async def write_initial_content(self) -> None:
            self.initial_written = True

        async def stop_stream(self) -> None:
            self.stopped = True

    async def mount_message(message: object) -> None:
        mounted.append(message)

    adapter = TextualUIAdapter(
        mount_message=mount_message,
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        set_active_message=lambda value: active.append(value),
        sync_message_content=lambda message_id, content: synced.append(
            (message_id, content)
        ),
    )
    monkeypatch.setattr(adapter_mod, "AssistantMessage", FakeAssistantMessage)

    messages: dict[tuple, object] = {}
    asyncio.run(_flush_assistant_text_ns(adapter, "  ", (), messages))
    assert mounted == []

    asyncio.run(_flush_assistant_text_ns(adapter, "hello", (), messages))
    created = mounted[0]
    assert isinstance(created, FakeAssistantMessage)
    assert created.initial_written
    assert synced == [(created.id, "hello")]
    assert active == [None]

    asyncio.run(_flush_assistant_text_ns(adapter, "hello", (), messages))
    assert created.stopped
    assert synced[-1] == (created.id, "hello")


def test_persist_context_tokens_retries_and_ensures_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ConnectError(Exception):
        pass

    sleeps: list[float] = []

    def sleep_stub(delay: float):
        sleeps.append(delay)
        return _completed()

    monkeypatch.setattr(
        "invincat_cli.textual_adapter.asyncio.sleep",
        sleep_stub,
    )

    class Agent:
        def __init__(self) -> None:
            self.calls = 0
            self.ensured = 0
            self.updates: list[dict[str, int]] = []

        async def aupdate_state(self, _config: dict, update: dict[str, int]) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("missing thread")
            self.updates.append(update)

        async def aensure_thread(self, _config: dict) -> None:
            self.ensured += 1

    agent = Agent()
    asyncio.run(_persist_context_tokens(agent, {"configurable": {}}, 42))
    assert agent.ensured == 1
    assert agent.updates == [{"_context_tokens": 42}]

    class RetryAgent:
        def __init__(self) -> None:
            self.calls = 0

        async def aupdate_state(self, _config: dict, _update: dict[str, int]) -> None:
            self.calls += 1
            if self.calls < 3:
                raise ConnectError("connection refused")

    retry_agent = RetryAgent()
    asyncio.run(_persist_context_tokens(retry_agent, {}, 7))
    assert retry_agent.calls == 3
    assert sleeps == [0.2, 0.4]

    class FailingAgent:
        async def aupdate_state(self, _config: dict, _update: dict[str, int]) -> None:
            raise ValueError("bad state")

    asyncio.run(_persist_context_tokens(FailingAgent(), {}, 1))

    class EnsureFailingAgent:
        async def aupdate_state(self, _config: dict, _update: dict[str, int]) -> None:
            raise ValueError("bad state")

        async def aensure_thread(self, _config: dict) -> None:
            raise RuntimeError("ensure failed")

    asyncio.run(_persist_context_tokens(EnsureFailingAgent(), {}, 2))

    class CancelAgent:
        async def aupdate_state(self, _config: dict, _update: dict[str, int]) -> None:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_persist_context_tokens(CancelAgent(), {}, 3))

    class EnsureCancelAgent:
        async def aupdate_state(self, _config: dict, _update: dict[str, int]) -> None:
            raise ValueError("missing thread")

        async def aensure_thread(self, _config: dict) -> None:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_persist_context_tokens(EnsureCancelAgent(), {}, 4))


def test_report_and_interrupt_cleanup_persist_tokens_and_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted: list[tuple[int, bool]] = []

    async def fake_persist(_agent: object, _config: dict, tokens: int) -> None:
        persisted.append((tokens, False))

    monkeypatch.setattr(
        "invincat_cli.textual_adapter._persist_context_tokens",
        fake_persist,
    )
    token_updates: list[tuple[int, bool]] = []
    token_shows: list[bool] = []
    adapter = TextualUIAdapter(
        mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
    )
    adapter._on_tokens_update = lambda count, *, approximate=False: (
        token_updates.append((count, approximate))
    )
    adapter._on_tokens_show = lambda *, approximate=False: token_shows.append(
        approximate
    )

    asyncio.run(_report_and_persist_tokens(adapter, object(), {}, 10, 2))
    assert token_updates == [(10, False)]
    assert persisted == [(10, False)]

    asyncio.run(
        _report_and_persist_tokens(adapter, object(), {}, 0, 0, approximate=True)
    )
    assert token_shows == [True]

    async def failing_persist(_agent: object, _config: dict, _tokens: int) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "invincat_cli.textual_adapter._persist_context_tokens",
        failing_persist,
    )
    asyncio.run(
        _report_and_persist_tokens(
            adapter,
            object(),
            {},
            8,
            0,
            shield=True,
            approximate=True,
        )
    )
    assert token_updates[-1] == (8, True)

    mounted: list[object] = []
    spinners: list[object] = []
    active: list[str | None] = []

    async def mount_message(message: object) -> None:
        mounted.append(message)

    async def set_spinner(value: object) -> None:
        spinners.append(value)

    cleanup_adapter = TextualUIAdapter(
        mount_message=mount_message,
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        set_spinner=set_spinner,
        set_active_message=lambda value: active.append(value),
    )
    tool = _ToolWidget()
    cleanup_adapter._current_tool_messages = {"tool-1": tool}  # type: ignore[assignment,dict-item]

    class CleanupAgent:
        def __init__(self) -> None:
            self.updates: list[dict[str, object]] = []

        async def aupdate_state(self, _config: dict, update: dict[str, object]) -> None:
            self.updates.append(update)

    cleanup_agent = CleanupAgent()
    asyncio.run(
        _handle_interrupt_cleanup(
            adapter=cleanup_adapter,
            agent=cleanup_agent,
            config={"configurable": {"thread_id": "thread-1"}},
            pending_text_by_namespace={(): "partial"},
            captured_input_tokens=5,
            captured_output_tokens=0,
            turn_stats=SessionStats(),
            start_time=0,
        )
    )

    assert active == [None]
    assert tool.rejected
    assert cleanup_adapter._current_tool_messages == {}
    assert spinners == [None]
    assert mounted
    assert len(cleanup_agent.updates) == 2


async def _completed() -> None:
    return None


def test_execute_task_textual_streams_text_and_retries_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    mounted: list[object] = []
    spinners: list[object] = []
    active: list[str | None] = []
    synced: list[tuple[str, str]] = []
    deltas: list[tuple[str, str]] = []
    hooks: list[tuple[str, dict[str, object]]] = []

    class FakeAssistantMessage:
        def __init__(self, content: str = "", *, id: str) -> None:
            self.id = id
            self._content = content
            self.appended: list[str] = []
            self.stopped = False

        async def append_content(self, text: str) -> None:
            self.appended.append(text)
            self._content += text

        async def write_initial_content(self) -> None:
            return None

        async def stop_stream(self) -> None:
            self.stopped = True

    class FakeChunk:
        def __init__(
            self, blocks: list[dict[str, object]], *, last: bool = False
        ) -> None:
            self.content_blocks = blocks
            if last:
                self.chunk_position = "last"

    class FakeAgent:
        def __init__(self) -> None:
            self.calls = 0
            self.inputs: list[object] = []

        async def astream(self, stream_input: object, **_kwargs: object):
            self.calls += 1
            self.inputs.append(stream_input)
            if self.calls == 1:
                raise RuntimeError("HTTP 503 unavailable")
            yield (
                (),
                "messages",
                (FakeChunk([{"type": "text", "text": "hello"}], last=True), {}),
            )

    async def mount_message(message: object) -> None:
        mounted.append(message)

    async def set_spinner(value: object) -> None:
        spinners.append(value)

    async def dispatch(name: str, payload: dict[str, object]) -> None:
        hooks.append((name, payload))

    monkeypatch.setattr(adapter_mod, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    monkeypatch.setattr(adapter_mod.asyncio, "sleep", lambda _delay: _completed())

    adapter = TextualUIAdapter(
        mount_message=mount_message,
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        set_spinner=set_spinner,
        set_active_message=lambda value: active.append(value),
        sync_message_content=lambda message_id, content: synced.append(
            (message_id, content)
        ),
    )
    stats = asyncio.run(
        execute_task_textual(
            "hi",
            FakeAgent(),
            "agent",
            SimpleNamespace(thread_id="thread-1", plan_mode=False),
            adapter,
            on_text_delta=lambda text, accumulated: _record_delta(
                deltas, text, accumulated
            ),
        )
    )

    retry_messages = [
        message for message in mounted if type(message).__name__ == "AppMessage"
    ]
    assistant_messages = [
        message for message in mounted if isinstance(message, FakeAssistantMessage)
    ]
    assert len(retry_messages) == 1
    assert len(assistant_messages) == 1
    assert assistant_messages[0]._content == "hello"
    assert assistant_messages[0].stopped
    assert deltas == [("hello", "hello")]
    assert synced == [(assistant_messages[0].id, "hello")]
    assert active[-1] is None
    assert hooks == [
        ("session.start", {"thread_id": "thread-1"}),
        ("task.complete", {"thread_id": "thread-1"}),
    ]
    assert stats.wall_time_seconds >= 0
    assert spinners[0] == "Thinking"
    assert None in spinners


def test_execute_task_textual_prepares_files_media_and_skips_noise(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    good = tmp_path / "good.txt"
    bad = tmp_path / "bad.txt"
    good.write_text("hello", encoding="utf-8")
    bad.write_text("nope", encoding="utf-8")
    spinners: list[object] = []
    hooks: list[tuple[str, dict[str, object]]] = []
    hidden_calls: list[None] = []
    token_updates: list[tuple[int, bool]] = []

    class ImageTracker:
        def __init__(self) -> None:
            self.cleared = False

        def get_images(self) -> list[str]:
            return ["image.png"]

        def get_videos(self) -> list[str]:
            return ["video.mp4"]

        def clear(self) -> None:
            self.cleared = True

    class FakeAgent:
        def __init__(self) -> None:
            self.inputs: list[object] = []

        async def astream(self, stream_input: object, **_kwargs: object):
            self.inputs.append(stream_input)
            yield "not-a-3-tuple"
            yield ((), "custom", {"event": "memory_agent", "status": "running"})
            yield ((), "custom", {"event": "memory_agent", "status": "done"})
            yield ((), "updates", "bad")
            yield ((), "updates", {"node": {"todos": []}})
            yield (("subagent",), "messages", (object(), {}))
            yield ((), "messages", "bad")

    async def set_spinner(value: object) -> None:
        spinners.append(value)

    async def dispatch(name: str, payload: dict[str, object]) -> None:
        hooks.append((name, payload))

    def parse_file_mentions(_text: str) -> tuple[str, list[Path]]:
        return "prompt", [good, bad]

    def read_mentioned_file(path: Path, _limit: int) -> str:
        if path == bad:
            raise OSError("denied")
        return f"embedded:{path.name}"

    def create_multimodal_content(
        text: str, images: list[str], videos: list[str]
    ) -> dict[str, object]:
        return {"text": text, "images": images, "videos": videos}

    monkeypatch.setattr(adapter_mod, "parse_file_mentions", parse_file_mentions)
    monkeypatch.setattr(adapter_mod, "_read_mentioned_file", read_mentioned_file)
    monkeypatch.setattr(
        adapter_mod, "create_multimodal_content", create_multimodal_content
    )
    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)

    image_tracker = ImageTracker()
    agent = FakeAgent()
    adapter = TextualUIAdapter(
        mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        set_spinner=set_spinner,
    )
    adapter._on_tokens_update = lambda count, *, approximate=False: (
        token_updates.append((count, approximate))
    )
    adapter._on_tokens_hide = lambda: hidden_calls.append(None)

    stats = asyncio.run(
        execute_task_textual(
            "ignored",
            agent,
            "agent",
            SimpleNamespace(thread_id="thread-media", plan_mode=False),
            adapter,
            image_tracker=image_tracker,  # type: ignore[arg-type]
            message_kwargs={"name": "user-name"},
        )
    )

    stream_input = agent.inputs[0]
    assert isinstance(stream_input, dict)
    user_msg = stream_input["messages"][0]
    content = user_msg["content"]
    assert content["images"] == ["image.png"]
    assert content["videos"] == ["video.mp4"]
    assert "embedded:good.txt" in content["text"]
    assert "[Error reading file: denied]" in content["text"]
    assert user_msg["name"] == "user-name"
    assert image_tracker.cleared is True
    assert hooks == [
        ("session.start", {"thread_id": "thread-media"}),
        ("task.complete", {"thread_id": "thread-media"}),
    ]
    assert isinstance(spinners[1], str)
    assert "memory" in spinners[1].lower()
    assert stats.wall_time_seconds >= 0
    assert token_updates == []
    assert hidden_calls == [None]


def test_execute_task_textual_unexpected_error_clears_transient_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    spinners: list[object] = []
    active: list[str | None] = []
    mounted_tools: list[object] = []

    class FakeToolCallMessage:
        def __init__(
            self,
            name: str,
            args: dict[str, object],
            *,
            tool_call_id: str,
            args_finalized: bool = False,
        ) -> None:
            self._tool_name = name
            self._args = args
            self._tool_call_id = tool_call_id
            self.args_finalized = args_finalized
            self.errors: list[str] = []
            self.id = None
            mounted_tools.append(self)

        def update_args(self, args: dict[str, object]) -> None:
            self._args = args

        def set_error(self, error: str) -> None:
            self.errors.append(error)

    class FakeChunk:
        content_blocks = [
            {
                "type": "tool_call",
                "name": "shell",
                "args": {"command": "pwd"},
                "id": "tool-1",
            }
        ]

    class ErrorAgent:
        async def astream(self, *_args: object, **_kwargs: object):
            yield ((), "messages", (FakeChunk(), {}))
            raise ValueError("stream failed")

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    async def set_spinner(value: object) -> None:
        spinners.append(value)

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    monkeypatch.setattr(adapter_mod, "ToolCallMessage", FakeToolCallMessage)
    adapter = TextualUIAdapter(
        mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        set_spinner=set_spinner,
        set_active_message=lambda value: active.append(value),
    )

    with pytest.raises(ValueError, match="stream failed"):
        asyncio.run(
            execute_task_textual(
                "hi",
                ErrorAgent(),
                "agent",
                SimpleNamespace(thread_id="thread-error", plan_mode=False),
                adapter,
            )
        )

    assert active == [None]
    assert spinners[-1] is None
    assert mounted_tools[0].errors
    assert adapter._current_tool_messages == {}


def test_execute_task_textual_cancel_runs_interrupt_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    cleanup_calls: list[dict[str, object]] = []

    class CancelAgent:
        async def astream(self, *_args: object, **_kwargs: object):
            raise asyncio.CancelledError
            yield None

    async def cleanup(**kwargs: object) -> None:
        cleanup_calls.append(kwargs)
        turn_stats = kwargs["turn_stats"]
        assert isinstance(turn_stats, SessionStats)
        turn_stats.wall_time_seconds = 12.5

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(adapter_mod, "_handle_interrupt_cleanup", cleanup)
    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    adapter = TextualUIAdapter(
        mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
    )

    stats = asyncio.run(
        execute_task_textual(
            "hi",
            CancelAgent(),
            "agent",
            SimpleNamespace(thread_id="thread-cancel", plan_mode=False),
            adapter,
        )
    )

    assert cleanup_calls
    assert cleanup_calls[0]["pending_text_by_namespace"] == {}
    assert stats.wall_time_seconds == 12.5


def test_execute_task_textual_invalid_hitl_interrupt_aborts_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    mounted: list[object] = []
    spinners: list[object] = []

    class Interrupt:
        id = "interrupt-1"
        value = {"not": "a valid HITL request"}

    class InterruptAgent:
        async def astream(self, *_args: object, **_kwargs: object):
            yield ((), "updates", {"__interrupt__": [Interrupt()]})

    async def mount_message(message: object) -> None:
        mounted.append(message)

    async def set_spinner(value: object) -> None:
        spinners.append(value)

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    stats = asyncio.run(
        execute_task_textual(
            "hi",
            InterruptAgent(),
            "agent",
            SimpleNamespace(thread_id="thread-hitl", plan_mode=False),
            TextualUIAdapter(
                mount_message=mount_message,
                update_status=lambda _status: None,
                request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
                set_spinner=set_spinner,
            ),
        )
    )

    assert type(mounted[0]).__name__ == "AppMessage"
    assert spinners[-1] is None
    assert stats.request_count == 0


def test_execute_task_textual_ask_user_without_ui_resumes_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    hooks: list[tuple[str, dict[str, object]]] = []

    class AskUserInterrupt:
        id = "ask-1"
        value = {
            "type": "ask_user",
            "questions": [{"question": "Continue?", "type": "text"}],
            "tool_call_id": "tool-ask",
        }

    class AskUserAgent:
        def __init__(self) -> None:
            self.inputs: list[object] = []

        async def astream(self, stream_input: object, **_kwargs: object):
            self.inputs.append(stream_input)
            if len(self.inputs) == 1:
                yield ((), "updates", {"__interrupt__": [AskUserInterrupt()]})

    async def dispatch(name: str, payload: dict[str, object]) -> None:
        hooks.append((name, payload))

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    agent = AskUserAgent()
    stats = asyncio.run(
        execute_task_textual(
            "hi",
            agent,
            "agent",
            SimpleNamespace(thread_id="thread-ask", plan_mode=False),
            TextualUIAdapter(
                mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
                update_status=lambda _status: None,
                request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
            ),
        )
    )

    assert len(agent.inputs) == 2
    assert type(agent.inputs[1]).__name__ == "Command"
    assert ("input.required", {}) in hooks
    assert hooks[-1] == ("task.complete", {"thread_id": "thread-ask"})
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_ask_user_answer_marks_tool_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    hooks: list[tuple[str, dict[str, object]]] = []
    spinners: list[object] = []
    requested_questions: list[object] = []
    mounted_tools: list[object] = []

    class FakeToolCallMessage:
        def __init__(
            self,
            name: str,
            args: dict[str, object],
            *,
            tool_call_id: str,
            args_finalized: bool = False,
        ) -> None:
            self._tool_name = name
            self._args = args
            self._tool_call_id = tool_call_id
            self.args_finalized = args_finalized
            self.successes: list[str] = []
            self.id = None
            mounted_tools.append(self)

        def update_args(self, args: dict[str, object]) -> None:
            self._args = args

        def set_success(self, value: str) -> None:
            self.successes.append(value)

    class ToolChunk:
        content_blocks = [
            {
                "type": "tool_call",
                "name": "ask_user",
                "args": {"questions": []},
                "id": "tool-ask",
            }
        ]

    class AskUserInterrupt:
        id = "ask-1"
        value = {
            "type": "ask_user",
            "questions": [{"question": "Continue?", "type": "text"}],
            "tool_call_id": "tool-ask",
        }

    class AskUserAgent:
        def __init__(self) -> None:
            self.inputs: list[object] = []

        async def astream(self, stream_input: object, **_kwargs: object):
            self.inputs.append(stream_input)
            if len(self.inputs) == 1:
                yield ((), "messages", (ToolChunk(), {}))
                yield ((), "updates", {"__interrupt__": [AskUserInterrupt()]})

    async def request_ask_user(questions: list[object]) -> asyncio.Future[dict]:
        requested_questions.extend(questions)
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        future.set_result({"type": "answered", "answers": ["yes"]})
        return future

    async def dispatch(name: str, payload: dict[str, object]) -> None:
        hooks.append((name, payload))

    async def set_spinner(value: object) -> None:
        spinners.append(value)

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    monkeypatch.setattr(adapter_mod, "ToolCallMessage", FakeToolCallMessage)
    agent = AskUserAgent()
    adapter = TextualUIAdapter(
        mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        set_spinner=set_spinner,
        request_ask_user=request_ask_user,  # type: ignore[arg-type]
    )

    stats = asyncio.run(
        execute_task_textual(
            "hi",
            agent,
            "agent",
            SimpleNamespace(thread_id="thread-ask-success", plan_mode=False),
            adapter,
        )
    )

    assert len(agent.inputs) == 2
    assert type(agent.inputs[1]).__name__ == "Command"
    assert requested_questions == [{"question": "Continue?", "type": "text"}]
    assert mounted_tools[0].successes == ["User answered"]
    assert adapter._current_tool_messages == {}
    assert spinners[-1] is None
    assert ("input.required", {}) in hooks
    assert stats.wall_time_seconds >= 0


@pytest.mark.parametrize(
    "widget_result",
    [
        {"type": "answered", "answers": "not-a-list"},
        {"type": "cancelled"},
        "not-a-dict",
    ],
)
def test_execute_task_textual_ask_user_error_results_resume_with_error(
    monkeypatch: pytest.MonkeyPatch,
    widget_result: object,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    class AskUserInterrupt:
        id = "ask-1"
        value = {
            "type": "ask_user",
            "questions": [{"question": "Continue?", "type": "text"}],
            "tool_call_id": "tool-ask",
        }

    class AskUserAgent:
        def __init__(self) -> None:
            self.inputs: list[object] = []

        async def astream(self, stream_input: object, **_kwargs: object):
            self.inputs.append(stream_input)
            if len(self.inputs) == 1:
                yield ((), "updates", {"__interrupt__": [AskUserInterrupt()]})

    async def request_ask_user(
        _questions: list[object],
    ) -> asyncio.Future[object]:
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        future.set_result(widget_result)
        return future

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    agent = AskUserAgent()
    stats = asyncio.run(
        execute_task_textual(
            "hi",
            agent,
            "agent",
            SimpleNamespace(thread_id="thread-ask-error", plan_mode=False),
            TextualUIAdapter(
                mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
                update_status=lambda _status: None,
                request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
                request_ask_user=request_ask_user,  # type: ignore[arg-type]
            ),
        )
    )

    assert len(agent.inputs) == 2
    assert type(agent.inputs[1]).__name__ == "Command"
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_approve_plan_approved_marks_tool_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    requested_todos: list[dict[str, object]] = []
    mounted_tools: list[object] = []

    class FakeToolCallMessage:
        def __init__(
            self,
            name: str,
            args: dict[str, object],
            *,
            tool_call_id: str,
            args_finalized: bool = False,
        ) -> None:
            self._tool_name = name
            self._args = args
            self._tool_call_id = tool_call_id
            self.args_finalized = args_finalized
            self.successes: list[str] = []
            self.id = None
            mounted_tools.append(self)

        def update_args(self, args: dict[str, object]) -> None:
            self._args = args

        def set_success(self, value: str) -> None:
            self.successes.append(value)

    class ToolChunk:
        content_blocks = [
            {
                "type": "tool_call",
                "name": "approve_plan",
                "args": {"todos": []},
                "id": "tool-plan",
            }
        ]

    class ApprovePlanInterrupt:
        id = "plan-1"
        value = {
            "type": "approve_plan",
            "todos": [{"content": "ship it", "status": "pending"}],
            "tool_call_id": "tool-plan",
        }

    class ApprovePlanAgent:
        def __init__(self) -> None:
            self.inputs: list[object] = []

        async def astream(self, stream_input: object, **_kwargs: object):
            self.inputs.append(stream_input)
            if len(self.inputs) == 1:
                yield ((), "messages", (ToolChunk(), {}))
                yield ((), "updates", {"__interrupt__": [ApprovePlanInterrupt()]})

    async def request_approve_plan(
        todos: list[dict[str, object]],
    ) -> asyncio.Future[dict]:
        requested_todos.extend(todos)
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        future.set_result({"type": "approved"})
        return future

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    monkeypatch.setattr(adapter_mod, "ToolCallMessage", FakeToolCallMessage)
    agent = ApprovePlanAgent()
    adapter = TextualUIAdapter(
        mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        request_approve_plan=request_approve_plan,  # type: ignore[arg-type]
    )

    stats = asyncio.run(
        execute_task_textual(
            "hi",
            agent,
            "agent",
            SimpleNamespace(thread_id="thread-plan-approved", plan_mode=False),
            adapter,
        )
    )

    assert len(agent.inputs) == 2
    assert type(agent.inputs[1]).__name__ == "Command"
    assert requested_todos == [{"content": "ship it", "status": "pending"}]
    assert mounted_tools[0].successes == ["Plan approved"]
    assert adapter._current_tool_messages == {}
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_approve_plan_rejected_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    class ApprovePlanInterrupt:
        id = "plan-1"
        value = {
            "type": "approve_plan",
            "todos": [{"content": "ship it", "status": "pending"}],
            "tool_call_id": "tool-plan",
        }

    class ApprovePlanAgent:
        def __init__(self) -> None:
            self.inputs: list[object] = []

        async def astream(self, stream_input: object, **_kwargs: object):
            self.inputs.append(stream_input)
            if len(self.inputs) == 1:
                yield ((), "updates", {"__interrupt__": [ApprovePlanInterrupt()]})

    async def request_approve_plan(
        _todos: list[dict[str, object]],
    ) -> asyncio.Future[dict]:
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        future.set_result({"type": "rejected"})
        return future

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    agent = ApprovePlanAgent()
    stats = asyncio.run(
        execute_task_textual(
            "hi",
            agent,
            "agent",
            SimpleNamespace(thread_id="thread-plan-rejected", plan_mode=False),
            TextualUIAdapter(
                mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
                update_status=lambda _status: None,
                request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
                request_approve_plan=request_approve_plan,  # type: ignore[arg-type]
            ),
        )
    )

    assert len(agent.inputs) == 2
    assert type(agent.inputs[1]).__name__ == "Command"
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_hitl_approve_marks_tools_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    hooks: list[tuple[str, dict[str, object]]] = []
    approval_requests: list[tuple[list[dict[str, object]], str | None]] = []
    mounted_tools: list[object] = []

    class FakeToolCallMessage:
        def __init__(
            self,
            name: str,
            args: dict[str, object],
            *,
            tool_call_id: str,
            args_finalized: bool = False,
        ) -> None:
            self._tool_name = name
            self._args = args
            self._tool_call_id = tool_call_id
            self.args_finalized = args_finalized
            self.running = 0
            self.rejected = 0
            self.id = None
            mounted_tools.append(self)

        def update_args(self, args: dict[str, object]) -> None:
            self._args = args

        def set_running(self) -> None:
            self.running += 1

        def set_rejected(self) -> None:
            self.rejected += 1

    action_request = {"name": "shell", "args": {"command": "pwd"}}

    class ToolChunk:
        content_blocks = [
            {
                "type": "tool_call",
                "name": "shell",
                "args": {"command": "pwd"},
                "id": "tool-hitl",
            }
        ]

    class HitlInterrupt:
        id = "hitl-1"
        value = {"action_requests": [action_request], "review_configs": []}

    class HitlAgent:
        def __init__(self) -> None:
            self.inputs: list[object] = []

        async def astream(self, stream_input: object, **_kwargs: object):
            self.inputs.append(stream_input)
            if len(self.inputs) == 1:
                yield ((), "messages", (ToolChunk(), {}))
                yield ((), "updates", {"__interrupt__": [HitlInterrupt()]})

    async def request_approval(
        action_requests: list[dict[str, object]],
        assistant_id: str | None,
    ) -> asyncio.Future[dict]:
        approval_requests.append((action_requests, assistant_id))
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        future.set_result({"type": "approve"})
        return future

    async def dispatch(name: str, payload: dict[str, object]) -> None:
        hooks.append((name, payload))

    monkeypatch.setattr(adapter_mod, "_hitl_adapter_cache", None)
    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    monkeypatch.setattr(adapter_mod, "ToolCallMessage", FakeToolCallMessage)
    agent = HitlAgent()
    stats = asyncio.run(
        execute_task_textual(
            "hi",
            agent,
            "agent-1",
            SimpleNamespace(
                thread_id="thread-hitl-approve",
                plan_mode=False,
                auto_approve=False,
            ),
            TextualUIAdapter(
                mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
                update_status=lambda _status: None,
                request_approval=request_approval,  # type: ignore[arg-type]
            ),
        )
    )

    assert len(agent.inputs) == 2
    assert type(agent.inputs[1]).__name__ == "Command"
    assert approval_requests == [([action_request], "agent-1")]
    assert mounted_tools[0].running == 1
    assert mounted_tools[0].rejected == 0
    assert ("permission.request", {"tool_names": ["shell"]}) in hooks
    assert stats.wall_time_seconds >= 0


@pytest.mark.parametrize(
    "decision",
    [
        {"type": "reject"},
        {"type": "unexpected"},
        "not-a-dict",
    ],
)
def test_execute_task_textual_hitl_reject_returns_guidance(
    monkeypatch: pytest.MonkeyPatch,
    decision: object,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    mounted: list[object] = []
    mounted_tools: list[object] = []

    class FakeToolCallMessage:
        def __init__(
            self,
            name: str,
            args: dict[str, object],
            *,
            tool_call_id: str,
            args_finalized: bool = False,
        ) -> None:
            self._tool_name = name
            self._args = args
            self._tool_call_id = tool_call_id
            self.args_finalized = args_finalized
            self.rejected = 0
            self.id = None
            mounted_tools.append(self)

        def update_args(self, args: dict[str, object]) -> None:
            self._args = args

        def set_rejected(self) -> None:
            self.rejected += 1

    class ToolChunk:
        content_blocks = [
            {
                "type": "tool_call",
                "name": "shell",
                "args": {"command": "pwd"},
                "id": "tool-hitl",
            }
        ]

    class HitlInterrupt:
        id = "hitl-1"
        value = {
            "action_requests": [{"name": "shell", "args": {"command": "pwd"}}],
            "review_configs": [],
        }

    class HitlAgent:
        def __init__(self) -> None:
            self.inputs: list[object] = []

        async def astream(self, stream_input: object, **_kwargs: object):
            self.inputs.append(stream_input)
            yield ((), "messages", (ToolChunk(), {}))
            yield ((), "updates", {"__interrupt__": [HitlInterrupt()]})

    async def mount_message(message: object) -> None:
        mounted.append(message)

    async def request_approval(
        _action_requests: list[dict[str, object]],
        _assistant_id: str | None,
    ) -> asyncio.Future[object]:
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        future.set_result(decision)
        return future

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(adapter_mod, "_hitl_adapter_cache", None)
    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    monkeypatch.setattr(adapter_mod, "ToolCallMessage", FakeToolCallMessage)
    agent = HitlAgent()
    stats = asyncio.run(
        execute_task_textual(
            "hi",
            agent,
            "agent-1",
            SimpleNamespace(
                thread_id="thread-hitl-reject",
                plan_mode=False,
                auto_approve=False,
            ),
            TextualUIAdapter(
                mount_message=mount_message,
                update_status=lambda _status: None,
                request_approval=request_approval,  # type: ignore[arg-type]
            ),
        )
    )

    assert len(agent.inputs) == 1
    assert mounted_tools[0].rejected == 1
    assert adapter_mod.AppMessage in {type(message) for message in mounted}
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_hitl_session_auto_approve_skips_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    hooks: list[tuple[str, dict[str, object]]] = []
    mounted_tools: list[object] = []

    class FakeToolCallMessage:
        def __init__(
            self,
            name: str,
            args: dict[str, object],
            *,
            tool_call_id: str,
            args_finalized: bool = False,
        ) -> None:
            self._tool_name = name
            self._args = args
            self._tool_call_id = tool_call_id
            self.args_finalized = args_finalized
            self.running = 0
            self.id = None
            mounted_tools.append(self)

        def update_args(self, args: dict[str, object]) -> None:
            self._args = args

        def set_running(self) -> None:
            self.running += 1

    class HitlAgent:
        def __init__(self) -> None:
            self.inputs: list[object] = []

        async def astream(self, stream_input: object, **_kwargs: object):
            self.inputs.append(stream_input)
            if len(self.inputs) == 1:
                yield (
                    (),
                    "messages",
                    (
                        SimpleNamespace(
                            content_blocks=[
                                {
                                    "type": "tool_call",
                                    "name": "shell",
                                    "args": {"command": "pwd"},
                                    "id": "tool-hitl",
                                }
                            ]
                        ),
                        {},
                    ),
                )
                yield (
                    (),
                    "updates",
                    {
                        "__interrupt__": [
                            SimpleNamespace(
                                id="hitl-1",
                                value={
                                    "action_requests": [
                                        {"name": "shell", "args": {"command": "pwd"}}
                                    ],
                                    "review_configs": [],
                                },
                            )
                        ]
                    },
                )

    async def request_approval(*_args: object) -> object:
        raise AssertionError("auto-approve should not prompt")

    async def dispatch(name: str, payload: dict[str, object]) -> None:
        hooks.append((name, payload))

    monkeypatch.setattr(adapter_mod, "_hitl_adapter_cache", None)
    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    monkeypatch.setattr(adapter_mod, "ToolCallMessage", FakeToolCallMessage)
    agent = HitlAgent()
    stats = asyncio.run(
        execute_task_textual(
            "hi",
            agent,
            "agent-1",
            SimpleNamespace(
                thread_id="thread-hitl-auto",
                plan_mode=False,
                auto_approve=True,
            ),
            TextualUIAdapter(
                mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
                update_status=lambda _status: None,
                request_approval=request_approval,  # type: ignore[arg-type]
            ),
        )
    )

    assert len(agent.inputs) == 2
    assert mounted_tools[0].running == 1
    assert ("permission.request", {"tool_names": ["shell"]}) not in hooks
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_hitl_auto_approve_all_marks_file_ops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    auto_enabled: list[None] = []
    mounted_tools: list[object] = []
    file_trackers: list[object] = []
    write_args = {"path": "demo.txt", "content": "hello"}
    action_request = {"name": "write_file", "args": write_args}

    class FakeFileOpTracker:
        def __init__(self, **_kwargs: object) -> None:
            self.active: dict[str, object] = {}
            self.started: list[tuple[str, dict[str, object], str]] = []
            self.approved: list[tuple[str, dict[str, object]]] = []
            file_trackers.append(self)

        def start_operation(
            self, tool_name: str, args: dict[str, object], tool_call_id: str
        ) -> None:
            self.started.append((tool_name, args, tool_call_id))
            self.active[tool_call_id] = SimpleNamespace(tool_call_id=tool_call_id)

        def mark_hitl_approved(self, tool_name: str, args: dict[str, object]) -> None:
            self.approved.append((tool_name, args))

        def complete_with_message(self, *_args: object) -> None:
            return None

    class FakeToolCallMessage:
        def __init__(
            self,
            name: str,
            args: dict[str, object],
            *,
            tool_call_id: str,
            args_finalized: bool = False,
        ) -> None:
            self._tool_name = name
            self._args = args
            self._tool_call_id = tool_call_id
            self.args_finalized = args_finalized
            self.running = 0
            self.id = None
            mounted_tools.append(self)

        def update_args(self, args: dict[str, object]) -> None:
            self._args = args

        def set_running(self) -> None:
            self.running += 1

    class HitlAgent:
        def __init__(self) -> None:
            self.inputs: list[object] = []

        async def astream(self, stream_input: object, **_kwargs: object):
            self.inputs.append(stream_input)
            if len(self.inputs) == 1:
                yield (
                    (),
                    "messages",
                    (
                        SimpleNamespace(
                            content_blocks=[
                                {
                                    "type": "tool_call",
                                    "name": "write_file",
                                    "args": write_args,
                                    "id": "tool-write",
                                }
                            ]
                        ),
                        {},
                    ),
                )
                yield (
                    (),
                    "updates",
                    {
                        "__interrupt__": [
                            SimpleNamespace(
                                id="hitl-1",
                                value={
                                    "action_requests": [action_request],
                                    "review_configs": [],
                                },
                            )
                        ]
                    },
                )

    async def request_approval(
        _action_requests: list[dict[str, object]],
        _assistant_id: str | None,
    ) -> asyncio.Future[dict]:
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        future.set_result({"type": "auto_approve_all"})
        return future

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(adapter_mod, "_hitl_adapter_cache", None)
    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    monkeypatch.setattr(adapter_mod, "FileOpTracker", FakeFileOpTracker)
    monkeypatch.setattr(adapter_mod, "ToolCallMessage", FakeToolCallMessage)
    session_state = SimpleNamespace(
        thread_id="thread-hitl-auto-all",
        plan_mode=False,
        auto_approve=False,
    )
    agent = HitlAgent()
    stats = asyncio.run(
        execute_task_textual(
            "hi",
            agent,
            "agent-1",
            session_state,
            TextualUIAdapter(
                mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
                update_status=lambda _status: None,
                request_approval=request_approval,  # type: ignore[arg-type]
                on_auto_approve_enabled=lambda: auto_enabled.append(None),
            ),
        )
    )

    tracker = file_trackers[0]
    assert len(agent.inputs) == 2
    assert session_state.auto_approve is True
    assert auto_enabled == [None]
    assert mounted_tools[0].running == 1
    assert tracker.started == [("write_file", write_args, "tool-write")]
    assert tracker.approved == [("write_file", write_args)]
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_tool_message_direct_match_completes_widget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.messages import ToolMessage

    from invincat_cli import textual_adapter as adapter_mod

    hooks: list[tuple[str, dict[str, object]]] = []
    mounted_tools: list[object] = []

    class FakeToolCallMessage:
        def __init__(
            self,
            name: str,
            args: dict[str, object],
            *,
            tool_call_id: str,
            args_finalized: bool = False,
        ) -> None:
            self._tool_name = name
            self._args = args
            self._tool_call_id = tool_call_id
            self.args_finalized = args_finalized
            self.successes: list[str] = []
            self.errors: list[str] = []
            self.id = None
            mounted_tools.append(self)

        def update_args(self, args: dict[str, object]) -> None:
            self._args = args

        def set_success(self, output: str) -> None:
            self.successes.append(output)

        def set_error(self, output: str) -> None:
            self.errors.append(output)

    class ToolResultAgent:
        def __init__(self) -> None:
            self.inputs: list[object] = []

        async def astream(self, stream_input: object, **_kwargs: object):
            self.inputs.append(stream_input)
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(
                        content_blocks=[
                            {
                                "type": "tool_call",
                                "name": "shell",
                                "args": {"command": "pwd"},
                                "id": "tool-1",
                            }
                        ]
                    ),
                    {},
                ),
            )
            yield (
                (),
                "messages",
                (
                    ToolMessage(
                        content="done",
                        name="shell",
                        tool_call_id="tool-1",
                        status="success",
                    ),
                    {},
                ),
            )
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(
                        content_blocks=[
                            {
                                "type": "tool_call",
                                "name": "shell",
                                "args": {"command": "false"},
                                "id": "tool-err",
                            }
                        ]
                    ),
                    {},
                ),
            )
            yield (
                (),
                "messages",
                (
                    ToolMessage(
                        content="failed",
                        name="shell",
                        tool_call_id="tool-err",
                        status="error",
                    ),
                    {},
                ),
            )
            yield (
                (),
                "messages",
                (
                    ToolMessage(
                        content="boom",
                        name="shell",
                        tool_call_id="missing-tool",
                        status="error",
                    ),
                    {},
                ),
            )

    async def dispatch(name: str, payload: dict[str, object]) -> None:
        hooks.append((name, payload))

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    monkeypatch.setattr(adapter_mod, "ToolCallMessage", FakeToolCallMessage)
    adapter = TextualUIAdapter(
        mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
    )

    stats = asyncio.run(
        execute_task_textual(
            "hi",
            ToolResultAgent(),
            "agent",
            SimpleNamespace(thread_id="thread-tool-result", plan_mode=False),
            adapter,
        )
    )

    assert mounted_tools[0].successes == ["done"]
    assert mounted_tools[1].errors == ["failed"]
    assert mounted_tools[2].errors == ["boom"]
    assert adapter._current_tool_messages == {}
    assert ("tool.error", {"tool_names": ["shell"]}) in hooks
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_invalid_ask_and_plan_interrupts_resume_with_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langgraph.types import Command

    from invincat_cli import textual_adapter as adapter_mod

    hooks: list[tuple[str, dict[str, object]]] = []

    class InvalidInterruptAgent:
        def __init__(self) -> None:
            self.inputs: list[object] = []

        async def astream(self, stream_input: object, **_kwargs: object):
            self.inputs.append(stream_input)
            if len(self.inputs) == 1:
                yield (
                    (),
                    "updates",
                    {
                        "__interrupt__": [
                            SimpleNamespace(
                                id="ask-bad",
                                value={"type": "ask_user", "questions": "bad"},
                            ),
                            SimpleNamespace(
                                id="plan-bad",
                                value={"type": "approve_plan", "todos": "bad"},
                            ),
                        ]
                    },
                )

    async def dispatch(name: str, payload: dict[str, object]) -> None:
        hooks.append((name, payload))

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    agent = InvalidInterruptAgent()
    stats = asyncio.run(
        execute_task_textual(
            "hi",
            agent,
            "agent",
            SimpleNamespace(thread_id="thread-invalid-interrupts", plan_mode=True),
            TextualUIAdapter(
                mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
                update_status=lambda _status: None,
                request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
            ),
        )
    )

    assert stats.wall_time_seconds >= 0
    assert isinstance(agent.inputs[1], Command)
    resume_payload = agent.inputs[1].resume
    assert resume_payload["ask-bad"]["status"] == "error"
    assert resume_payload["plan-bad"]["status"] == "error"
    assert hooks == [
        ("session.start", {"thread_id": "thread-invalid-interrupts"}),
        ("task.complete", {"thread_id": "thread-invalid-interrupts"}),
    ]


def test_execute_task_textual_tool_message_matches_widget_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.messages import ToolMessage

    from invincat_cli import textual_adapter as adapter_mod

    mounted_tools: list[object] = []
    store_updates: list[tuple[str, dict[str, object]]] = []

    class FakeStore:
        def update_message(self, message_id: str, **kwargs: object) -> None:
            store_updates.append((message_id, kwargs))

    class FakeToolCallMessage:
        def __init__(
            self,
            name: str,
            args: dict[str, object],
            *,
            tool_call_id: str,
            args_finalized: bool = False,
        ) -> None:
            self._tool_name = name
            self._args = args
            # Simulate a widget stored under an index key while its own
            # canonical tool id has already been updated.
            self._tool_call_id = "tool-real"
            self.args_finalized = args_finalized
            self.successes: list[str] = []
            self.id = "msg-tool"
            mounted_tools.append(self)

        def update_args(self, args: dict[str, object]) -> None:
            self._args = args

        def set_success(self, output: str) -> None:
            self.successes.append(output)

    class AttributeMatchAgent:
        async def astream(self, *_args: object, **_kwargs: object):
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(
                        content_blocks=[
                            {
                                "type": "tool_call",
                                "name": "shell",
                                "args": {"command": "pwd"},
                                "index": 0,
                            }
                        ]
                    ),
                    {},
                ),
            )
            yield (
                (),
                "messages",
                (
                    ToolMessage(
                        content="done",
                        name="shell",
                        tool_call_id="tool-real",
                        status="success",
                    ),
                    {},
                ),
            )

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    monkeypatch.setattr(adapter_mod, "ToolCallMessage", FakeToolCallMessage)
    adapter = TextualUIAdapter(
        mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
    )
    adapter.set_message_store(FakeStore())

    stats = asyncio.run(
        execute_task_textual(
            "hi",
            AttributeMatchAgent(),
            "agent",
            SimpleNamespace(thread_id="thread-tool-attr", plan_mode=False),
            adapter,
        )
    )

    assert mounted_tools[0].successes == ["done"]
    assert adapter._current_tool_messages == {}
    assert ("msg-tool", {"tool_call_id": "tool-real"}) in store_updates
    assert any("tool_status" in update for _, update in store_updates)
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_tool_message_matches_by_name_and_rekeys_streamed_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.messages import ToolMessage

    from invincat_cli import textual_adapter as adapter_mod

    mounted: list[object] = []
    mounted_tools: list[object] = []
    store_updates: list[tuple[str, dict[str, object]]] = []
    spinners: list[object] = []

    class FakeStore:
        def update_message(self, message_id: str, **kwargs: object) -> None:
            store_updates.append((message_id, kwargs))

    class FakeFileRecord:
        def __init__(self, tool_call_id: str) -> None:
            self.tool_call_id = tool_call_id
            self.tool_name = "write_file"
            self.args = {"path": "demo.txt"}
            self.status = "success"
            self.error = None
            self.diff = ""
            self.display_path = "demo.txt"

    class FakeFileOpTracker:
        def __init__(self, **_kwargs: object) -> None:
            self.active = {"0": FakeFileRecord("0")}

        def start_operation(
            self, tool_name: str, args: dict[str, object], tool_call_id: str
        ) -> None:
            self.active[tool_call_id] = FakeFileRecord(tool_call_id)
            self.active[tool_call_id].tool_name = tool_name
            self.active[tool_call_id].args = args

        def complete_with_message(
            self, message: object, _tool_args: dict[str, object] | None = None
        ) -> object | None:
            return self.active.pop(getattr(message, "tool_call_id", ""), None)

    class FakeAssistantMessage:
        def __init__(self, content: str = "", *, id: str) -> None:
            self.id = id
            self._content = content
            self.stopped = False

        async def append_content(self, text: str) -> None:
            self._content += text

        async def write_initial_content(self) -> None:
            return None

        async def stop_stream(self) -> None:
            self.stopped = True

    class FakeToolCallMessage:
        def __init__(
            self,
            name: str,
            args: dict[str, object],
            *,
            tool_call_id: str,
            args_finalized: bool = False,
        ) -> None:
            self._tool_name = name
            self._args = args
            self._tool_call_id = tool_call_id
            self._status = "success" if tool_call_id == "0" else "running"
            self.args_finalized = args_finalized
            self.id = f"msg-{tool_call_id}"
            self.successes: list[str] = []
            mounted_tools.append(self)

        def update_args(self, args: dict[str, object]) -> None:
            self._args = args

        def set_success(self, output: str) -> None:
            self._status = "success"
            self.successes.append(output)

        def set_error(self, output: str) -> None:
            self._status = "error"
            self.successes.append(output)

    class NameAndRekeyAgent:
        async def astream(self, *_args: object, **_kwargs: object):
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(
                        content_blocks=[{"type": "text", "text": "before tool"}]
                    ),
                    {},
                ),
            )
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(
                        content_blocks=[
                            {
                                "type": "tool_call_chunk",
                                "name": "write_file",
                                "args": '{"path": "demo.txt"',
                                "index": 0,
                            }
                        ]
                    ),
                    {},
                ),
            )
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(
                        content_blocks=[
                            {
                                "type": "tool_call_chunk",
                                "name": "write_file",
                                "args": ', "content": "hi"}',
                                "id": "write-real",
                                "index": 0,
                            },
                            {
                                "type": "tool_call",
                                "name": "shell",
                                "args": {"command": "first"},
                                "id": "shell-a",
                            },
                            {
                                "type": "tool_call",
                                "name": "shell",
                                "args": {"command": "second"},
                                "id": "shell-b",
                            },
                        ]
                    ),
                    {},
                ),
            )
            yield (
                (),
                "messages",
                (
                    ToolMessage(
                        content="done",
                        name="shell",
                        tool_call_id="",
                        status="success",
                    ),
                    {},
                ),
            )

    async def mount_message(message: object) -> None:
        mounted.append(message)

    async def set_spinner(value: object) -> None:
        spinners.append(value)

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(adapter_mod, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(adapter_mod, "ToolCallMessage", FakeToolCallMessage)
    monkeypatch.setattr(adapter_mod, "FileOpTracker", FakeFileOpTracker)
    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)

    adapter = TextualUIAdapter(
        mount_message=mount_message,
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        set_spinner=set_spinner,
    )
    adapter.set_message_store(FakeStore())

    stats = asyncio.run(
        execute_task_textual(
            "hi",
            NameAndRekeyAgent(),
            "agent",
            SimpleNamespace(thread_id="thread-name-rekey", plan_mode=False),
            adapter,
        )
    )

    assert any(isinstance(message, FakeAssistantMessage) for message in mounted)
    assert any(
        update == ("msg-0", {"tool_call_id": "write-real"}) for update in store_updates
    )
    assert any(tool._tool_call_id == "write-real" for tool in mounted_tools)
    shell_tools = [tool for tool in mounted_tools if tool._tool_name == "shell"]
    assert any(tool.successes == ["done"] for tool in shell_tools)
    assert "write-real" in adapter._current_tool_messages
    assert "0" not in adapter._current_tool_messages
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_content_block_edge_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.messages import HumanMessage

    from invincat_cli import textual_adapter as adapter_mod

    mounted: list[object] = []
    mounted_tools: list[object] = []
    spinners: list[object] = []

    class FakeAssistantMessage:
        def __init__(self, content: str = "", *, id: str) -> None:
            self.id = id
            self._content = content
            self.stopped = False

        async def append_content(self, text: str) -> None:
            self._content += text

        async def write_initial_content(self) -> None:
            return None

        async def stop_stream(self) -> None:
            self.stopped = True

    class FakeToolCallMessage:
        def __init__(
            self,
            name: str,
            args: dict[str, object],
            *,
            tool_call_id: str,
            args_finalized: bool = False,
        ) -> None:
            self._tool_name = name
            self._args = args
            self._tool_call_id = tool_call_id
            self.id = None
            self.errors: list[str] = []
            mounted_tools.append(self)

        def update_args(self, args: dict[str, object]) -> None:
            self._args = args

        def set_error(self, output: str) -> None:
            self.errors.append(output)

    class EdgeBlockAgent:
        async def astream(self, *_args: object, **_kwargs: object):
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(
                        content_blocks=[
                            {"type": "text", "text": "hello"},
                            {"type": "reasoning", "text": "hidden"},
                            {"type": "tool_call", "args": ["no name"]},
                        ]
                    ),
                    {},
                ),
            )
            yield ((), "messages", (HumanMessage(content="user echo"), {}))
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(
                        content_blocks=[
                            {
                                "type": "tool_call",
                                "name": "custom",
                                "args": '"scalar"',
                            }
                        ]
                    ),
                    {},
                ),
            )

    async def mount_message(message: object) -> None:
        mounted.append(message)

    async def set_spinner(value: object) -> None:
        spinners.append(value)

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    async def failing_delta(_text: str, _accumulated: str) -> None:
        raise RuntimeError("delta failed")

    monkeypatch.setattr(adapter_mod, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(adapter_mod, "ToolCallMessage", FakeToolCallMessage)
    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)

    stats = asyncio.run(
        execute_task_textual(
            "hi",
            EdgeBlockAgent(),
            "agent",
            SimpleNamespace(thread_id="thread-edge-blocks", plan_mode=False),
            TextualUIAdapter(
                mount_message=mount_message,
                update_status=lambda _status: None,
                request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
                set_spinner=set_spinner,
            ),
            on_text_delta=failing_delta,
        )
    )

    assert any(isinstance(message, FakeAssistantMessage) for message in mounted)
    assert mounted_tools[0]._args == {"value": "scalar"}
    assert mounted_tools[0]._tool_call_id.startswith("unknown-")
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_plan_mode_blocks_disallowed_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    mounted_tools: list[object] = []

    class FakeToolCallMessage:
        def __init__(
            self,
            name: str,
            args: dict[str, object],
            *,
            tool_call_id: str,
            args_finalized: bool = False,
        ) -> None:
            self._tool_name = name
            self._args = args
            self._tool_call_id = tool_call_id
            self.id = None
            self.errors: list[str] = []
            mounted_tools.append(self)

        def update_args(self, args: dict[str, object]) -> None:
            self._args = args

        def set_error(self, output: str) -> None:
            self.errors.append(output)

    class BlockedToolAgent:
        async def astream(self, *_args: object, **_kwargs: object):
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(
                        content_blocks=[
                            {
                                "type": "tool_call",
                                "name": "shell",
                                "args": {"command": "pwd"},
                                "id": "blocked-1",
                            }
                        ]
                    ),
                    {},
                ),
            )

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(adapter_mod, "ToolCallMessage", FakeToolCallMessage)
    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)

    stats = asyncio.run(
        execute_task_textual(
            "hi",
            BlockedToolAgent(),
            "agent",
            SimpleNamespace(thread_id="thread-plan-block", plan_mode=True),
            TextualUIAdapter(
                mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
                update_status=lambda _status: None,
                request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
            ),
            is_planner_turn=True,
        )
    )

    assert mounted_tools[0].errors
    assert "plan" in mounted_tools[0].errors[0].lower()
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_summary_and_memory_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    spinners: list[object] = []

    class SummaryThenRegularAgent:
        async def astream(self, *_args: object, **_kwargs: object):
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(content_blocks=[{"type": "text", "text": "hide"}]),
                    {"lc_source": "summarization"},
                ),
            )
            yield (
                (),
                "messages",
                (SimpleNamespace(content_blocks=[]), {}),
            )

    class SummaryOnlyAgent:
        async def astream(self, *_args: object, **_kwargs: object):
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(content_blocks=[{"type": "text", "text": "hide"}]),
                    {"lc_source": "summarization"},
                ),
            )

    class MemoryOnlyAgent:
        async def astream(self, *_args: object, **_kwargs: object):
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(content_blocks=[]),
                    {"lc_source": "memory_agent"},
                ),
            )

    async def mount_message(message: object) -> None:
        if type(message).__name__ == "SummarizationMessage":
            raise RuntimeError("mount failed")

    async def set_spinner(value: object) -> None:
        spinners.append(value)

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    adapter = TextualUIAdapter(
        mount_message=mount_message,
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
        set_spinner=set_spinner,
    )

    for agent, thread_id in (
        (SummaryThenRegularAgent(), "thread-summary-regular"),
        (SummaryOnlyAgent(), "thread-summary-end"),
        (MemoryOnlyAgent(), "thread-memory-end"),
    ):
        stats = asyncio.run(
            execute_task_textual(
                "hi",
                agent,
                "agent",
                SimpleNamespace(thread_id=thread_id, plan_mode=False),
                adapter,
            )
        )
        assert stats.wall_time_seconds >= 0

    assert any("offload" in str(value).lower() for value in spinners)
    assert any("memory" in str(value).lower() for value in spinners)
    assert spinners[-1] == "Thinking"


def test_execute_task_textual_tool_message_side_effect_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.messages import ToolMessage

    from invincat_cli import textual_adapter as adapter_mod

    wecom_payload = {
        "type": "wecom_send_file",
        "path": "/tmp/report.md",
        "filename": "report.md",
        "tool_call_id": "file-1",
    }
    schedule_payload = {
        "type": "schedule_cancel",
        "task_id": "task-1",
    }
    wecom_calls: list[dict[str, object]] = []
    schedule_calls: list[dict[str, object]] = []

    class SideEffectAgent:
        async def astream(self, *_args: object, **_kwargs: object):
            for _ in range(2):
                yield (
                    (),
                    "messages",
                    (
                        ToolMessage(
                            content=json.dumps(wecom_payload),
                            name="send_wecom_file",
                            tool_call_id="file-1",
                            status="success",
                        ),
                        {},
                    ),
                )
            yield (
                (),
                "messages",
                (
                    ToolMessage(
                        content=json.dumps(schedule_payload),
                        name="cancel_scheduled_task",
                        tool_call_id="sched-1",
                        status="success",
                    ),
                    {},
                ),
            )
            yield (
                (),
                "messages",
                (
                    ToolMessage(
                        content=json.dumps({"type": "schedule_list"}),
                        name="list_scheduled_tasks",
                        tool_call_id="sched-2",
                        status="success",
                    ),
                    {},
                ),
            )

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    async def on_wecom_file(payload: dict[str, object]) -> None:
        wecom_calls.append(payload)

    async def on_schedule(payload: dict[str, object]) -> None:
        schedule_calls.append(payload)
        if payload.get("type") == "schedule_list":
            raise RuntimeError("callback failed")

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    stats = asyncio.run(
        execute_task_textual(
            "hi",
            SideEffectAgent(),
            "agent",
            SimpleNamespace(thread_id="thread-tool-side-effects", plan_mode=False),
            TextualUIAdapter(
                mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
                update_status=lambda _status: None,
                request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
            ),
            on_wecom_file_request=on_wecom_file,
            on_schedule_payload=on_schedule,
        )
    )

    assert wecom_calls == [wecom_payload]
    assert schedule_calls == [schedule_payload, {"type": "schedule_list"}]
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_wecom_file_callback_failure_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.messages import ToolMessage

    from invincat_cli import textual_adapter as adapter_mod

    payload = {
        "type": "wecom_send_file",
        "path": "/tmp/report.md",
        "tool_call_id": "file-2",
    }
    calls: list[dict[str, object]] = []

    class WeComCallbackFailureAgent:
        async def astream(self, *_args: object, **_kwargs: object):
            yield (
                (),
                "messages",
                (
                    ToolMessage(
                        content=json.dumps(payload),
                        name="send_wecom_file",
                        tool_call_id="file-2",
                        status="success",
                    ),
                    {},
                ),
            )

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    async def on_wecom_file(file_payload: dict[str, object]) -> None:
        calls.append(file_payload)
        raise RuntimeError("upload failed")

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    stats = asyncio.run(
        execute_task_textual(
            "hi",
            WeComCallbackFailureAgent(),
            "agent",
            SimpleNamespace(thread_id="thread-wecom-failure", plan_mode=False),
            TextualUIAdapter(
                mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
                update_status=lambda _status: None,
                request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
            ),
            on_wecom_file_request=on_wecom_file,
        )
    )

    assert calls == [payload]
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_middleware_status_chunks_update_spinner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    mounted: list[object] = []
    spinners: list[object] = []

    class MiddlewareStatusAgent:
        async def astream(self, *_args: object, **_kwargs: object):
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(content_blocks=[{"type": "text", "text": "hide"}]),
                    {"lc_source": "summarization"},
                ),
            )
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(content_blocks=[]),
                    {"lc_source": "memory_agent"},
                ),
            )
            yield (
                (),
                "messages",
                (SimpleNamespace(content_blocks=[]), {}),
            )

    async def mount_message(message: object) -> None:
        mounted.append(message)

    async def set_spinner(value: object) -> None:
        spinners.append(value)

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    stats = asyncio.run(
        execute_task_textual(
            "hi",
            MiddlewareStatusAgent(),
            "agent",
            SimpleNamespace(thread_id="thread-middleware-status", plan_mode=False),
            TextualUIAdapter(
                mount_message=mount_message,
                update_status=lambda _status: None,
                request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
                set_spinner=set_spinner,
            ),
        )
    )

    assert any(type(message).__name__ == "SummarizationMessage" for message in mounted)
    assert any("offload" in str(value).lower() for value in spinners)
    assert any("memory" in str(value).lower() for value in spinners)
    assert "Thinking" in spinners
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_mounts_summary_notice_when_stream_ends_mid_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    mounted: list[object] = []
    spinners: list[object] = []

    class SummaryOnlyAgent:
        async def astream(self, *_args: object, **_kwargs: object):
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(content_blocks=[{"type": "text", "text": "hide"}]),
                    {"lc_source": "summarization"},
                ),
            )

    async def mount_message(message: object) -> None:
        mounted.append(message)

    async def set_spinner(value: object) -> None:
        spinners.append(value)

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    stats = asyncio.run(
        execute_task_textual(
            "hi",
            SummaryOnlyAgent(),
            "agent",
            SimpleNamespace(thread_id="thread-summary-ended", plan_mode=False),
            TextualUIAdapter(
                mount_message=mount_message,
                update_status=lambda _status: None,
                request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
                set_spinner=set_spinner,
            ),
        )
    )

    assert any(type(message).__name__ == "SummarizationMessage" for message in mounted)
    assert spinners[-1] == "Thinking"
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_file_tool_result_annotations_and_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.messages import ToolMessage

    from invincat_cli import textual_adapter as adapter_mod

    mounted: list[object] = []
    mounted_tools: list[object] = []
    write_args = {"path": "demo.txt", "content": "hello"}

    class FakeFileOpTracker:
        def __init__(self, **_kwargs: object) -> None:
            self.active: dict[str, object] = {}

        def start_operation(
            self, tool_name: str, args: dict[str, object], tool_call_id: str
        ) -> None:
            self.active[tool_call_id] = SimpleNamespace(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args=args,
            )

        def complete_with_message(
            self, message: object, _tool_args: dict[str, object] | None = None
        ) -> object | None:
            tool_call_id = getattr(message, "tool_call_id", "")
            if tool_call_id == "write-missing":
                return None
            if tool_call_id == "edit-error":
                return SimpleNamespace(
                    status="error",
                    error="read failed",
                    diff="",
                    display_path="demo.txt",
                    args=write_args,
                )
            return SimpleNamespace(
                status="success",
                error=None,
                diff="--- before\n+++ after",
                display_path="demo.txt",
                args=write_args,
            )

    class FakeToolCallMessage:
        def __init__(
            self,
            name: str,
            args: dict[str, object],
            *,
            tool_call_id: str,
            args_finalized: bool = False,
        ) -> None:
            self._tool_name = name
            self._args = args
            self._tool_call_id = tool_call_id
            self.args_finalized = args_finalized
            self.successes: list[str] = []
            self.id = None
            mounted_tools.append(self)

        def update_args(self, args: dict[str, object]) -> None:
            self._args = args

        def set_success(self, output: str) -> None:
            self.successes.append(output)

    class FakeDiffMessage:
        def __init__(self, diff: str, display_path: str) -> None:
            self.diff = diff
            self.display_path = display_path

    class FileToolAgent:
        async def astream(self, *_args: object, **_kwargs: object):
            for name, tool_call_id in [
                ("write_file", "write-missing"),
                ("edit_file", "edit-error"),
                ("edit_file", "edit-diff"),
            ]:
                yield (
                    (),
                    "messages",
                    (
                        SimpleNamespace(
                            content_blocks=[
                                {
                                    "type": "tool_call",
                                    "name": name,
                                    "args": write_args,
                                    "id": tool_call_id,
                                }
                            ]
                        ),
                        {},
                    ),
                )
                yield (
                    (),
                    "messages",
                    (
                        ToolMessage(
                            content="ok",
                            name=name,
                            tool_call_id=tool_call_id,
                            status="success",
                        ),
                        {},
                    ),
                )

    async def mount_message(message: object) -> None:
        mounted.append(message)

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    monkeypatch.setattr(adapter_mod, "FileOpTracker", FakeFileOpTracker)
    monkeypatch.setattr(adapter_mod, "ToolCallMessage", FakeToolCallMessage)
    monkeypatch.setattr(adapter_mod, "DiffMessage", FakeDiffMessage)
    stats = asyncio.run(
        execute_task_textual(
            "hi",
            FileToolAgent(),
            "agent",
            SimpleNamespace(thread_id="thread-file-tool-results", plan_mode=False),
            TextualUIAdapter(
                mount_message=mount_message,
                update_status=lambda _status: None,
                request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
            ),
        )
    )

    assert "operation was not tracked" in mounted_tools[0].successes[0]
    assert "read failed" in mounted_tools[1].successes[0]
    assert mounted_tools[2].successes == ["ok"]
    diff_messages = [
        message for message in mounted if isinstance(message, FakeDiffMessage)
    ]
    assert diff_messages[0].display_path == "demo.txt"
    assert diff_messages[0].diff == "--- before\n+++ after"
    assert stats.wall_time_seconds >= 0


def test_execute_task_textual_records_stream_token_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli import textual_adapter as adapter_mod

    token_updates: list[tuple[int, bool]] = []

    class TokenAgent:
        def __init__(self) -> None:
            self.state_updates: list[dict[str, object]] = []

        async def astream(self, *_args: object, **_kwargs: object):
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(
                        content_blocks=[],
                        usage_metadata={"input_tokens": 5, "output_tokens": 2},
                    ),
                    {},
                ),
            )
            yield (
                (),
                "messages",
                (
                    SimpleNamespace(
                        content_blocks=[],
                        usage_metadata={"total_tokens": 11},
                    ),
                    {},
                ),
            )
            yield ((), "messages", (object(), {}))

        async def aupdate_state(
            self, _config: dict[str, object], update: dict[str, object]
        ) -> None:
            self.state_updates.append(update)

    async def dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    monkeypatch.setattr("invincat_cli.config.settings.model_name", "model-token-test")
    monkeypatch.setattr(adapter_mod, "dispatch_hook", dispatch)
    agent = TokenAgent()
    adapter = TextualUIAdapter(
        mount_message=lambda *_args, **_kwargs: _completed(),  # type: ignore[arg-type]
        update_status=lambda _status: None,
        request_approval=lambda *_args, **_kwargs: None,  # type: ignore[arg-type]
    )
    adapter._on_tokens_update = lambda count, *, approximate=False: (
        token_updates.append((count, approximate))
    )

    stats = asyncio.run(
        execute_task_textual(
            "hi",
            agent,
            "agent",
            SimpleNamespace(thread_id="thread-token-usage", plan_mode=False),
            adapter,
        )
    )

    assert token_updates == [(7, False), (11, False), (11, False)]
    assert agent.state_updates == [{"_context_tokens": 11}]
    assert stats.request_count == 2
    assert stats.input_tokens == 16
    assert stats.output_tokens == 2


async def _record_delta(
    deltas: list[tuple[str, str]], text: str, accumulated: str
) -> None:
    deltas.append((text, accumulated))
