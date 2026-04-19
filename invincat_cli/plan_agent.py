"""Plan Agent — a standalone agent for task planning.

The planner is a dedicated agent that:
1. Understands user requirements
2. Generates structured todo lists via write_todos
3. Returns the plan for user approval

How `/plan <task>` works:
  1. The user types `/plan <task description>`.
  2. The CLI creates a planner agent and invokes it with the task.
  3. The planner generates a todo list via write_todos tool.
  4. The CLI displays the approve widget for user confirmation.
  5. If approved, the main agent executes the plan.
  6. If rejected, the CLI asks for feedback and re-invokes the planner.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langgraph.graph.state import CompiledStateGraph


PLANNER_SYSTEM_PROMPT: str = """You are a task planning agent. Your ONLY job is to create structured task plans.

## Your Task

1. Understand the user's request
2. Break it down into actionable steps
3. Call `write_todos` tool to record the plan
4. Output the plan as a numbered list

## Rules

- You can ONLY use the `write_todos` tool
- Do NOT read files, edit code, run commands, or search the web
- Do NOT ask questions - make reasonable assumptions
- Focus on planning, not implementation

## Output Format

After calling `write_todos`, output a numbered list:

1. First task
2. Second task
3. Third task

## write_todos Example

```
write_todos([
    {"content": "First task description", "status": "in_progress"},
    {"content": "Second task description", "status": "pending"},
    {"content": "Third task description", "status": "pending"}
])
```

Each task should be:
- Action-oriented (starts with a verb)
- Specific and achievable
- Ordered by execution sequence

Mark the first task as "in_progress", others as "pending"."""


def create_planner_agent(
    model: str | BaseChatModel,
    model_params: dict[str, Any] | None = None,
) -> CompiledStateGraph:
    """Create a standalone planner agent.

    The planner agent has access only to `write_todos` tool and is designed
    to generate structured task plans for user approval.

    Args:
        model: The language model to use (string identifier or BaseChatModel).
        model_params: Optional model parameters to pass to model initialization.

    Returns:
        A compiled planner agent graph.
    """
    from invincat_cli.config import create_model, settings

    if isinstance(model, str):
        settings.reload_from_environment()
        model_result = create_model(model, extra_kwargs=model_params)
        model = model_result.model

    todo_middleware = TodoListMiddleware()

    return create_agent(
        model=model,
        tools=todo_middleware.tools,
        system_prompt=PLANNER_SYSTEM_PROMPT,
        middleware=[todo_middleware],
        name="planner",
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
