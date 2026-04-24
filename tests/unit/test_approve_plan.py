from __future__ import annotations

from invincat_cli.approve_plan import ApprovePlanMiddleware, _parse_approval_response


def test_parse_approval_response_sets_matching_tool_name() -> None:
    command = _parse_approval_response({"type": "approved"}, "call_1")
    messages = command.update["messages"]
    assert len(messages) == 1
    message = messages[0]
    assert message.tool_call_id == "call_1"
    assert getattr(message, "name", "") == "approve_plan"
    assert message.content == "approved"


def test_parse_approval_response_maps_non_approved_to_rejected() -> None:
    command = _parse_approval_response({"type": "rejected"}, "call_2")
    messages = command.update["messages"]
    assert len(messages) == 1
    message = messages[0]
    assert message.tool_call_id == "call_2"
    assert getattr(message, "name", "") == "approve_plan"
    assert message.content == "rejected"


def test_approve_plan_tool_schema_hides_injected_tool_call_id() -> None:
    middleware = ApprovePlanMiddleware()
    tool = middleware.tools[0]
    assert set(tool.args.keys()) == {"todos"}
