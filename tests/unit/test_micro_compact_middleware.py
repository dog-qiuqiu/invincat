"""Tests for rule-based micro-compaction of old tool outputs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from invincat_cli.middleware import micro_compact as mc


def _tool_group(name: str, content: str, index: int) -> list[object]:
    call_id = f"call-{index}"
    return [
        AIMessage(
            content="",
            tool_calls=[
                {"name": name, "args": {}, "id": call_id, "type": "tool_call"}
            ],
        ),
        ToolMessage(content=content, name=name, tool_call_id=call_id),
    ]


def test_micro_compact_keeps_recent_groups_and_uses_light_near_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mc, "BASE_KEEP_RECENT_GROUPS", 1)
    monkeypatch.setattr(mc, "MAX_KEEP_RECENT_GROUPS", 1)
    monkeypatch.setattr(mc, "LIGHT_NEAR_CUTOFF_GROUPS", 1)
    monkeypatch.setattr(mc, "MIN_COMPRESS_CHARS", 10)

    old_content = "old head\n" + ("x" * 40)
    near_content = "near head\nmiddle\nnear tail"
    recent_content = "recent\n" + ("z" * 40)
    messages = [
        HumanMessage(content="start"),
        *_tool_group("read_file", old_content, 1),
        *_tool_group("execute", near_content, 2),
        *_tool_group("read_file", recent_content, 3),
    ]

    trimmed, cleared = mc.micro_compact_messages(messages)

    assert cleared == 2
    assert trimmed is not messages
    assert messages[2].content == old_content
    assert trimmed[2].content.startswith("[cleared-heavy")
    assert trimmed[4].content.startswith("[cleared-light")
    assert "tail=near tail" in trimmed[4].content
    assert trimmed[6] is messages[6]


def test_micro_compact_skips_small_noncompressible_and_existing_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mc, "BASE_KEEP_RECENT_GROUPS", 1)
    monkeypatch.setattr(mc, "MAX_KEEP_RECENT_GROUPS", 1)
    monkeypatch.setattr(mc, "LIGHT_NEAR_CUTOFF_GROUPS", 0)
    monkeypatch.setattr(mc, "MIN_COMPRESS_CHARS", 20)

    small = "short"
    legacy = "[cleared - old placeholder]"
    heavy = "[cleared-heavy - read_file, 1 line]"
    messages = [
        *_tool_group("read_file", small, 1),
        *_tool_group("ask_user", "important answer" * 10, 2),
        *_tool_group("read_file", legacy, 3),
        *_tool_group("read_file", heavy, 4),
        *_tool_group("read_file", "recent" * 20, 5),
    ]

    trimmed, cleared = mc.micro_compact_messages(messages)

    assert cleared == 0
    assert trimmed is not messages
    assert [trimmed[i].content for i in (1, 3, 5, 7, 9)] == [
        small,
        "important answer" * 10,
        legacy,
        heavy,
        "recent" * 20,
    ]


def test_micro_compact_upgrades_light_placeholder_when_group_ages_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mc, "BASE_KEEP_RECENT_GROUPS", 1)
    monkeypatch.setattr(mc, "MAX_KEEP_RECENT_GROUPS", 1)
    monkeypatch.setattr(mc, "LIGHT_NEAR_CUTOFF_GROUPS", 1)
    monkeypatch.setattr(mc, "MIN_COMPRESS_CHARS", 0)

    light = "[cleared-light - read_file, 3 lines: head=x | tail=y]"
    messages = [
        *_tool_group("read_file", light, 1),
        *_tool_group("read_file", "near\n" + ("n" * 30), 2),
        *_tool_group("read_file", "recent\n" + ("r" * 30), 3),
    ]

    trimmed, cleared = mc.micro_compact_messages(messages)

    assert cleared == 2
    assert trimmed[1].content.startswith("[cleared-heavy")
    assert trimmed[3].content.startswith("[cleared-light")


def test_micro_compact_returns_original_when_no_old_groups() -> None:
    messages = [*_tool_group("read_file", "x" * 500, 1)]

    trimmed, cleared = mc.micro_compact_messages(messages)

    assert cleared == 0
    assert trimmed is messages


def test_resolve_keep_recent_groups_scales_and_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mc, "BASE_KEEP_RECENT_GROUPS", 2)
    monkeypatch.setattr(mc, "DYNAMIC_GROUP_FACTOR", 3)
    monkeypatch.setattr(mc, "MAX_KEEP_RECENT_GROUPS", 5)

    assert mc._resolve_keep_recent_groups(total_groups=2, total_messages=10) == 2
    assert mc._resolve_keep_recent_groups(total_groups=9, total_messages=10) == 5
    assert mc._resolve_keep_recent_groups(total_groups=3, total_messages=120) == 4


def test_middleware_overrides_only_messages_when_compaction_occurs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mc, "BASE_KEEP_RECENT_GROUPS", 1)
    monkeypatch.setattr(mc, "MAX_KEEP_RECENT_GROUPS", 1)
    monkeypatch.setattr(mc, "MIN_COMPRESS_CHARS", 1)

    class Request:
        def __init__(self, messages: list[object]) -> None:
            self.state = {"messages": messages, "other": "kept"}
            self.system_prompt = None
            self.override_args: dict[str, object] | None = None

        def override(self, **kwargs: object) -> Request:
            clone = Request(list(self.state["messages"]))
            clone.state = dict(self.state)
            clone.override_args = kwargs
            return clone

    request = Request(
        [
            *_tool_group("read_file", "old content", 1),
            *_tool_group("read_file", "recent content", 2),
        ]
    )

    modified = mc.MicroCompactMiddleware()._apply(request)

    assert modified is not None
    assert modified is not request
    assert modified.override_args == {"system_prompt": ""}
    assert modified.state["other"] == "kept"
    assert modified.state["messages"][1].content.startswith("[cleared-light")


def test_middleware_returns_none_when_compaction_not_needed() -> None:
    request = SimpleNamespace(state={"messages": []})

    assert mc.MicroCompactMiddleware()._apply(request) is None
