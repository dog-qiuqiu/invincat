"""Planner mode middleware and helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from invincat_cli.plan_mode.policy import (
    PLANNER_ALLOWED_TOOLS,
    extract_todos_from_message,
)
from invincat_cli.plan_mode.prompts import (
    PLANNER_APPROVE_PLAN_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
)

if TYPE_CHECKING:
    from langgraph.prebuilt.tool_node import ToolCallRequest

PLANNER_SUBAGENT_NAME = "planner"
"""Canonical planner subagent name used in directives and metadata."""

__all__ = [
    "PLANNER_ALLOWED_TOOLS",
    "PLANNER_APPROVE_PLAN_SYSTEM_PROMPT",
    "PLANNER_SUBAGENT_NAME",
    "PLANNER_SYSTEM_PROMPT",
    "PlannerToolAllowListMiddleware",
    "PlannerVisibleToolsMiddleware",
    "build_planner_input",
    "extract_todos_from_message",
]

class PlannerToolAllowListMiddleware(AgentMiddleware):
    """Hard allow-list for planner tool calls.

    This enforces read-only planning boundaries at runtime, independent of prompt
    compliance.
    """

    def __init__(self, allowed_tools: set[str]) -> None:
        super().__init__()
        self._allowed_tools = set(allowed_tools)

    def _reject_if_disallowed(self, request: ToolCallRequest) -> ToolMessage | None:
        tool_name = str(request.tool_call.get("name", "")).strip()
        if tool_name in self._allowed_tools:
            return None
        allowed = ", ".join(sorted(self._allowed_tools))
        return ToolMessage(
            content=(
                f"Tool '{tool_name}' is not allowed in /plan mode. "
                f"Allowed tools: {allowed}."
            ),
            name=tool_name,
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage],
    ) -> ToolMessage:
        if (rejection := self._reject_if_disallowed(request)) is not None:
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> ToolMessage:
        if (rejection := self._reject_if_disallowed(request)) is not None:
            return rejection
        return await handler(request)


class PlannerVisibleToolsMiddleware(AgentMiddleware):
    """Filter planner-visible tool schemas at model-call time."""

    def __init__(self, visible_tools: set[str]) -> None:
        super().__init__()
        self._visible_tools = set(visible_tools)

    @staticmethod
    def _tool_name(tool: Any) -> str:  # noqa: ANN401
        if hasattr(tool, "name"):
            return str(getattr(tool, "name", "")).strip()
        if isinstance(tool, dict):
            return str(tool.get("name", "")).strip()
        return ""

    def _filter_tools(self, tools: list[Any]) -> list[Any]:  # noqa: ANN401
        return [tool for tool in tools if self._tool_name(tool) in self._visible_tools]

    def wrap_model_call(
        self,
        request: Any,  # noqa: ANN401
        handler: Callable[[Any], Any],  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        filtered_tools = self._filter_tools(list(getattr(request, "tools", [])))
        return handler(request.override(tools=filtered_tools))

    async def awrap_model_call(
        self,
        request: Any,  # noqa: ANN401
        handler: Callable[[Any], Any],  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        filtered_tools = self._filter_tools(list(getattr(request, "tools", [])))
        return await handler(request.override(tools=filtered_tools))


def build_planner_input(
    task: str,
    refinement_notes: list[str] | None = None,
    *,
    rejected_plan: list[dict[str, str]] | None = None,
) -> str:
    """Build planner input text from the base task and optional refinements."""
    normalized_task = task.strip()
    notes = [note.strip() for note in (refinement_notes or []) if note and note.strip()]
    rejected = [
        str(item.get("content", "")).strip()
        for item in (rejected_plan or [])
        if isinstance(item, dict) and str(item.get("content", "")).strip()
    ]
    if not notes and not rejected:
        return normalized_task

    rendered_notes = "\n".join(f"- {note}" for note in notes)
    rendered_rejected = "\n".join(
        f"{index}. {content}" for index, content in enumerate(rejected, start=1)
    )
    rejected_block = (
        "Previous rejected plan:\n"
        f"{rendered_rejected}\n\n"
        if rendered_rejected
        else ""
    )
    notes_block = (
        "User refinement feedback (apply all items when revising the plan):\n"
        f"{rendered_notes}\n\n"
        if rendered_notes
        else ""
    )
    return (
        "Original task:\n"
        f"{normalized_task}\n\n"
        f"{rejected_block}"
        f"{notes_block}"
        "Return a revised plan with write_todos and approve_plan."
    )
