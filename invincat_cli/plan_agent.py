"""Planner mode middleware and helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from langgraph.prebuilt.tool_node import ToolCallRequest

PLANNER_SUBAGENT_NAME = "planner"
"""Canonical planner subagent name used in directives and metadata."""

PLANNER_ALLOWED_TOOLS: tuple[str, ...] = (
    "read_file",
    "ls",
    "glob",
    "grep",
    "web_search",
    "fetch_url",
    "write_todos",
    "ask_user",
    "approve_plan",
)
"""Planner-visible read/planning tool contract documented in the system prompt."""

PLANNER_SYSTEM_PROMPT: str = f"""You are a task planning agent. Your ONLY job is to create structured task plans.

## Task boundary

Input is the user's query and intent.
Output is a structured plan recorded via write_todos.

## Your Task

1. Understand the user's request
2. Use read-only tools only when extra context is needed
3. Discuss/refine the plan with the user if needed (use ask_user when ambiguity blocks planning)
4. When the plan is ready, call `write_todos` exactly once with the final checklist
5. Immediately call `approve_plan` with the same todo list
6. After approval, return a concise numbered summary of the same plan
7. Do NOT execute implementation tasks

## Rules

- You may use read-only tools to gather planning context: {", ".join(PLANNER_ALLOWED_TOOLS)}
- Do NOT edit files, run commands, call task, edit_file, write_file, or execute
- Ask clarifying questions only when necessary; otherwise make reasonable assumptions
- Focus on planning, not implementation
- Respond in the same language as the user's input

## Output Format

First call `write_todos` with the final checklist.
Then call `approve_plan` with the exact same todo list.
Only after approval should you output a numbered plan:

1. First task
2. Second task
3. Third task

The runtime will interrupt immediately on `approve_plan`.

## write_todos Example

```
write_todos([
    {{"content": "First task description", "status": "in_progress"}},
    {{"content": "Second task description", "status": "pending"}},
    {{"content": "Third task description", "status": "pending"}}
])
```

Each task should be:
- Action-oriented (starts with a verb)
- Specific and achievable
- Ordered by execution sequence

Mark the first task as "in_progress", others as "pending"."""


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


def build_planner_input(task: str, refinement_notes: list[str] | None = None) -> str:
    """Build planner input text from the base task and optional refinements."""
    normalized_task = task.strip()
    notes = [note.strip() for note in (refinement_notes or []) if note and note.strip()]
    if not notes:
        return normalized_task

    rendered_notes = "\n".join(f"- {note}" for note in notes)
    return (
        "Original task:\n"
        f"{normalized_task}\n\n"
        "User refinement feedback (apply all items when revising the plan):\n"
        f"{rendered_notes}\n\n"
        "Return a revised plan with write_todos and keep tasks actionable."
    )


_TODO_PATTERN = re.compile(r"^\s*(\d+)\.\s+(.+)$")


def extract_todos_from_message(message: str) -> list[dict[str, str]] | None:
    """Extract todo items from planner's output message.

    Args:
        message: The planner's final message containing the plan.

    Returns:
        List of todo dicts with 'content' and 'status' keys, or None if
        extraction fails.
    """
    lines = message.split("\n")
    todos: list[dict[str, str]] = []

    for line in lines:
        match = _TODO_PATTERN.match(line)
        if match:
            content = match.group(2).strip()
            if content:
                todos.append({
                    "content": content,
                    "status": "in_progress" if len(todos) == 0 else "pending",
                })

    return todos if todos else None
