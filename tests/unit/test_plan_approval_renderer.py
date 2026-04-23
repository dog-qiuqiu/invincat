from __future__ import annotations

from invincat_cli.widgets.tool_renderers import get_renderer
from invincat_cli.widgets.tool_widgets import PlanApprovalWidget


def test_approve_plan_uses_plan_widget_renderer() -> None:
    renderer = get_renderer("approve_plan")
    widget_cls, data = renderer.get_approval_widget(
        {
            "todos": [
                {"content": "Refactor planner loop", "status": "in_progress"},
                {"content": "Add approval tests", "status": "pending"},
            ]
        }
    )

    assert widget_cls is PlanApprovalWidget
    assert data["todos"][0]["content"] == "Refactor planner loop"
    assert data["todos"][1]["status"] == "pending"
