"""Plan Mode — design-first workflow.

When plan mode is enabled (`/plan`), the agent is asked to draft a
step-by-step implementation plan and any tool that mutates the workspace
(`write_file`, `edit_file`, `execute`, async subagent launches, …) is
auto-rejected with a hint that steers the model back to planning. Read-only
tools (`read_file`, `grep`, `glob`, `ls`, `web_search`, `fetch_url`) are still
allowed so the planner can ground its proposal in real code.

The user exits plan mode with `/exit-plan` or by approving a presented plan
with the regular plan-mode banner instructions.
"""

from __future__ import annotations

WRITE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "write_file",
        "edit_file",
        "execute",
        "task",
        "launch_async_subagent",
        "update_async_subagent",
        "cancel_async_subagent",
        "compact_conversation",
    }
)
"""Tools that are blocked while plan mode is active.

These mutate the workspace, spend significant tokens, or fork sub-agents that
could themselves edit code. Every other gated tool (read_file, grep, glob,
ls, web_search, fetch_url, …) is left untouched so the planner can still
gather grounding information.
"""


PLAN_MODE_PREAMBLE: str = (
    "You are now in **PLAN MODE**.\n\n"
    "Rules for this turn (and every turn until the user exits plan mode):\n"
    "1. Do NOT modify the workspace. The following tools are disabled and "
    "any call to them will be rejected: "
    "`write_file`, `edit_file`, `execute`, `task`, `launch_async_subagent`, "
    "`update_async_subagent`, `cancel_async_subagent`, "
    "`compact_conversation`.\n"
    "2. You MAY use read-only tools (`read_file`, `grep`, `glob`, `ls`, "
    "`web_search`, `fetch_url`) to ground your plan in real code.\n"
    "3. Produce a numbered, file-level implementation plan covering: goal, "
    "files to change, key functions/classes, risks, test strategy, and the "
    "order in which the steps should be executed.\n"
    "4. Do not start implementing. Wait for the user to run `/exit-plan` "
    "(or otherwise approve the plan) before making any edits."
)
"""Inserted into the chat as a system-style user message when /plan starts."""


PLAN_MODE_REJECTION_HINT: str = (
    "Plan mode is active — write tools are disabled. "
    "Refine the plan instead. The user will run `/exit-plan` when ready."
)
"""Returned as the rejection reason for blocked tool calls."""


def is_write_tool(tool_name: str) -> bool:
    """Return True if `tool_name` is blocked by plan mode.

    Args:
        tool_name: Canonical tool name from a HITL action request.

    Returns:
        True when the tool mutates state and must be rejected in plan mode.
    """
    return tool_name in WRITE_TOOL_NAMES


def split_blocked_tools(
    tool_names: list[str],
) -> tuple[list[str], list[str]]:
    """Partition tool names into (blocked, allowed) for plan mode.

    Args:
        tool_names: Tool names from a batched HITL request.

    Returns:
        `(blocked, allowed)` — preserves input order in each list.
    """
    blocked = [n for n in tool_names if is_write_tool(n)]
    allowed = [n for n in tool_names if not is_write_tool(n)]
    return blocked, allowed
