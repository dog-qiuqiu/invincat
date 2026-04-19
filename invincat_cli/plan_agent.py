"""Plan Agent — a dedicated planner subagent.

The planner is registered as a built-in subagent named `planner`. Its sole
responsibility is to understand a user task, draft a `write_todos` list,
and return the todos for user approval.

How `/plan <task>` works:
  1. The user types `/plan <task description>`.
  2. The CLI sends a directive message to the main agent instructing it to
     invoke `task(subagent_type="planner", description=<task>)`.
  3. The main agent delegates — the planner takes over with a narrowed prompt
     that allows only `write_todos`.
  4. The planner drafts todos and returns them with a `<<PLAN_READY>>` marker.
  5. The main agent displays the approve widget for user confirmation.
  6. If approved, the main agent executes the plan.
  7. If rejected, the main agent asks for feedback and re-invokes the planner.

The planner inherits the main agent's tool catalogue but is steered by its
system prompt to call only `write_todos`. For defense-in-depth,
the main agent's HITL gate still applies when the session `plan_mode` flag
is on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deepagents.middleware.subagents import SubAgent


PLANNER_SUBAGENT_NAME: str = "planner"
"""Canonical subagent identifier used when the main agent calls `task(...)`."""

PLANNER_ALLOWED_TOOLS: tuple[str, ...] = ("write_todos",)
"""Tools the planner is allowed to invoke. Enforced by system prompt."""

PLANNER_DESCRIPTION: str = (
    "Plan-first subagent. Drafts a step-by-step todo list for a user task "
    "and returns the todos for user approval. Use this subagent when the "
    "user runs /plan or explicitly asks for planning before implementation. "
    "The planner itself never edits files or runs commands — it only plans."
)
"""Shown to the main agent in the `task` tool schema so it can route correctly."""

PLANNER_SYSTEM_PROMPT: str = """You are the **Planner** subagent.

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
     `write_todos` state across the subagent boundary, so this list is
     the actual payload it uses to display and execute. Do not skip it.)
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


def build_planner_subagent() -> SubAgent:
    """Return the `planner` subagent spec.

    The spec is a `SubAgent` TypedDict that plugs into `create_deep_agent`
    via its `subagents=` argument. We now explicitly set the `tools` key
    to ensure the planner has access to write_todos.
    """
    return {
        "name": PLANNER_SUBAGENT_NAME,
        "description": PLANNER_DESCRIPTION,
        "system_prompt": PLANNER_SYSTEM_PROMPT,
        "tools": [
            {
                "name": "write_todos",
                "description": "Record and update the plan as a structured todo list",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "todos": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "content": {
                                        "type": "string",
                                        "description": "Todo item description"
                                    },
                                    "status": {
                                        "type": "string",
                                        "enum": ["pending", "in_progress", "completed"],
                                        "description": "Todo status"
                                    }
                                },
                                "required": ["content", "status"]
                            }
                        }
                    },
                    "required": ["todos"]
                }
            }
        ]
    }


def build_plan_directive(task: str) -> str:
    """Build the user-facing directive that triggers planner delegation.

    This string is sent to the main agent as a user message when the user
    runs `/plan <task>`. It uses imperative language the main agent
    reliably interprets as a `task()` delegation call.

    Args:
        task: The raw task description the user typed after `/plan`.

    Returns:
        A directive string ready to submit to the main agent.
    """
    task = task.strip()
    return (
        f"Use the `{PLANNER_SUBAGENT_NAME}` subagent to draft a plan for the "
        f"following task.\n\n"
        f"Task: {task}\n\n"
        f"When the planner returns, inspect its final message:\n"
        f"1. If the message starts with `{PLAN_READY_MARKER}`, extract the "
        f"numbered todo list from the message and display it for user approval. "
        f"Do NOT execute anything yet — wait for user confirmation.\n"
        f"2. If the message is anything else, stop and tell me what happened."
    )


def build_plan_refine_directive(task: str, feedback: str, previous_todos: list[dict]) -> str:
    """Build a directive for refining a plan based on user feedback.

    Args:
        task: The original task description.
        feedback: User's feedback on the previous plan.
        previous_todos: The previous todo list that was rejected.

    Returns:
        A directive string for plan refinement.
    """
    task = task.strip()
    previous_plan = "\n".join(
        f"{i + 1}. {todo.get('content', '')}"
        for i, todo in enumerate(previous_todos)
    )
    return (
        f"Use the `{PLANNER_SUBAGENT_NAME}` subagent to refine the plan for the "
        f"following task based on user feedback.\n\n"
        f"Original task: {task}\n\n"
        f"Previous plan:\n{previous_plan}\n\n"
        f"User feedback: {feedback}\n\n"
        f"Create an updated plan that addresses the user's feedback. "
        f"When done, return with the `{PLAN_READY_MARKER}` marker and the "
        f"updated numbered todo list."
    )
