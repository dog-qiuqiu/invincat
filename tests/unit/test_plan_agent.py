"""Tests for the dedicated planner subagent."""

from __future__ import annotations

import asyncio

from langchain_core.messages import ToolMessage

from invincat_cli.middleware.plan_agent import (
    PLANNER_ALLOWED_TOOLS,
    PLANNER_APPROVE_PLAN_SYSTEM_PROMPT,
    PLANNER_SUBAGENT_NAME,
    PLANNER_SYSTEM_PROMPT,
    PlannerToolAllowListMiddleware,
    PlannerVisibleToolsMiddleware,
    build_planner_input,
    extract_todos_from_message,
)


class TestPlannerConstants:
    def test_subagent_name_is_planner(self) -> None:
        assert PLANNER_SUBAGENT_NAME == "planner"

    def test_allowed_tools_cover_read_and_planning(self) -> None:
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


class TestPlannerSystemPrompt:
    def test_prompt_names_allowed_tools(self) -> None:
        for name in PLANNER_ALLOWED_TOOLS:
            assert name in PLANNER_SYSTEM_PROMPT, (
                f"system prompt should mention allowed tool {name}"
            )

    def test_prompt_forbids_common_write_tools(self) -> None:
        # Spot-check that the prompt explicitly names tools the planner
        # must NOT call, so the model understands the boundary.
        for forbidden in [
            "edit_file",
            "write_file",
            "execute",
            "task",
            "provide patches",
            "requested deliverable",
        ]:
            assert forbidden in PLANNER_SYSTEM_PROMPT, (
                f"system prompt should forbid {forbidden}"
            )

    def test_prompt_separates_planning_from_execution(self) -> None:
        lowered = PLANNER_SYSTEM_PROMPT.lower()
        assert "not the execution agent" in lowered
        assert "must not complete" in lowered
        assert "deliverable is always an approved checklist" in lowered
        assert "main agent executes" in lowered
        assert "never replace `write_todos`/`approve_plan`" in lowered

    def test_prompt_describes_confirmation_loop(self) -> None:
        assert "ask_user" in PLANNER_SYSTEM_PROMPT
        assert (
            "Discuss/refine the plan with the user if needed" in PLANNER_SYSTEM_PROMPT
        )
        assert "approve_plan" in PLANNER_SYSTEM_PROMPT
        assert "If approval is rejected" in PLANNER_SYSTEM_PROMPT
        assert "rejected plan is not a completed turn" in PLANNER_SYSTEM_PROMPT

    def test_approval_prompt_keeps_rejected_plans_in_planning_loop(self) -> None:
        assert "stay in planning mode" in PLANNER_APPROVE_PLAN_SYSTEM_PROMPT
        assert "call `approve_plan` again" in PLANNER_APPROVE_PLAN_SYSTEM_PROMPT
        assert "Do NOT start the first task" in PLANNER_APPROVE_PLAN_SYSTEM_PROMPT

    def test_prompt_mentions_interrupt_flow(self) -> None:
        assert "write_todos" in PLANNER_SYSTEM_PROMPT
        assert "interrupt immediately" in PLANNER_SYSTEM_PROMPT

    def test_prompt_declares_task_boundary(self) -> None:
        # The opening section must make the planner's narrow contract
        # unmistakable: input = user query, output = todos via write_todos.
        # Without this, the model drifts into reading files or "just
        # trying" things.
        assert "Task boundary" in PLANNER_SYSTEM_PROMPT
        lowered = PLANNER_SYSTEM_PROMPT.lower()
        assert "user query" in lowered or "user's query" in lowered
        assert "intent" in lowered
        assert "write_todos" in PLANNER_SYSTEM_PROMPT


class TestBuildPlannerInput:
    def test_no_refinement_returns_trimmed_task(self) -> None:
        assert build_planner_input("  refactor auth  ") == "refactor auth"

    def test_refinement_includes_original_task_and_feedback(self) -> None:
        payload = build_planner_input(
            "refactor auth",
            ["split service layer", "keep public API unchanged"],
        )
        assert "Original task" in payload
        assert "refactor auth" in payload
        assert "- split service layer" in payload
        assert "- keep public API unchanged" in payload


class TestPlannerToolAllowListMiddleware:
    def test_rejects_write_file(self) -> None:
        middleware = PlannerToolAllowListMiddleware(set(PLANNER_ALLOWED_TOOLS))
        request = type(
            "Req",
            (),
            {"tool_call": {"name": "write_file", "id": "tc1", "args": {}}},
        )()
        rejection = middleware._reject_if_disallowed(request)  # type: ignore[arg-type]
        assert rejection is not None
        assert "not allowed" in str(rejection.content)

    def test_allows_read_file(self) -> None:
        middleware = PlannerToolAllowListMiddleware(set(PLANNER_ALLOWED_TOOLS))
        request = type(
            "Req",
            (),
            {"tool_call": {"name": "read_file", "id": "tc2", "args": {}}},
        )()
        rejection = middleware._reject_if_disallowed(request)  # type: ignore[arg-type]
        assert rejection is None

    def test_wrap_tool_call_allows_and_rejects(self) -> None:
        middleware = PlannerToolAllowListMiddleware(set(PLANNER_ALLOWED_TOOLS))
        allowed_request = type(
            "Req",
            (),
            {"tool_call": {"name": "read_file", "id": "tc2", "args": {}}},
        )()
        rejected_request = type(
            "Req",
            (),
            {"tool_call": {"name": "write_file", "id": "tc3", "args": {}}},
        )()

        def handler(_request):  # noqa: ANN001
            return ToolMessage("ok", tool_call_id="tc2", name="read_file")

        assert middleware.wrap_tool_call(allowed_request, handler).content == "ok"  # type: ignore[arg-type]
        rejected = middleware.wrap_tool_call(rejected_request, handler)  # type: ignore[arg-type]
        assert rejected.status == "error"

    def test_awrap_tool_call_allows_and_rejects(self) -> None:
        middleware = PlannerToolAllowListMiddleware(set(PLANNER_ALLOWED_TOOLS))
        allowed_request = type(
            "Req",
            (),
            {"tool_call": {"name": "read_file", "id": "tc2", "args": {}}},
        )()
        rejected_request = type(
            "Req",
            (),
            {"tool_call": {"name": "write_file", "id": "tc3", "args": {}}},
        )()

        async def handler(_request):  # noqa: ANN001
            return ToolMessage("ok", tool_call_id="tc2", name="read_file")

        assert (
            asyncio.run(
                middleware.awrap_tool_call(allowed_request, handler)  # type: ignore[arg-type]
            ).content
            == "ok"
        )
        rejected = asyncio.run(
            middleware.awrap_tool_call(rejected_request, handler)  # type: ignore[arg-type]
        )
        assert rejected.status == "error"


class TestPlannerVisibleToolsMiddleware:
    def test_filters_out_write_tools_from_model_schema(self) -> None:
        middleware = PlannerVisibleToolsMiddleware(set(PLANNER_ALLOWED_TOOLS))

        class _Req:
            def __init__(self) -> None:
                self.tools = [
                    {"name": "read_file"},
                    {"name": "write_file"},
                    {"name": "edit_file"},
                    {"name": "write_todos"},
                    {"name": "approve_plan"},
                ]

            def override(self, **kwargs):  # noqa: ANN003
                nxt = _Req()
                nxt.tools = kwargs.get("tools", self.tools)
                return nxt

        captured: list[str] = []

        def _handler(req):  # noqa: ANN001
            captured.extend([tool.get("name", "") for tool in req.tools])
            return req

        middleware.wrap_model_call(_Req(), _handler)
        assert captured == ["read_file", "write_todos", "approve_plan"]

    def test_filters_object_tools_and_async_model_call(self) -> None:
        middleware = PlannerVisibleToolsMiddleware({"read_file"})

        class Tool:
            def __init__(self, name: str) -> None:
                self.name = name

        class _Req:
            def __init__(self) -> None:
                self.tools = [Tool("read_file"), Tool("write_file"), object()]

            def override(self, **kwargs):  # noqa: ANN003
                nxt = _Req()
                nxt.tools = kwargs.get("tools", self.tools)
                return nxt

        async def _handler(req):  # noqa: ANN001
            return [tool.name for tool in req.tools]

        assert asyncio.run(middleware.awrap_model_call(_Req(), _handler)) == [
            "read_file"
        ]


class TestExtractTodosFromMessage:
    def test_extracts_numbered_items_and_statuses(self) -> None:
        assert extract_todos_from_message("1. Implement\n2. Test") == [
            {"content": "Implement", "status": "in_progress"},
            {"content": "Test", "status": "pending"},
        ]

    def test_returns_none_without_numbered_items(self) -> None:
        assert extract_todos_from_message("No numbered plan here") is None
