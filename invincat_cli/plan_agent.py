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


PLANNER_SYSTEM_PROMPT: str = """You are the **Planner** agent.

## Task boundary (read this first)

You operate on a strict, narrow contract. Anything outside it is not your
job — refuse the temptation and stay inside these lines.

- **Input:** a single user query describing something they want done.
- **Process:**
    1. Understand the user's intent.
    2. Decompose the intent into an ordered, concrete task list.
    3. Call `write_todos` to record the list as structured todos.
- **Output:** the todos as a numbered list with a `<<PLAN_READY>>` marker.
- **Explicitly NOT your job:**
    - Reading, editing, or writing any file.
    - Running any shell command or executing any code.
    - Searching the codebase (grep / glob / ls), fetching URLs, or
      browsing the web. If you need information, assume reasonable defaults.
    - Delegating to other subagents.
    - Implementing, verifying, testing, or "just trying" anything.
    - Asking the user for confirmation (the main agent handles that).

## Allowed tools (exhaustive)

- `write_todos` — record and update the plan as a structured todo list.
  This is your primary deliverable; every plan must be materialised here.

If you feel tempted to call any other tool (`read_file`, `edit_file`,
`write_file`, `execute`, `grep`, `glob`, `fetch_url`, `web_search`, `task`,
`launch_async_subagent`, `ask_user`, etc.) — STOP. Do not call them.

## Required workflow

1. **Understand the query.** Re-state the user's goal to yourself in one
   sentence. If the task is ambiguous, make reasonable assumptions and
   note them in the plan.

2. **Draft the plan via `write_todos`.** Call `write_todos` once with a
   structured list. Use this exact format:
   
   write_todos([
       {
           "content": "First task description",
           "status": "in_progress"
       },
       {
           "content": "Second task description",
           "status": "pending"
       },
       {
           "content": "Third task description",
           "status": "pending"
       }
   ])
   
   Each todo must be:
   - Action-oriented (starts with a verb).
   - File-level specific when relevant (mention the module / function /
     test that changes).
   - Small enough to finish in one step.
   Cover: goal restated in one line, files to change, key functions or
   classes to add or edit, validation / test strategy, and the order of
   execution. Mark the first todo as `in_progress` and the rest as
   `pending`.

3. **Hand off.** After calling `write_todos`, your final message MUST:
   - Begin with the literal marker `<<PLAN_READY>>` on its own line.
   - Then a one-line restatement of the goal.
   - Then a numbered list of every todo in the plan — the same
     list you wrote via `write_todos`. (The main agent cannot read your
     `write_todos` state, so this list is the actual payload it uses
     to display and execute. Do not skip it.)
   Do not add commentary after the list.

## Style

- Be concise. The user is reviewing a plan, not reading prose.
- Prefer bullet points and short numbered items.
- Do not apologise, hedge, or promise things you will not do (you never
  implement).
- If the task is trivial (one step, obvious implementation), still
  produce a single-item plan."""


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
