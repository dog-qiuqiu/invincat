"""Tests for the dedicated planner subagent."""

from __future__ import annotations

from invincat_cli.plan_agent import (
    PLANNER_ALLOWED_TOOLS,
    PLANNER_SUBAGENT_NAME,
    PLANNER_SYSTEM_PROMPT,
    PlannerToolAllowListMiddleware,
    PlannerVisibleToolsMiddleware,
    build_planner_input,
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
        for forbidden in ["edit_file", "write_file", "execute", "task"]:
            assert forbidden in PLANNER_SYSTEM_PROMPT, (
                f"system prompt should forbid {forbidden}"
            )

    def test_prompt_describes_confirmation_loop(self) -> None:
        assert "ask_user" in PLANNER_SYSTEM_PROMPT
        assert "Discuss/refine the plan with the user if needed" in PLANNER_SYSTEM_PROMPT
        assert "approve_plan" in PLANNER_SYSTEM_PROMPT

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
