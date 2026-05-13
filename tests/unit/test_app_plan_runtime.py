"""Tests for pure plan-mode runtime helpers."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, ToolMessage

from invincat_cli.app_plan_runtime import (
    build_plan_handoff_prompt,
    build_plan_text,
    build_planner_system_prompt,
    build_planner_turn_input,
    extract_todos_from_state,
    latest_ai_text_after_latest_tool,
    normalize_state_messages,
    planner_turn_approve_plan_decision,
    planner_turn_has_write_todos,
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
    assert build_plan_text(
        [
            {"content": "Implement", "status": "pending"},
            {"content": "Test", "status": "pending"},
        ]
    ) == "1. Implement\n2. Test"


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


def test_normalize_state_messages_converts_dict_messages() -> None:
    messages = normalize_state_messages(
        [{"type": "human", "content": "hello"}]
    )

    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)
