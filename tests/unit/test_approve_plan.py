from __future__ import annotations

from invincat_cli.approve_plan import _parse_approval_response


def test_parse_approval_response_sets_matching_tool_name() -> None:
    command = _parse_approval_response({"type": "approved"}, "call_1")
    messages = command.update
    assert len(messages) == 1
    message = messages[0]
    assert message.tool_call_id == "call_1"
    assert getattr(message, "name", "") == "approve_plan"
    assert message.content == "approved"
