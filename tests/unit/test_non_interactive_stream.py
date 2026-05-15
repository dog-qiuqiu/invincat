"""Tests for non-interactive stream chunk processing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain.agents.middleware.human_in_the_loop import ActionRequest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Interrupt
from rich.console import Console

from invincat_cli.non_interactive import stream as ni_stream
from invincat_cli.non_interactive.state import StreamState


class _Spinner:
    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        self.starts += 1

    def stop(self) -> None:
        self.stops += 1


class _Tracker:
    def __init__(self, record: object | None = None) -> None:
        self.record = record
        self.messages: list[ToolMessage] = []

    def complete_with_message(self, message: ToolMessage) -> object | None:
        self.messages.append(message)
        return self.record


def test_process_interrupts_records_valid_hitl_and_rejects_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hook_calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        ni_stream,
        "dispatch_hook_fire_and_forget",
        lambda name, payload: hook_calls.append((name, payload)),
    )
    state = StreamState()

    ni_stream._process_interrupts(
        {
            "__interrupt__": [
                Interrupt(
                    id="valid",
                    value={
                        "action_requests": [{"name": "read_file", "args": {}}],
                        "review_configs": [
                            {
                                "action_name": "read_file",
                                "allowed_decisions": ["approve", "reject"],
                            }
                        ],
                    },
                ),
                Interrupt(id="bad", value={"action_requests": "not-a-list"}),
            ]
        },
        state,
        Console(record=True),
    )

    assert "valid" in state.pending_interrupts
    assert state.interrupt_occurred
    assert hook_calls == [("input.required", {})]
    assert state.hitl_response["bad"]["decisions"][0]["type"] == "reject"


def test_process_ai_message_streams_text_buffers_tool_and_records_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: list[str] = []
    monkeypatch.setattr(ni_stream, "_write_text", written.append)
    state = StreamState(spinner=_Spinner())
    message = AIMessage(
        content=[
            {"type": "text", "text": "hello"},
            {"type": "tool_call_chunk", "name": "read_file", "id": "a", "index": 0},
            "ignored",
        ],
        usage_metadata={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
    )
    console = Console(record=True)

    ni_stream._process_ai_message(message, state, console)

    assert written == ["hello", "ignored"]
    assert state.full_response == ["hello", "ignored"]
    assert state.tool_call_buffers[0] == {"name": "read_file", "id": None}
    assert state.spinner.stops == 3
    assert "Calling tool: read_file" in console.export_text()
    assert state.stats.input_tokens == 3
    assert state.stats.output_tokens == 4


def test_process_ai_message_non_stream_accumulates_without_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: list[str] = []
    monkeypatch.setattr(ni_stream, "_write_text", written.append)
    state = StreamState(stream=False)

    ni_stream._process_ai_message(
        AIMessage(content=[{"type": "text", "text": "held"}]),
        state,
        Console(record=True),
    )

    assert written == []
    assert state.full_response == ["held"]


def test_process_message_chunk_skips_bad_shapes_and_internal_sources() -> None:
    state = StreamState()
    tracker = _Tracker()

    ni_stream._process_message_chunk("bad", state, Console(record=True), tracker)
    ni_stream._process_message_chunk(
        (
            AIMessage(content=[{"type": "text", "text": "hidden"}]),
            {"lc_source": "memory_agent"},
        ),
        state,
        Console(record=True),
        tracker,
    )

    assert state.full_response == []
    assert tracker.messages == []


def test_process_message_chunk_prints_file_diff_records_and_restarts_spinner() -> None:
    state = StreamState(spinner=_Spinner())
    record = SimpleNamespace(diff="--- diff", display_path="file.txt")
    tracker = _Tracker(record)
    console = Console(record=True)
    message = ToolMessage("done", name="edit_file", tool_call_id="call-1")

    ni_stream._process_message_chunk((message, {}), state, console, tracker)

    assert tracker.messages == [message]
    assert state.spinner.stops == 1
    assert state.spinner.starts == 1
    assert "file.txt" in console.export_text()


def test_process_stream_chunk_ignores_subagents_and_routes_main_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        ni_stream,
        "_process_message_chunk",
        lambda data, state, console, tracker: calls.append(data),
    )
    state = StreamState()

    ni_stream._process_stream_chunk(
        (("subgraph",), "messages", ("ignored", {})),
        state,
        Console(record=True),
        _Tracker(),
    )
    ni_stream._process_stream_chunk(
        ((), "messages", ("used", {})),
        state,
        Console(record=True),
        _Tracker(),
    )
    ni_stream._process_stream_chunk(
        ("bad", "shape"),
        state,
        Console(record=True),
        _Tracker(),
    )

    assert calls == [("used", {})]


def test_make_hitl_decision_rejects_shell_without_allow_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ni_stream.settings, "shell_allow_list", [])
    decision = ni_stream._make_hitl_decision(
        ActionRequest(name="execute", args={"command": "rm -rf /"}),
        Console(record=True),
    )

    assert decision["type"] == "reject"
    assert "allow-list" in decision["message"]


def test_make_hitl_decision_rejects_disallowed_shell_and_approves_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ni_stream.settings, "shell_allow_list", ["git"])

    rejected = ni_stream._make_hitl_decision(
        ActionRequest(name="execute", args={"command": "rm file.txt"}),
        Console(record=True),
    )
    approved = ni_stream._make_hitl_decision(
        ActionRequest(name="execute", args={"command": "git status"}),
        Console(record=True),
    )

    assert rejected["type"] == "reject"
    assert approved == {"type": "approve"}


def test_make_hitl_decision_warns_on_suspicious_action_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ni_stream.settings, "shell_allow_list", [])
    console = Console(record=True)

    decision = ni_stream._make_hitl_decision(
        ActionRequest(
            name="read_file",
            args={"url": "https://xn--pple-43d.com", "text": "safe"},
        ),
        console,
    )

    assert decision == {"type": "approve"}
    assert "Warning:" in console.export_text()


def test_process_hitl_interrupts_clears_pending_and_records_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ni_stream,
        "_make_hitl_decision",
        lambda action, console: {"type": "approve", "message": action["name"]},
    )
    state = StreamState(
        pending_interrupts={
            "i1": {
                "action_requests": [
                    {"name": "read_file", "args": {}},
                    {"name": "grep", "args": {}},
                ],
                "review_configs": [],
            }
        }
    )

    ni_stream._process_hitl_interrupts(state, Console(record=True))

    assert state.pending_interrupts == {}
    assert state.hitl_response == {
        "i1": {
            "decisions": [
                {"type": "approve", "message": "read_file"},
                {"type": "approve", "message": "grep"},
            ]
        }
    }
