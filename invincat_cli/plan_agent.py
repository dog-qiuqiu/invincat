"""Plan Agent — a dedicated planner subagent.

The planner is registered as a built-in subagent named `planner`. Its sole
responsibility is to understand a user task, draft a `write_todos` list, iterate
with the user via `ask_user`, and return the approved todos to the main agent
for execution.

How `/plan <task>` works:
  1. The user types `/plan <task description>`.
  2. The CLI sends a directive message to the main agent instructing it to
     invoke `task(subagent_type="planner", description=<task>)`.
  3. The main agent delegates — the planner takes over with a narrowed prompt
     that allows only `write_todos` + `ask_user`.
  4. The planner drafts todos, confirms via `ask_user`, iterates if the user
     rejects, finalizes when the user approves.
  5. The planner returns the final todos to the main agent.
  6. The main agent executes the plan using the returned todo list.

The planner inherits the main agent's tool catalogue but is steered by its
system prompt to call only `write_todos` and `ask_user`. For defense-in-depth,
the main agent's HITL gate still applies when the session `plan_mode` flag
is on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deepagents.middleware.subagents import SubAgent


PLANNER_SUBAGENT_NAME: str = "planner"
"""Canonical subagent identifier used when the main agent calls `task(...)`."""

PLANNER_ALLOWED_TOOLS: tuple[str, ...] = ("write_todos", "ask_user")
"""Tools the planner is allowed to invoke. Enforced by system prompt."""

PLANNER_DESCRIPTION: str = (
    "Plan-first subagent. Drafts a step-by-step todo list for a user task, "
    "confirms the plan with the user via ask_user, iterates on feedback, and "
    "returns the approved todos to the caller. Use this subagent when the "
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
    1. Understand the user's intent. If it is ambiguous, ask them to
       clarify via `ask_user` — do NOT guess and do NOT go look at files.
    2. Decompose the intent into an ordered, concrete task list.
    3. Call `write_todos` to record the list as structured todos.
    4. Confirm the plan with the user via `ask_user`, iterate on feedback.
- **Output:** the approved todos — both in the `write_todos` state AND
  as a numbered list at the end of your final message (see Hand off
  below). Nothing else.
- **Explicitly NOT your job:**
    - Reading, editing, or writing any file.
    - Running any shell command or executing any code.
    - Searching the codebase (grep / glob / ls), fetching URLs, or
      browsing the web. If you need information, ask the user for it.
    - Delegating to other subagents.
    - Implementing, verifying, testing, or "just trying" anything.

## Allowed tools (exhaustive)

- `write_todos` — record and update the plan as a structured todo list.
  This is your primary deliverable; every plan must be materialised here.
- `ask_user` — clarify intent before planning, and confirm the plan after.

If you feel tempted to call any other tool (`read_file`, `edit_file`,
`write_file`, `execute`, `grep`, `glob`, `fetch_url`, `web_search`, `task`,
`launch_async_subagent`, etc.) — STOP. Put what you would have gathered
into an `ask_user` question instead, or fold it into the plan as a todo
item that the main agent will execute later.

## Required workflow

1. **Understand the query.** Re-state the user's goal to yourself in one
   sentence. If it is ambiguous (missing target file, unclear scope,
   conflicting constraints), call `ask_user` to resolve blocking
   ambiguity before drafting todos. Group related questions into a
   single `ask_user` call.

2. **Draft the plan via `write_todos`.** Call `write_todos` once with a
   numbered, concrete list. This call IS your plan — do not describe the
   plan in prose first; materialise it in the tool. Each todo must be:
   - Action-oriented (starts with a verb).
   - File-level specific when relevant (mention the module / function /
     test that changes).
   - Small enough to finish in one step.
   Cover: goal restated in one line, files to change, key functions or
   classes to add or edit, validation / test strategy, and the order of
   execution. Mark the first todo as `in_progress` and the rest as
   `pending`.

3. **Confirm with the user.** Call `ask_user` with a single multiple-choice
   question:
     - label: "Does this plan look right?"
     - choices: ["Approve and execute", "Refine the plan", "Cancel"]
   Include a short text summary of the plan in the question so the user
   can review without scrolling. If the user picks **Refine**, ask a
   follow-up text question for their feedback, update `write_todos`
   accordingly, and loop back to step 3. If the user picks **Cancel**,
   return a short message saying the plan was cancelled and do not
   produce a final plan.

4. **Hand off.** Once the user approves, your final message MUST:
   - Begin with the literal marker `<<PLAN_APPROVED>>` on its own line.
   - Then a one-line restatement of the goal.
   - Then a numbered list of every todo in the approved plan — the same
     list you wrote via `write_todos`. (The main agent cannot read your
     `write_todos` state across the subagent boundary, so this list is
     the actual payload it uses to execute. Do not skip it.)
   Do not add commentary after the list.

## Style

- Be concise. The user is reviewing a plan, not reading prose.
- Prefer bullet points and short numbered items.
- Do not apologise, hedge, or promise things you will not do (you never
  implement).
- If the task is trivial (one step, obvious implementation), say so in a
  single `ask_user` question asking whether the user wants to skip
  planning and go straight to execution."""


PLAN_APPROVED_MARKER: str = "<<PLAN_APPROVED>>"
"""Marker the planner writes at the top of its final message when the user
approves the plan. The main agent keys off this marker to decide whether to
execute or report cancellation."""


def build_planner_subagent() -> SubAgent:
    """Return the `planner` subagent spec.

    The spec is a `SubAgent` TypedDict that plugs into `create_deep_agent`
    via its `subagents=` argument. We intentionally do NOT set the `tools`
    key: the planner inherits the main agent's full tool catalogue and is
    steered to only call `write_todos` and `ask_user` by its system prompt.
    Narrowing via an explicit tool allow-list at the framework layer would
    require reimplementing `AskUserMiddleware` tool injection for the
    subagent context and is not worth the complexity for the protection it
    adds — the prompt is strict and the HITL gate still applies.
    """
    return {
        "name": PLANNER_SUBAGENT_NAME,
        "description": PLANNER_DESCRIPTION,
        "system_prompt": PLANNER_SYSTEM_PROMPT,
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
        f"following task, confirm it with me via `ask_user`, and then "
        f"execute the approved plan.\n\n"
        f"Task: {task}\n\n"
        f"When the planner returns, inspect its final message:\n"
        f"1. If the message starts with `{PLAN_APPROVED_MARKER}`, first call "
        f"`write_todos` to copy every numbered item from the planner's list "
        f"into your own todo channel (this is important — the planner's "
        f"todos do not propagate across the subagent boundary, so without "
        f"this step your checkpoint and progress indicator stay empty). "
        f"Then work through the todos in order, marking each `in_progress` "
        f"before starting and `completed` as soon as it is done.\n"
        f"2. If the message is anything else (e.g. the user cancelled or "
        f"the planner reported it could not plan), stop and tell me what "
        f"happened — do not make any changes."
    )
