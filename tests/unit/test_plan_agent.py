"""Tests for the dedicated planner subagent."""

from __future__ import annotations

from invincat_cli.plan_agent import (
    PLAN_APPROVED_MARKER,
    PLANNER_ALLOWED_TOOLS,
    PLANNER_DESCRIPTION,
    PLANNER_SUBAGENT_NAME,
    PLANNER_SYSTEM_PROMPT,
    build_plan_directive,
    build_planner_subagent,
)


class TestPlannerConstants:
    def test_subagent_name_is_planner(self) -> None:
        assert PLANNER_SUBAGENT_NAME == "planner"

    def test_handoff_marker_is_distinct(self) -> None:
        # The main agent keys off this marker to decide whether to execute
        # the plan, so it must be unmistakable.
        assert PLAN_APPROVED_MARKER == "<<PLAN_APPROVED>>"
        assert PLAN_APPROVED_MARKER not in PLANNER_DESCRIPTION

    def test_allowed_tools_cover_write_todos_and_ask_user(self) -> None:
        assert set(PLANNER_ALLOWED_TOOLS) == {"write_todos", "ask_user"}


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
        # The three canonical choices should appear verbatim so the model
        # produces a stable multiple-choice question.
        assert "Approve and execute" in PLANNER_SYSTEM_PROMPT
        assert "Refine" in PLANNER_SYSTEM_PROMPT
        assert "Cancel" in PLANNER_SYSTEM_PROMPT

    def test_prompt_mentions_handoff_marker(self) -> None:
        assert PLAN_APPROVED_MARKER in PLANNER_SYSTEM_PROMPT

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


class TestBuildPlannerSubagent:
    def test_shape_matches_subagent_typeddict(self) -> None:
        spec = build_planner_subagent()
        assert spec["name"] == PLANNER_SUBAGENT_NAME
        assert spec["description"] == PLANNER_DESCRIPTION
        assert spec["system_prompt"] == PLANNER_SYSTEM_PROMPT

    def test_does_not_pin_tools_or_middleware(self) -> None:
        # The planner inherits the main agent's tool catalogue and is steered
        # by its system prompt. Pinning `tools=` or `middleware=` here would
        # require reimplementing AskUserMiddleware tool injection for the
        # subagent context (see plan_agent.py docstring).
        spec = build_planner_subagent()
        assert "tools" not in spec
        assert "middleware" not in spec

    def test_description_flags_it_as_read_only(self) -> None:
        # The main agent should understand from the description alone that
        # this subagent only plans — it never edits or executes.
        lowered = PLANNER_DESCRIPTION.lower()
        assert "plan" in lowered
        assert "never" in lowered or "only" in lowered


class TestBuildPlanDirective:
    def test_contains_task_text(self) -> None:
        directive = build_plan_directive("add a dark mode toggle")
        assert "add a dark mode toggle" in directive

    def test_names_planner_subagent(self) -> None:
        directive = build_plan_directive("anything")
        assert PLANNER_SUBAGENT_NAME in directive

    def test_references_handoff_marker(self) -> None:
        # The main agent keys off the marker to decide whether the plan was
        # approved; the directive must document that contract.
        directive = build_plan_directive("anything")
        assert PLAN_APPROVED_MARKER in directive

    def test_strips_surrounding_whitespace(self) -> None:
        directive = build_plan_directive("   refactor auth module   ")
        assert "refactor auth module" in directive
        assert "   refactor auth module   " not in directive

    def test_instructs_main_agent_to_execute_on_approval(self) -> None:
        directive = build_plan_directive("ship it").lower()
        assert "implement" in directive or "execute" in directive

    def test_instructs_main_agent_to_rehydrate_todos(self) -> None:
        # The planner's `todos` channel is filtered out by the subagent
        # boundary (see deepagents _EXCLUDED_STATE_KEYS). The directive
        # must tell the main agent to re-record the approved list via its
        # own `write_todos` so the checkpoint / progress UI reflects it.
        directive = build_plan_directive("ship it")
        assert "write_todos" in directive

    def test_instructs_main_agent_to_stop_on_non_approval(self) -> None:
        directive = build_plan_directive("ship it").lower()
        assert "stop" in directive or "do not" in directive
