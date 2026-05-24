"""Tests for Textual app thread-history conversion helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from invincat_cli.app_runtime import thread_history
from invincat_cli.app_runtime.thread_diff_history import (
    ThreadDiffRecord,
    load_thread_diffs,
    save_thread_diff,
)
from invincat_cli.app_runtime.thread_history import (
    build_resume_summary,
    convert_messages_to_data,
    merge_thread_diff_messages,
    merge_thread_state_with_fallback,
    thread_history_payload_from_state_values,
)
from invincat_cli.widgets.message_store import MessageData, MessageType, ToolStatus


def test_convert_messages_to_data_matches_tool_results() -> None:
    data = convert_messages_to_data(
        [
            HumanMessage(content="hello"),
            AIMessage(
                content=[{"type": "text", "text": "working"}, " done"],
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "read_file",
                        "args": {"path": "README.md"},
                    }
                ],
            ),
            ToolMessage("file content", tool_call_id="call-1"),
        ]
    )

    assert [item.type for item in data] == [
        MessageType.USER,
        MessageType.ASSISTANT,
        MessageType.TOOL,
    ]
    assert data[1].content == "working done"
    assert data[2].tool_name == "read_file"
    assert data[2].tool_call_id == "call-1"
    assert data[2].tool_status == ToolStatus.SUCCESS
    assert data[2].tool_output == "file content"


def test_merge_thread_diff_messages_inserts_after_matching_file_tool() -> None:
    messages = [
        MessageData(type=MessageType.USER, content="edit"),
        MessageData(
            type=MessageType.TOOL,
            content="",
            tool_name="edit_file",
            tool_args={"file_path": "demo.py"},
            tool_status=ToolStatus.SUCCESS,
            tool_call_id="call-1",
            tool_output="ok",
        ),
        MessageData(
            type=MessageType.TOOL,
            content="",
            tool_name="read_file",
            tool_args={"file_path": "demo.py"},
            tool_status=ToolStatus.SUCCESS,
            tool_call_id="call-2",
            tool_output="content",
        ),
    ]

    merged = merge_thread_diff_messages(
        messages,
        [
            ThreadDiffRecord(
                tool_call_id="call-1",
                display_path="demo.py",
                diff="--- before\n+++ after",
                created_at=1.0,
            )
        ],
    )

    assert [item.type for item in merged] == [
        MessageType.USER,
        MessageType.TOOL,
        MessageType.DIFF,
        MessageType.TOOL,
    ]
    assert merged[2].content == "--- before\n+++ after"
    assert merged[2].diff_file_path == "demo.py"


def test_merge_thread_state_with_fallback_fills_empty_remote_values() -> None:
    merged = merge_thread_state_with_fallback(
        {"messages": [], "_context_tokens": None},
        {
            "messages": [HumanMessage(content="from fallback")],
            "_context_tokens": 42,
            "_summarization_event": {"summary": "kept"},
        },
    )

    assert merged["messages"][0].content == "from fallback"
    assert merged["_context_tokens"] == 42
    assert merged["_summarization_event"] == {"summary": "kept"}


def test_merge_thread_state_with_fallback_keeps_existing_messages() -> None:
    existing = [HumanMessage(content="from remote")]
    merged = merge_thread_state_with_fallback(
        {"messages": existing, "_context_tokens": 12},
        {"messages": [HumanMessage(content="from fallback")], "_context_tokens": 42},
    )

    assert merged["messages"] == existing
    assert merged["_context_tokens"] == 12


def test_thread_history_payload_from_state_values_converts_messages() -> None:
    payload = thread_history_payload_from_state_values(
        {
            "_context_tokens": 123,
            "messages": [HumanMessage(content="hello")],
        }
    )

    assert payload.context_tokens == 123
    assert len(payload.messages) == 1
    assert payload.messages[0].type == MessageType.USER
    assert payload.messages[0].content == "hello"


def test_thread_history_payload_loads_persisted_diff_records(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "invincat_cli.app_runtime.thread_diff_history._history_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(thread_history, "load_thread_diffs", load_thread_diffs)
    save_thread_diff(
        thread_id="thread-1",
        tool_call_id="call-1",
        display_path="demo.py",
        diff="--- before\n+++ after",
    )

    payload = thread_history_payload_from_state_values(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "call-1",
                            "name": "write_file",
                            "args": {"file_path": "demo.py"},
                        }
                    ],
                ),
                ToolMessage("Updated file demo.py", tool_call_id="call-1"),
            ]
        },
        thread_id="thread-1",
    )

    assert [item.type for item in payload.messages] == [
        MessageType.TOOL,
        MessageType.DIFF,
    ]
    assert payload.messages[1].content == "--- before\n+++ after"
    assert payload.messages[1].diff_file_path == "demo.py"


def test_thread_history_payload_handles_empty_and_serialized_messages() -> None:
    empty = thread_history_payload_from_state_values({"_context_tokens": -1})

    assert empty.context_tokens == 0
    assert empty.messages == []

    payload = thread_history_payload_from_state_values(
        {
            "_context_tokens": 5,
            "messages": [{"type": "human", "content": "serialized"}],
        }
    )

    assert payload.context_tokens == 5
    assert payload.messages[0].type == MessageType.USER
    assert payload.messages[0].content == "serialized"


def test_convert_messages_to_data_skips_system_and_preserves_skill_invocation() -> None:
    data = convert_messages_to_data(
        [
            HumanMessage(content="[SYSTEM] hidden"),
            HumanMessage(
                content="skill body",
                additional_kwargs={
                    "__skill": {
                        "name": "writer",
                        "description": "drafts text",
                        "source": "local",
                        "args": "make it shorter",
                    }
                },
            ),
        ]
    )

    assert len(data) == 1
    assert data[0].type == MessageType.SKILL
    assert data[0].skill_name == "writer"
    assert data[0].skill_body == "skill body"
    assert data[0].skill_args == "make it shorter"


def test_convert_messages_to_data_rejects_unmatched_tool_calls() -> None:
    data = convert_messages_to_data(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call-1", "name": "write_file", "args": {"path": "x"}}
                ],
            )
        ]
    )

    assert len(data) == 1
    assert data[0].type == MessageType.TOOL
    assert data[0].tool_status == ToolStatus.REJECTED
    assert data[0].tool_call_id == "call-1"


def test_convert_messages_to_data_keeps_empty_tool_results_visible() -> None:
    data = convert_messages_to_data(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call-1", "name": "shell", "args": {"command": "true"}}
                ],
            ),
            ToolMessage("", tool_call_id="call-1"),
        ]
    )

    assert len(data) == 1
    assert data[0].type == MessageType.TOOL
    assert data[0].tool_status == ToolStatus.SUCCESS
    assert data[0].tool_call_id == "call-1"
    assert data[0].tool_output == "(no output)"


def test_convert_messages_to_data_handles_unmatched_and_unsupported_messages() -> None:
    data = convert_messages_to_data(
        [
            AIMessage(
                content="",
                tool_calls=[{"id": None, "name": "shell", "args": {"command": "ls"}}],
            ),
            ToolMessage("orphan", tool_call_id="missing"),
            object(),
        ]
    )

    assert len(data) == 1
    assert data[0].type == MessageType.TOOL
    assert data[0].tool_status == ToolStatus.REJECTED
    assert thread_history._extract_ai_text({"not": "text"}) == ""


def test_build_resume_summary_uses_first_last_user_messages_and_tokens() -> None:
    summary = build_resume_summary(
        [
            MessageData(type=MessageType.USER, content="first topic"),
            MessageData(type=MessageType.ASSISTANT, content="answer"),
            MessageData(type=MessageType.USER, content="second topic"),
        ],
        context_tokens=1234,
    )

    assert summary == (
        "3 messages, 1.2K tokens · Started with: “first topic” · "
        "Last topic: “second topic”"
    )


def test_build_resume_summary_truncates_long_preview() -> None:
    summary = build_resume_summary(
        [MessageData(type=MessageType.USER, content="x" * 100)],
        context_tokens=0,
    )

    assert "x" * 80 in summary
    assert summary.endswith("…”")


def test_build_resume_summary_returns_empty_without_user_messages() -> None:
    assert (
        build_resume_summary(
            [MessageData(type=MessageType.ASSISTANT, content="answer")],
            context_tokens=0,
        )
        == ""
    )
