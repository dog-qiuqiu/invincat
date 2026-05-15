from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import SystemMessage

from invincat_cli import approve_plan
from invincat_cli.approve_plan import (
    ApprovePlanMiddleware,
    _parse_approval_response,
    _validate_todos,
)


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


def test_approve_plan_tool_interrupts_and_parses_response(monkeypatch) -> None:
    seen: list[dict[str, object]] = []

    def interrupt(request: dict[str, object]) -> dict[str, str]:
        seen.append(request)
        return {"type": "approved"}

    monkeypatch.setattr(approve_plan, "interrupt", interrupt)
    tool = ApprovePlanMiddleware().tools[0]

    command = tool.func(  # type: ignore[misc]
        [{"content": "Implement", "status": "pending"}],
        tool_call_id="call-1",
    )

    assert seen == [
        {
            "type": "approve_plan",
            "todos": [{"content": "Implement", "status": "pending"}],
            "tool_call_id": "call-1",
        }
    ]
    assert command.update["messages"][0].content == "approved"


def test_validate_todos_accepts_valid_statuses() -> None:
    _validate_todos(
        [
            {"content": "Plan", "status": "pending"},
            {"content": "Build", "status": "in_progress"},
            {"content": "Done", "status": "completed"},
        ]
    )


@pytest.mark.parametrize(
    ("todos", "message"),
    [
        ([], "cannot be empty"),
        ([{"status": "pending"}], "missing 'content'"),
        ([{"content": "Plan"}], "missing 'status'"),
        ([{"content": "Plan", "status": "blocked"}], "invalid status"),
    ],
)
def test_validate_todos_rejects_invalid_items(todos: list[dict], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _validate_todos(todos)


class FakeRequest:
    def __init__(self, system_message: SystemMessage | None = None) -> None:
        self.system_message = system_message

    def override(self, *, system_message: SystemMessage) -> FakeRequest:
        return FakeRequest(system_message)


def test_wrap_tool_call_passes_through_handler() -> None:
    middleware = ApprovePlanMiddleware()
    request = object()
    seen: list[object] = []

    def handler(value: object) -> str:
        seen.append(value)
        return "tool-result"

    assert middleware.wrap_tool_call(request, handler) == "tool-result"  # type: ignore[arg-type]
    assert seen == [request]


def test_wrap_model_call_injects_prompt_without_existing_system_message() -> None:
    middleware = ApprovePlanMiddleware(system_prompt="APPROVE")
    request = FakeRequest()
    seen: list[FakeRequest] = []

    def handler(value: FakeRequest) -> str:
        seen.append(value)
        return "model-result"

    assert middleware.wrap_model_call(request, handler) == "model-result"
    assert seen[0].system_message is not None
    assert seen[0].system_message.content == [{"type": "text", "text": "APPROVE"}]


def test_wrap_model_call_appends_prompt_to_existing_system_message() -> None:
    middleware = ApprovePlanMiddleware(system_prompt="APPROVE")
    request = FakeRequest(SystemMessage(content=[{"type": "text", "text": "base"}]))
    seen: list[FakeRequest] = []

    def handler(value: FakeRequest) -> str:
        seen.append(value)
        return "model-result"

    assert middleware.wrap_model_call(request, handler) == "model-result"
    assert seen[0].system_message is not None
    assert seen[0].system_message.content == [
        {"type": "text", "text": "base"},
        {"type": "text", "text": "\n\nAPPROVE"},
    ]


def test_async_wrappers_pass_through_and_inject_prompt() -> None:
    async def run() -> None:
        middleware = ApprovePlanMiddleware(system_prompt="APPROVE")
        request = object()
        seen_tool: list[object] = []

        async def tool_handler(value: object) -> str:
            seen_tool.append(value)
            return "tool-result"

        assert await middleware.awrap_tool_call(request, tool_handler) == "tool-result"  # type: ignore[arg-type]
        assert seen_tool == [request]

        model_request = FakeRequest()
        seen_model: list[FakeRequest] = []

        async def model_handler(value: FakeRequest) -> str:
            seen_model.append(value)
            return "model-result"

        assert (
            await middleware.awrap_model_call(model_request, model_handler)
            == "model-result"
        )
        assert seen_model[0].system_message is not None
        assert seen_model[0].system_message.content == [
            {"type": "text", "text": "APPROVE"}
        ]

        existing = FakeRequest(
            SystemMessage(content=[{"type": "text", "text": "base"}])
        )
        seen_existing: list[FakeRequest] = []

        async def existing_handler(value: FakeRequest) -> str:
            seen_existing.append(value)
            return "model-result"

        assert (
            await middleware.awrap_model_call(existing, existing_handler)
            == "model-result"
        )
        assert seen_existing[0].system_message is not None
        assert seen_existing[0].system_message.content == [
            {"type": "text", "text": "base"},
            {"type": "text", "text": "\n\nAPPROVE"},
        ]

    asyncio.run(run())
