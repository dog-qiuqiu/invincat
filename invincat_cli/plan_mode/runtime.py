"""Pure plan-mode runtime decisions."""

from __future__ import annotations

from typing import Any

from invincat_cli.plan_mode.models import PlanTurnResolution
from invincat_cli.plan_mode.policy import (
    ai_text_after_latest_tool,
    approval_decision,
    detect_planner_drift,
    extract_todos_from_message,
    extract_todos_from_state,
    latest_ai_text,
    plan_todos_fingerprint,
    turn_has_tool,
)


def resolve_planner_turn(
    state_values: dict[str, Any],
    *,
    messages: list[Any],
    prompted_todos_fingerprint: str | None,
) -> PlanTurnResolution:
    """Resolve the latest planner turn into an app-level action."""
    decision = approval_decision(messages)
    if decision is not None:
        if decision != "approved":
            return PlanTurnResolution(
                kind="rejected",
                suppress_refine_prompt=bool(
                    ai_text_after_latest_tool(messages, "approve_plan")
                ),
            )

        drift = detect_planner_drift(messages)
        if drift is not None and drift["reason"] == "todo_mismatch":
            return PlanTurnResolution(kind="drifted", drift=drift)

        todos = extract_todos_from_state(state_values)
        if not todos:
            todos = extract_todos_from_message(latest_ai_text(messages)) or []
        if not todos:
            return PlanTurnResolution(kind="approval_no_valid_todos")
        return PlanTurnResolution(kind="approved", todos=todos)

    drift = detect_planner_drift(messages)
    if drift is not None:
        return PlanTurnResolution(kind="drifted", drift=drift)

    if not turn_has_tool(messages, "write_todos"):
        return PlanTurnResolution(kind="noop")

    todos = extract_todos_from_state(state_values)
    if not todos:
        todos = extract_todos_from_message(latest_ai_text(messages)) or []
    if not todos:
        return PlanTurnResolution(kind="ready_no_valid_todos")

    todos_fingerprint = plan_todos_fingerprint(todos)
    if todos_fingerprint == prompted_todos_fingerprint:
        return PlanTurnResolution(kind="already_prompted")

    return PlanTurnResolution(kind="prompt_todos", todos=todos)
