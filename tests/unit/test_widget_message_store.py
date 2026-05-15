from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from textual.content import Content

from invincat_cli.widgets.message_store import (
    MessageData,
    MessageStore,
    MessageType,
    ToolStatus,
)
from invincat_cli.widgets.messages import (
    AppMessage,
    AssistantMessage,
    DiffMessage,
    ErrorMessage,
    SkillMessage,
    SummarizationMessage,
    ToolCallMessage,
    UserMessage,
)


def _message(index: int, *, msg_type: MessageType = MessageType.APP) -> MessageData:
    return MessageData(type=msg_type, content=f"message {index}", id=f"msg-{index}")


def test_message_data_requires_tool_and_skill_names() -> None:
    with pytest.raises(ValueError, match="TOOL messages"):
        MessageData(type=MessageType.TOOL, content="")

    with pytest.raises(ValueError, match="SKILL messages"):
        MessageData(type=MessageType.SKILL, content="")


def test_message_data_to_widget_restores_all_message_types() -> None:
    cases: list[tuple[MessageData, type[Any]]] = [
        (MessageData(type=MessageType.USER, content="user", id="u"), UserMessage),
        (
            MessageData(type=MessageType.ASSISTANT, content="assistant", id="a"),
            AssistantMessage,
        ),
        (MessageData(type=MessageType.ERROR, content="error", id="e"), ErrorMessage),
        (MessageData(type=MessageType.APP, content="app", id="app"), AppMessage),
        (
            MessageData(type=MessageType.SUMMARIZATION, content="sum", id="s"),
            SummarizationMessage,
        ),
        (
            MessageData(
                type=MessageType.DIFF,
                content="@@\n+added",
                id="d",
                diff_file_path="file.py",
            ),
            DiffMessage,
        ),
    ]

    for data, expected_type in cases:
        widget = data.to_widget()
        assert isinstance(widget, expected_type)
        assert widget.id == data.id


def test_message_data_to_widget_falls_back_for_unknown_message_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    data = MessageData(type=MessageType.APP, content="fallback", id="unknown-type")
    data.type = "future"  # type: ignore[assignment]

    with caplog.at_level("WARNING"):
        widget = data.to_widget()

    assert isinstance(widget, AppMessage)
    assert widget.id == "unknown-type"
    assert "Unknown MessageType" in caplog.text


def test_tool_and_skill_to_widget_restore_deferred_state() -> None:
    tool = MessageData(
        type=MessageType.TOOL,
        content="",
        id="tool",
        tool_name="read_file",
        tool_args={"path": "README.md"},
        tool_status=ToolStatus.SUCCESS,
        tool_output="done",
        tool_expanded=True,
        tool_call_id=123,
    ).to_widget()
    assert isinstance(tool, ToolCallMessage)
    assert tool._deferred_status == ToolStatus.SUCCESS
    assert tool._deferred_output == "done"
    assert tool._deferred_expanded is True

    skill = MessageData(
        type=MessageType.SKILL,
        content="",
        id="skill",
        skill_name="code-review",
        skill_description="Review code",
        skill_source="user",
        skill_body="# Body",
        skill_args="args",
        skill_expanded=True,
    ).to_widget()
    assert isinstance(skill, SkillMessage)
    assert skill._deferred_expanded is True


def test_from_widget_serializes_known_widgets() -> None:
    skill = SkillMessage(
        "code-review",
        description="Review code",
        source="user",
        body="# Body",
        args="focus",
        id="skill",
    )
    skill._expanded = True
    assert MessageData.from_widget(skill).skill_expanded is True

    user = UserMessage("hello", id="user")
    assert MessageData.from_widget(user).type == MessageType.USER

    assistant = AssistantMessage("answer", id="assistant")
    assistant._stream = object()  # type: ignore[assignment]
    assistant_data = MessageData.from_widget(assistant)
    assert assistant_data.type == MessageType.ASSISTANT
    assert assistant_data.is_streaming is True

    tool = ToolCallMessage("shell", {"cmd": "pwd"}, tool_call_id="tc-1", id="tool")
    tool._status = "success"
    tool._output = "ok"
    tool._expanded = True
    tool_data = MessageData.from_widget(tool)
    assert tool_data.tool_status == ToolStatus.SUCCESS
    assert tool_data.tool_output == "ok"
    assert tool_data.tool_expanded is True

    tool._status = "future-status"
    assert MessageData.from_widget(tool).tool_status is None

    assert (
        MessageData.from_widget(ErrorMessage("bad", id="err")).type == MessageType.ERROR
    )
    assert (
        MessageData.from_widget(
            DiffMessage("@@\n+new", file_path="file.py", id="diff")
        ).diff_file_path
        == "file.py"
    )
    assert (
        MessageData.from_widget(SummarizationMessage("summary", id="sum")).type
        == MessageType.SUMMARIZATION
    )
    app_data = MessageData.from_widget(AppMessage(Content("app"), id="app"))
    assert app_data.type == MessageType.APP
    assert app_data.content == "app"


def test_from_widget_falls_back_for_unknown_widget_type() -> None:
    widget = SimpleNamespace(id="unknown")

    data = MessageData.from_widget(widget)  # type: ignore[arg-type]

    assert data.type == MessageType.APP
    assert "Unknown widget" in data.content
    assert data.id == "unknown"


def test_store_index_lookup_update_and_clear() -> None:
    store = MessageStore()
    tool = MessageData(
        type=MessageType.TOOL,
        content="",
        id="tool",
        tool_name="shell",
        tool_call_id=1,
    )
    store.append(tool)

    assert store.get_message("tool") is tool
    assert store.get_message("missing") is None
    assert store.get_message_at_index(0) is tool
    assert store.get_message_at_index(-1) is None
    assert store.get_message_by_tool_call_id("1") is tool
    assert store.get_message_by_tool_call_id(1) is tool
    assert store.get_message_by_tool_call_id("missing") is None

    assert store.update_message("tool", tool_output="ok", tool_call_id="real-id")
    assert tool.tool_output == "ok"
    assert store.get_message_by_tool_call_id("real-id") is tool
    assert store.get_message_by_tool_call_id(1) is None
    assert store.update_message("missing", content="x") is False

    with pytest.raises(ValueError, match="protected"):
        store.update_message("tool", id="new-id")

    assert store.is_active("tool") is False
    store.set_active_message("tool")
    assert store.is_active("tool") is True
    store.clear()
    assert store.total_count == 0
    assert store.get_visible_range() == (0, 0)
    assert store.get_message_by_tool_call_id("real-id") is None


def test_store_bulk_window_prune_and_hydrate_boundaries() -> None:
    store = MessageStore()
    archived, visible = store.bulk_load([_message(i) for i in range(60)])

    assert len(archived) == 10
    assert len(visible) == MessageStore.WINDOW_SIZE
    assert store.visible_count == MessageStore.WINDOW_SIZE
    assert store.has_messages_above is True
    assert store.has_messages_below is False
    assert store.get_visible_range() == (10, 60)
    assert store.get_all_messages()[0].id == "msg-0"
    assert store.get_visible_messages()[0].id == "msg-10"

    store.append(_message(60))
    assert store.window_exceeded() is True
    assert [msg.id for msg in store.get_messages_to_prune()] == ["msg-10"]
    store.set_active_message("msg-10")
    assert store.get_messages_to_prune(count=3) == []
    store.set_active_message(None)
    assert store.get_messages_to_prune(count=0) == []

    store.mark_pruned(["msg-10", "msg-11"])
    assert store.get_visible_range() == (12, 61)
    assert [msg.id for msg in store.get_messages_to_hydrate(count=3)] == [
        "msg-9",
        "msg-10",
        "msg-11",
    ]
    assert store.mark_hydrated(99) == 12
    assert store.get_visible_range()[0] == 0
    assert store.get_messages_to_hydrate() == []


def test_store_small_bulk_load_and_none_tool_call_id() -> None:
    store = MessageStore()
    archived, visible = store.bulk_load([_message(i) for i in range(3)])

    assert archived == []
    assert [msg.id for msg in visible] == ["msg-0", "msg-1", "msg-2"]
    assert store.get_visible_range() == (0, 3)
    assert store.get_message_by_tool_call_id(None) is None


def test_store_scroll_threshold_helpers() -> None:
    store = MessageStore()
    assert store.should_hydrate_above(scroll_position=0, viewport_height=10) is False

    store.bulk_load([_message(i) for i in range(60)])

    assert store.should_hydrate_above(scroll_position=10, viewport_height=10) is True
    assert store.should_hydrate_above(scroll_position=25, viewport_height=10) is False

    # No visible overflow yet, so below-pruning is disabled.
    assert (
        store.should_prune_below(
            scroll_position=0,
            viewport_height=10,
            content_height=200,
        )
        is False
    )

    store.append(_message(60))
    assert (
        store.should_prune_below(
            scroll_position=0,
            viewport_height=10,
            content_height=200,
        )
        is True
    )
    assert (
        store.should_prune_below(
            scroll_position=180,
            viewport_height=10,
            content_height=200,
        )
        is False
    )


def test_append_logs_large_window_overflow(caplog: pytest.LogCaptureFixture) -> None:
    store = MessageStore()
    with caplog.at_level("WARNING"):
        for index in range(MessageStore.WINDOW_SIZE + 6):
            store.append(_message(index))

    assert "window overflow" in caplog.text
