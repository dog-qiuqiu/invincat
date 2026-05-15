from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from invincat_cli.plan_mode.models import PlanModeStatus
from invincat_cli.plan_mode.policy import (
    PLANNER_ALLOWED_TOOLS,
    detect_planner_drift,
    normalize_plan_steps,
    plan_todos_fingerprint,
)


def test_plan_mode_status_contract() -> None:
    assert PlanModeStatus.PLANNING.value == "planning"
    assert PlanModeStatus.WAITING_APPROVAL.value == "waiting_approval"
    assert PlanModeStatus.HANDOFF_PENDING.value == "handoff_pending"


def test_allowed_tools_are_planning_only() -> None:
    assert set(PLANNER_ALLOWED_TOOLS) == {
        "read_file",
        "ls",
        "glob",
        "grep",
        "web_search",
        "fetch_url",
        "write_todos",
        "ask_user",
        "approve_plan",
    }


def test_normalize_plan_steps_keeps_enhanced_fields() -> None:
    steps = normalize_plan_steps(
        [
            {
                "content": "Inspect auth flow",
                "status": "in_progress",
                "rationale": "Find the root cause",
                "target": ["auth.py"],
                "verification": "pytest tests/test_auth.py",
                "risk": "medium",
            }
        ]
    )

    assert steps == [
        {
            "id": "step-1",
            "content": "Inspect auth flow",
            "status": "in_progress",
            "rationale": "Find the root cause",
            "target": ["auth.py"],
            "verification": "pytest tests/test_auth.py",
            "risk": "medium",
        }
    ]


def test_fingerprint_uses_normalized_content_and_status() -> None:
    assert plan_todos_fingerprint([{"content": " A ", "status": ""}]) == (
        '[{"content": "A", "status": "pending"}]'
    )


def test_detect_planner_drift_for_prose_only_output() -> None:
    drift = detect_planner_drift(
        [
            HumanMessage(content="fix bug"),
            AIMessage(content="I found the issue and the fix is straightforward."),
        ]
    )

    assert drift is not None
    assert drift["reason"] == "missing_todos"


def test_detect_planner_drift_for_final_answer_content() -> None:
    drift = detect_planner_drift(
        [
            HumanMessage(content="write code"),
            AIMessage(content="下面是代码\n```python\nprint('done')\n```"),
        ]
    )

    assert drift is not None
    assert drift["reason"] == "final_answer"


def test_detect_planner_drift_for_disallowed_tool() -> None:
    drift = detect_planner_drift(
        [
            HumanMessage(content="fix"),
            ToolMessage("ok", tool_call_id="1", name="write_file"),
        ]
    )

    assert drift is not None
    assert drift["reason"] == "disallowed_tool"


def test_detect_planner_drift_for_missing_approval() -> None:
    drift = detect_planner_drift(
        [
            HumanMessage(content="plan"),
            ToolMessage("ok", tool_call_id="1", name="write_todos"),
        ]
    )

    assert drift is not None
    assert drift["reason"] == "missing_approval"


def test_detect_planner_drift_for_todo_mismatch() -> None:
    drift = detect_planner_drift(
        [
            HumanMessage(content="plan"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_todos",
                        "args": {
                            "todos": [
                                {"content": "Inspect code", "status": "pending"}
                            ]
                        },
                        "id": "write",
                    },
                    {
                        "name": "approve_plan",
                        "args": {
                            "todos": [
                                {"content": "Edit files", "status": "pending"}
                            ]
                        },
                        "id": "approve",
                    },
                ],
            ),
            ToolMessage("ok", tool_call_id="write", name="write_todos"),
            ToolMessage("approved", tool_call_id="approve", name="approve_plan"),
        ]
    )

    assert drift is not None
    assert drift["reason"] == "todo_mismatch"
