"""Tests for Textual app thread-history conversion helpers."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from invincat_cli.app_runtime.thread_history import (
    build_resume_summary,
    convert_messages_to_data,
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
    assert data[2].tool_status == ToolStatus.SUCCESS
    assert data[2].tool_output == "file content"


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


def test_build_resume_summary_returns_empty_without_user_messages() -> None:
    assert (
        build_resume_summary(
            [MessageData(type=MessageType.ASSISTANT, content="answer")],
            context_tokens=0,
        )
        == ""
    )
