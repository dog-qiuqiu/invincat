"""Unit tests for offload business logic."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from invincat_cli import offload
from invincat_cli.offload import (
    OffloadModelError,
    OffloadResult,
    OffloadThresholdNotMet,
    _trim_tool_outputs,
    format_offload_limit,
    offload_messages_to_backend,
    perform_offload,
)


class FakeMiddleware:
    """Small summarization middleware double used by perform_offload tests."""

    cutoff = 1
    instances: list[FakeMiddleware] = []

    def __init__(
        self,
        *,
        model: object,
        backend: object,
        keep: tuple[str, int | float],
        trim_tokens_to_summarize: object,
    ) -> None:
        self.model = model
        self.backend = backend
        self.keep = keep
        self.trim_tokens_to_summarize = trim_tokens_to_summarize
        self.summary_inputs: list[list[object]] = []
        FakeMiddleware.instances.append(self)

    def _apply_event_to_messages(
        self,
        messages: list[object],
        prior_event: dict | None,
    ) -> list[object]:
        if prior_event:
            return [prior_event["summary_message"], *messages]
        return messages

    def _determine_cutoff_index(self, _messages: list[object]) -> int:
        return self.cutoff

    def _partition_messages(
        self,
        messages: list[object],
        cutoff: int,
    ) -> tuple[list[object], list[object]]:
        return messages[:cutoff], messages[cutoff:]

    async def _acreate_summary(self, messages: list[object]) -> str:
        self.summary_inputs.append(messages)
        return "summary text"

    def _filter_summary_messages(self, messages: list[object]) -> list[object]:
        return [
            msg for msg in messages if getattr(msg, "content", None) != "prior summary"
        ]

    def _build_new_messages_with_path(
        self,
        summary: str,
        file_path: str | None,
    ) -> list[AIMessage]:
        suffix = f" saved={file_path}" if file_path else ""
        return [AIMessage(content=f"{summary}{suffix}")]

    def _compute_state_cutoff(self, prior_event: dict | None, cutoff: int) -> int:
        prior_cutoff = prior_event.get("cutoff_index", 0) if prior_event else 0
        return prior_cutoff + cutoff


@pytest.fixture(autouse=True)
def reset_fake_middleware() -> None:
    FakeMiddleware.cutoff = 1
    FakeMiddleware.instances = []


def _install_fake_summarization(
    monkeypatch: pytest.MonkeyPatch,
    *,
    keep: tuple[str, int | float] = ("messages", 2),
) -> None:
    import deepagents.middleware.summarization as summarization

    monkeypatch.setattr(summarization, "SummarizationMiddleware", FakeMiddleware)
    monkeypatch.setattr(
        summarization,
        "compute_summarization_defaults",
        lambda _model: {"keep": keep},
    )


def test_format_offload_limit_variants() -> None:
    assert format_offload_limit(("messages", 1), 1000) == "last 1 message"
    assert format_offload_limit(("messages", 3), 1000) == "last 3 messages"
    assert format_offload_limit(("tokens", 1200), None) == "1.2K tokens"
    assert format_offload_limit(("fraction", 0.25), 8000) == "2.0K tokens"
    assert format_offload_limit(("fraction", 0.25), None) == "25% of context window"
    assert format_offload_limit(("unknown", 1), None) == "current retention threshold"


def test_trim_tool_outputs_only_replaces_large_string_tool_messages() -> None:
    large_content = "x" * (offload._TOOL_OUTPUT_TRIM_CHARS + 1000)
    large_tool = ToolMessage(content=large_content, tool_call_id="tool-1", name="read")
    small_tool = ToolMessage(content="small", tool_call_id="tool-2")
    human = HumanMessage(content="hello")
    non_string_tool = ToolMessage(content=["chunk"], tool_call_id="tool-3")

    trimmed = _trim_tool_outputs([human, large_tool, small_tool, non_string_tool])

    assert trimmed[0] is human
    assert trimmed[2] is small_tool
    assert trimmed[3] is non_string_tool
    assert trimmed[1] is not large_tool
    assert isinstance(trimmed[1], ToolMessage)
    assert trimmed[1].tool_call_id == "tool-1"
    assert len(trimmed[1].content) < len(large_content)
    assert "chars omitted" in trimmed[1].content


def test_offload_messages_to_backend_writes_filtered_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    middleware = FakeMiddleware(
        model=object(),
        backend=object(),
        keep=("messages", 1),
        trim_tokens_to_summarize=None,
    )
    messages = [
        AIMessage(content="prior summary"),
        HumanMessage(content="please analyze this"),
    ]

    path = asyncio.run(
        offload_messages_to_backend(messages, middleware, thread_id="thread-1")
    )

    assert path == str(tmp_path / ".invincat" / "conversation_history" / "thread-1.md")
    text = Path(path).read_text(encoding="utf-8")
    assert "Offloaded at" in text
    assert "please analyze this" in text
    assert "prior summary" not in text

    assert asyncio.run(offload_messages_to_backend([], middleware, thread_id="x")) == ""


def test_offload_messages_to_backend_returns_none_on_read_or_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    middleware = FakeMiddleware(
        model=object(),
        backend=object(),
        keep=("messages", 1),
        trim_tokens_to_summarize=None,
    )
    messages = [HumanMessage(content="hello")]
    history_dir = tmp_path / ".invincat" / "conversation_history"
    history_dir.mkdir(parents=True)
    history_file = history_dir / "thread-1.md"
    history_file.write_text("existing", encoding="utf-8")

    original_read_text = Path.read_text
    original_write_text = Path.write_text

    def fail_read(self: Path, *args: object, **kwargs: object) -> str:
        if self == history_file:
            raise OSError("read failed")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read)
    assert (
        asyncio.run(
            offload_messages_to_backend(messages, middleware, thread_id="thread-1")
        )
        is None
    )

    monkeypatch.setattr(Path, "read_text", original_read_text)

    def fail_write(self: Path, *args: object, **kwargs: object) -> int:
        if self == history_file:
            raise OSError("write failed")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_write)
    assert (
        asyncio.run(
            offload_messages_to_backend(messages, middleware, thread_id="thread-1")
        )
        is None
    )


def test_perform_offload_raises_model_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_create_model(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("bad credentials")

    monkeypatch.setattr(offload, "create_model", fail_create_model)

    with pytest.raises(OffloadModelError, match="bad credentials"):
        asyncio.run(
            perform_offload(
                messages=[HumanMessage(content="hello")],
                prior_event=None,
                thread_id="thread-1",
                model_spec="openai:gpt-5.2",
                profile_overrides=None,
                context_limit=None,
                total_context_tokens=0,
                backend=object(),
            )
        )


def test_perform_offload_returns_threshold_not_met(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_summarization(monkeypatch, keep=("tokens", 2000))
    FakeMiddleware.cutoff = 0
    model = SimpleNamespace(profile={"max_input_tokens": 1000})
    monkeypatch.setattr(
        offload,
        "create_model",
        lambda *_args, **_kwargs: SimpleNamespace(model=model),
    )

    result = asyncio.run(
        perform_offload(
            messages=[HumanMessage(content="short conversation")],
            prior_event=None,
            thread_id="thread-1",
            model_spec="openai:gpt-5.2",
            profile_overrides={"tool_calling": False},
            context_limit=4000,
            total_context_tokens=123,
            backend=object(),
        )
    )

    assert isinstance(result, OffloadThresholdNotMet)
    assert result.total_context_tokens == 123
    assert result.context_limit == 4000
    assert result.budget_str == "2.0K tokens"
    assert model.profile["max_input_tokens"] == 4000


def test_perform_offload_tolerates_unpatchable_model_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_summarization(monkeypatch)
    FakeMiddleware.cutoff = 0

    class ModelWithReadOnlyProfile:
        @property
        def profile(self) -> dict[str, int]:
            return {"max_input_tokens": 1000}

        @profile.setter
        def profile(self, _value: dict[str, int]) -> None:
            raise AttributeError("read only")

    monkeypatch.setattr(
        offload,
        "create_model",
        lambda *_args, **_kwargs: SimpleNamespace(model=ModelWithReadOnlyProfile()),
    )

    result = asyncio.run(
        perform_offload(
            messages=[HumanMessage(content="short")],
            prior_event=None,
            thread_id="thread-1",
            model_spec="openai:gpt-5.2",
            profile_overrides=None,
            context_limit=4000,
            total_context_tokens=0,
            backend=object(),
        )
    )

    assert isinstance(result, OffloadThresholdNotMet)


def test_perform_offload_uses_filesystem_backend_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import deepagents.backends.filesystem as filesystem

    _install_fake_summarization(monkeypatch)
    FakeMiddleware.cutoff = 0

    class FakeFilesystemBackend:
        pass

    monkeypatch.setattr(filesystem, "FilesystemBackend", FakeFilesystemBackend)
    monkeypatch.setattr(
        offload,
        "create_model",
        lambda *_args, **_kwargs: SimpleNamespace(model=SimpleNamespace(profile={})),
    )

    result = asyncio.run(
        perform_offload(
            messages=[HumanMessage(content="short")],
            prior_event=None,
            thread_id="thread-1",
            model_spec="openai:gpt-5.2",
            profile_overrides=None,
            context_limit=None,
            total_context_tokens=0,
            backend=None,
        )
    )

    assert isinstance(result, OffloadThresholdNotMet)
    assert isinstance(FakeMiddleware.instances[-1].backend, FakeFilesystemBackend)


def test_perform_offload_summarizes_trimmed_messages_and_builds_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_summarization(monkeypatch, keep=("messages", 1))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    model = SimpleNamespace(profile={"max_input_tokens": 4000})
    monkeypatch.setattr(
        offload,
        "create_model",
        lambda *_args, **_kwargs: SimpleNamespace(model=model),
    )
    large_tool = ToolMessage(
        content="x" * (offload._TOOL_OUTPUT_TRIM_CHARS + 50),
        tool_call_id="tool-1",
    )
    messages = [
        large_tool,
        AIMessage(content="tool result interpreted"),
        HumanMessage(content="new question"),
    ]
    result = asyncio.run(
        perform_offload(
            messages=messages,
            prior_event=None,
            thread_id="thread-1",
            model_spec="openai:gpt-5.2",
            profile_overrides=None,
            context_limit=4000,
            total_context_tokens=999,
            backend=object(),
        )
    )

    assert isinstance(result, OffloadResult)
    assert result.messages_offloaded == 1
    assert result.messages_kept == 2
    assert result.offload_warning is None
    assert result.new_event["cutoff_index"] == 1
    assert result.new_event["file_path"] is not None
    assert "messages were offloaded" in result.new_event["summary_message"].content
    assert FakeMiddleware.instances[-1].summary_inputs
    summarized_tool = FakeMiddleware.instances[-1].summary_inputs[0][0]
    assert isinstance(summarized_tool, ToolMessage)
    assert "chars omitted" in summarized_tool.content
    stored = Path(result.new_event["file_path"]).read_text(encoding="utf-8")
    assert large_tool.content in stored


def test_perform_offload_continues_when_backend_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_summarization(monkeypatch)
    monkeypatch.setattr(
        offload,
        "create_model",
        lambda *_args, **_kwargs: SimpleNamespace(model=SimpleNamespace(profile={})),
    )

    async def fail_backend_write(
        _messages: list[object],
        _middleware: object,
        *,
        thread_id: str,
    ) -> None:
        assert thread_id == "thread-1"
        return None

    monkeypatch.setattr(offload, "offload_messages_to_backend", fail_backend_write)

    result = asyncio.run(
        perform_offload(
            messages=[
                HumanMessage(content="old"),
                HumanMessage(content="new"),
            ],
            prior_event=None,
            thread_id="thread-1",
            model_spec="openai:gpt-5.2",
            profile_overrides=None,
            context_limit=None,
            total_context_tokens=0,
            backend=object(),
        )
    )

    assert isinstance(result, OffloadResult)
    assert result.offload_warning is not None
    assert result.new_event["file_path"] is None
