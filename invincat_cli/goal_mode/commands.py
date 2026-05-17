"""Parsing helpers for `/goal` commands."""

from __future__ import annotations

import shlex

from invincat_cli.goal_mode.models import GoalCommand

_STATUS_ALIASES = {"status", "show", "info"}
_COMPLETE_ALIASES = {"complete", "done", "finish"}
_CANCEL_ALIASES = {"cancel", "stop", "exit"}
_CLEAR_ALIASES = {"clear", "reset"}


def parse_goal_command(command: str) -> GoalCommand:
    """Parse a `/goal` command into a normalized action."""
    text = command.strip()
    lowered = text.lower()
    if lowered == "/exit-goal":
        return GoalCommand(kind="cancel")
    if not lowered.startswith("/goal"):
        return GoalCommand(kind="error", error="Goal command must start with /goal")

    args_text = text[len("/goal") :].strip()
    if not args_text:
        return GoalCommand(kind="status")

    try:
        parts = shlex.split(args_text)
    except ValueError as exc:
        return GoalCommand(kind="error", error=str(exc))
    if not parts:
        return GoalCommand(kind="status")

    action = parts[0].lower()
    if action in _STATUS_ALIASES:
        return GoalCommand(kind="status")
    if action in _COMPLETE_ALIASES:
        summary = " ".join(parts[1:]).strip() or None
        return GoalCommand(kind="complete", objective=summary)
    if action in _CANCEL_ALIASES:
        summary = " ".join(parts[1:]).strip() or None
        return GoalCommand(kind="cancel", objective=summary)
    if action in _CLEAR_ALIASES:
        return GoalCommand(kind="clear")

    token_budget: int | None = None
    objective_parts: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if part == "--budget":
            if i + 1 >= len(parts):
                return GoalCommand(kind="error", error="Missing value for --budget")
            try:
                token_budget = int(parts[i + 1])
            except ValueError:
                return GoalCommand(kind="error", error="Invalid --budget value")
            if token_budget <= 0:
                return GoalCommand(kind="error", error="--budget must be positive")
            i += 2
            continue
        if part.startswith("--budget="):
            raw_budget = part.split("=", 1)[1]
            try:
                token_budget = int(raw_budget)
            except ValueError:
                return GoalCommand(kind="error", error="Invalid --budget value")
            if token_budget <= 0:
                return GoalCommand(kind="error", error="--budget must be positive")
            i += 1
            continue
        objective_parts.append(part)
        i += 1

    objective = " ".join(objective_parts).strip()
    if not objective:
        return GoalCommand(kind="error", error="Goal objective cannot be empty")
    return GoalCommand(
        kind="create",
        objective=objective,
        token_budget=token_budget,
    )
