"""Tests for pure plan-mode runtime helpers."""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from invincat_cli.app_runtime.plan import (
    build_plan_handoff_prompt,
    build_plan_text,
    build_planner_system_prompt,
    build_planner_turn_input,
    extract_latest_ai_text,
    extract_latest_human_text,
    extract_todos_from_state,
    latest_ai_text_after_latest_tool,
    looks_cjk_text,
    normalize_state_messages,
    planner_turn_approve_plan_decision,
    planner_turn_has_approve_plan,
    planner_turn_has_write_todos,
    prefer_zh_for_text,
)


def test_build_planner_turn_input() -> None:
    prompt = build_planner_turn_input(task="  Draft the plan  ", cwd="/repo")

    assert "[planner_runtime_context]" in prompt
    assert "cwd: `/repo`" in prompt
    assert "[user_task]\nDraft the plan" in prompt


def test_build_planner_system_prompt() -> None:
    prompt = build_planner_system_prompt(base_prompt="Base", cwd="/repo")

    assert prompt.startswith("Base\n\n")
    assert "root_context_dir: `/repo`" in prompt


def test_build_plan_text() -> None:
    assert (
        build_plan_text(
            [
                {"content": "Implement", "status": "pending"},
                {"content": "Test", "status": "pending"},
            ]
        )
        == "1. Implement\n2. Test"
    )


def test_plan_runtime_detects_latest_turn_tool_state() -> None:
    messages = [
        HumanMessage(content="make a plan"),
        ToolMessage("todos recorded", tool_call_id="write-1", name="write_todos"),
        ToolMessage("approved", tool_call_id="approve-1", name="approve_plan"),
    ]

    assert planner_turn_has_write_todos(messages) is True
    assert planner_turn_approve_plan_decision(messages) == "approved"
    assert latest_ai_text_after_latest_tool(messages, "approve_plan") == ""


def test_plan_runtime_extracts_todos_from_state() -> None:
    todos = extract_todos_from_state(
        {
            "todos": [
                {"content": "Implement feature", "status": "in_progress"},
                {"content": "", "status": "pending"},
                "invalid",
            ]
        }
    )

    assert todos == [{"content": "Implement feature", "status": "in_progress"}]


def test_build_plan_handoff_prompt_keeps_user_context() -> None:
    state = {"messages": [HumanMessage(content="Refactor the scheduler")]}

    prompt = build_plan_handoff_prompt(
        [{"content": "Extract scheduler payload logic", "status": "pending"}],
        planner_state_values=state,
    )

    assert "execute_approved_plan" in prompt
    assert "Extract scheduler payload logic" in prompt
    assert "Refactor the scheduler" in prompt


def test_build_plan_handoff_prompt_uses_chinese_for_cjk_context() -> None:
    state = {"messages": [HumanMessage(content="重构调度器")]}

    prompt = build_plan_handoff_prompt(
        [{"content": "提取调度 payload 逻辑", "status": "pending"}],
        planner_state_values=state,
    )

    assert "请立即执行以下已批准计划" in prompt
    assert "规划阶段关键上下文" in prompt
    assert "重构调度器" in prompt


def test_normalize_state_messages_converts_dict_messages() -> None:
    messages = normalize_state_messages([{"type": "human", "content": "hello"}])

    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)


def test_plan_runtime_text_extraction_and_language_helpers(monkeypatch) -> None:
    messages = [
        HumanMessage(content=[{"type": "text", "text": " first "}, "topic"]),
        AIMessage(content=[{"type": "text", "text": " plan "}, "summary"]),
    ]

    assert extract_latest_human_text(messages) == "first \ntopic"
    assert extract_latest_ai_text(messages) == "plan \nsummary"
    assert extract_latest_ai_text([AIMessage(content=[])]) == ""
    assert extract_latest_human_text([HumanMessage(content=[])]) == ""
    assert looks_cjk_text("hello 世界")
    assert prefer_zh_for_text("中文") is True

    class I18n:
        language = object()

    monkeypatch.setattr("invincat_cli.i18n.get_i18n", lambda: I18n())
    monkeypatch.setattr("invincat_cli.i18n.Language", SimpleNamespace(ZH=I18n.language))

    assert prefer_zh_for_text("plain") is True

    def fail_i18n() -> object:
        raise RuntimeError("i18n unavailable")

    monkeypatch.setattr("invincat_cli.i18n.get_i18n", fail_i18n)

    assert prefer_zh_for_text("plain") is False


def test_extract_latest_ai_text_handles_non_ai_and_string_content() -> None:
    messages = [
        HumanMessage(content="human"),
        AIMessage(content="  latest answer  "),
    ]

    assert extract_latest_ai_text(messages) == "latest answer"
    assert (
        extract_latest_ai_text([AIMessage(content=[]), HumanMessage(content="skip")])
        == ""
    )


def test_normalize_state_messages_rejects_non_list() -> None:
    assert normalize_state_messages("not messages") == []


def test_plan_runtime_latest_turn_detection_variants() -> None:
    messages = [
        HumanMessage(content="first"),
        ToolMessage("todos recorded", tool_call_id="write-1", name="write_todos"),
        AIMessage(content="after write"),
        HumanMessage(content="new turn"),
        AIMessage(content="no tools"),
    ]

    assert planner_turn_has_write_todos(messages) is False
    assert planner_turn_has_approve_plan(messages) is False
    assert planner_turn_approve_plan_decision(messages) is None

    approved_list = [
        HumanMessage(content="plan"),
        ToolMessage(
            [{"type": "text", "text": ""}, "approved"],
            tool_call_id="approve-1",
            name="approve_plan",
        ),
    ]
    assert planner_turn_has_approve_plan(approved_list) is True
    assert planner_turn_approve_plan_decision(approved_list) == "approved"

    other_tool_then_rejected = [
        HumanMessage(content="plan"),
        ToolMessage("no", tool_call_id="approve-1", name="approve_plan"),
        ToolMessage("done", tool_call_id="write-1", name="write_todos"),
    ]
    assert planner_turn_approve_plan_decision(other_tool_then_rejected) == "rejected"

    rejected_other_content = ToolMessage(
        "no", tool_call_id="approve-2", name="approve_plan"
    )
    object.__setattr__(rejected_other_content, "content", {"decision": "no"})
    assert (
        planner_turn_approve_plan_decision(
            [HumanMessage(content="plan"), rejected_other_content]
        )
        == "rejected"
    )

    assistant_after_tool = [
        HumanMessage(content="plan"),
        ToolMessage("done", tool_call_id="write-1", name="write_todos"),
        AIMessage(content="first note"),
        AIMessage(content=[{"type": "text", "text": "second"}, "note"]),
    ]
    assert latest_ai_text_after_latest_tool(assistant_after_tool, "write_todos") == (
        "first note\nsecond\nnote"
    )

    assert (
        latest_ai_text_after_latest_tool(
            [
                HumanMessage(content="plan"),
                AIMessage(content="old"),
                ToolMessage("noop", tool_call_id="other-1", name="other_tool"),
            ],
            "write_todos",
        )
        == "old"
    )


def test_extract_todos_from_state_handles_missing_todos() -> None:
    assert extract_todos_from_state({}) == []
