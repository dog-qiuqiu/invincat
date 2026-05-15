"""Compatibility exports for Textual app plan-mode helpers."""

from __future__ import annotations

from invincat_cli.plan_mode.handoff import (
    build_plan_handoff_prompt,
    build_plan_text,
    looks_cjk_text,
    normalize_state_messages,
    prefer_zh_for_text,
)
from invincat_cli.plan_mode.policy import (
    ai_text_after_latest_tool as latest_ai_text_after_latest_tool,
)
from invincat_cli.plan_mode.policy import (
    approval_decision as planner_turn_approve_plan_decision,
)
from invincat_cli.plan_mode.policy import (
    extract_todos_from_message,
    extract_todos_from_state,
    turn_has_tool,
)
from invincat_cli.plan_mode.policy import (
    latest_ai_text as extract_latest_ai_text,
)
from invincat_cli.plan_mode.policy import (
    latest_human_text as extract_latest_human_text,
)
from invincat_cli.plan_mode.prompts import (
    build_planner_runtime_context,
    build_planner_system_prompt,
    build_planner_turn_input,
)

__all__ = [
    "build_plan_handoff_prompt",
    "build_plan_text",
    "build_planner_runtime_context",
    "build_planner_system_prompt",
    "build_planner_turn_input",
    "extract_latest_ai_text",
    "extract_latest_human_text",
    "extract_todos_from_message",
    "extract_todos_from_state",
    "latest_ai_text_after_latest_tool",
    "looks_cjk_text",
    "normalize_state_messages",
    "planner_turn_approve_plan_decision",
    "planner_turn_has_approve_plan",
    "planner_turn_has_write_todos",
    "prefer_zh_for_text",
]


def planner_turn_has_write_todos(messages: list[object]) -> bool:
    """Return whether the latest planner turn invoked `write_todos`."""
    return turn_has_tool(messages, "write_todos")


def planner_turn_has_approve_plan(messages: list[object]) -> bool:
    """Return whether the latest planner turn invoked `approve_plan`."""
    return turn_has_tool(messages, "approve_plan")
