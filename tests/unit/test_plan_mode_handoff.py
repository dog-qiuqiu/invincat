from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from invincat_cli.plan_mode.handoff import build_plan_handoff_prompt


def test_handoff_includes_original_request_and_approved_plan_only() -> None:
    prompt = build_plan_handoff_prompt(
        [
            {
                "content": "Implement API endpoint",
                "status": "in_progress",
                "verification": "pytest tests/test_api.py",
                "risk": "medium",
            }
        ],
        planner_state_values={
            "messages": [
                HumanMessage(content="Add an API endpoint"),
                AIMessage(content="I will keep it minimal."),
            ]
        },
        refinement_notes=["Keep public API stable"],
    )

    assert "[approved_plan_handoff]" in prompt
    assert "original_user_request:" in prompt
    assert "Add an API endpoint" in prompt
    assert "refinement_notes:" in prompt
    assert "Keep public API stable" in prompt
    assert "approved_plan:" in prompt
    assert "verification: pytest tests/test_api.py" in prompt
    assert "risk: medium" in prompt
    assert "I will keep it minimal" not in prompt
    assert "Do not re-plan the same work" in prompt

