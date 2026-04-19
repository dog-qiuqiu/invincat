"""Plan Agent — a standalone agent for task planning.

The planner is a dedicated agent that:
1. Understands user requirements
2. Generates structured todo lists via write_todos
3. Returns the plan for user approval

How `/plan <task>` works:
  1. The user types `/plan <task description>`.
  2. The CLI creates a planner agent and invokes it with the task.
  3. The planner generates a todo list and returns with PLAN_READY_MARKER.
  4. The CLI displays the approve widget for user confirmation.
  5. If approved, the main agent executes the plan.
  6. If rejected, the CLI asks for feedback and re-invokes the planner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
4. Output the plan with `<<PLAN_READY>>` marker

## Rules

- You can ONLY use the `write_todos` tool
- Do NOT read files, edit code, run commands, or search the web
- Do NOT ask questions or seek clarification - make reasonable assumptions
- Focus on planning, not implementation

## Output Format

After calling `write_todos`, your final message must be:

```
<<PLAN_READY>>
<one-line goal summary>

1. First task
2. Second task
3. Third task
...
```

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


PLAN_READY_MARKER: str = "<<PLAN_READY>>"
"""Marker the planner writes at the top of its final message when the plan
is ready for user approval."""


def create_planner_agent(
    model: str | BaseChatModel,
) -> CompiledStateGraph:
    """Create a standalone planner agent.

    The planner agent has access only to `write_todos` tool and is designed
    to generate structured task plans for user approval.

    Args:
        model: The language model to use (string identifier or BaseChatModel).

    Returns:
        A compiled planner agent graph.
    """
    todo_middleware = TodoListMiddleware()

    return create_agent(
        model=model,
        tools=todo_middleware.tools,
        system_prompt=PLANNER_SYSTEM_PROMPT,
        middleware=[todo_middleware],
        name="planner",
    )


def extract_todos_from_message(message: str) -> list[dict[str, str]] | None:
    """Extract todo items from planner's output message.

    Args:
        message: The planner's final message containing the plan.

    Returns:
        List of todo dicts with 'content' and 'status' keys, or None if
        extraction fails.
    """
    if not message.startswith(PLAN_READY_MARKER):
        return None

    lines = message.split("\n")
    todos: list[dict[str, str]] = []
    first_todo = True

    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue

        if line.startswith(("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")):
            content = line.split(".", 1)[1].strip() if "." in line else line
            if content:
                todos.append({
                    "content": content,
                    "status": "in_progress" if first_todo else "pending",
                })
                first_todo = False

    return todos if todos else None
