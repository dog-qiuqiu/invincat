from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from invincat_cli.plan_mode.runtime import resolve_planner_turn


def test_runtime_detects_prose_only_drift() -> None:
    resolution = resolve_planner_turn(
        {"messages": []},
        messages=[
            HumanMessage(content="fix bug"),
            AIMessage(content="Here is the implementation."),
        ],
        prompted_todos_fingerprint=None,
    )

    assert resolution.kind == "drifted"
    assert resolution.drift is not None
    assert resolution.drift["reason"] == "final_answer"


def test_runtime_requires_approval_after_write_todos() -> None:
    resolution = resolve_planner_turn(
        {"todos": [{"content": "Inspect code", "status": "pending"}]},
        messages=[
            HumanMessage(content="plan"),
            ToolMessage("ok", tool_call_id="write", name="write_todos"),
        ],
        prompted_todos_fingerprint=None,
    )

    assert resolution.kind == "drifted"
    assert resolution.drift is not None
    assert resolution.drift["reason"] == "missing_approval"


def test_runtime_finalizes_approved_plan() -> None:
    resolution = resolve_planner_turn(
        {"todos": [{"content": "Implement fix", "status": "in_progress"}]},
        messages=[
            HumanMessage(content="plan"),
            ToolMessage("approved", tool_call_id="approve", name="approve_plan"),
        ],
        prompted_todos_fingerprint=None,
    )

    assert resolution.kind == "approved"
    assert resolution.todos == [
        {"content": "Implement fix", "status": "in_progress"}
    ]


def test_runtime_rejects_approved_plan_with_mismatched_todos() -> None:
    resolution = resolve_planner_turn(
        {"todos": [{"content": "Inspect code", "status": "pending"}]},
        messages=[
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
        ],
        prompted_todos_fingerprint=None,
    )

    assert resolution.kind == "drifted"
    assert resolution.drift is not None
    assert resolution.drift["reason"] == "todo_mismatch"


def test_runtime_reject_keeps_planning_loop() -> None:
    resolution = resolve_planner_turn(
        {},
        messages=[
            HumanMessage(content="plan"),
            ToolMessage("rejected", tool_call_id="approve", name="approve_plan"),
        ],
        prompted_todos_fingerprint=None,
    )

    assert resolution.kind == "rejected"
    assert resolution.suppress_refine_prompt is False
