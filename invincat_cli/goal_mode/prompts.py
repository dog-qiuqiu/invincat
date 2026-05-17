"""Prompt helpers for active goal context."""

from __future__ import annotations

from invincat_cli.goal_mode.models import GoalState


def render_goal_status(goal: GoalState) -> str:
    """Render a compact user-facing status summary."""
    budget = (
        f"{goal.tokens_used} / {goal.token_budget} tokens"
        if goal.token_budget is not None
        else f"{goal.tokens_used} tokens"
    )
    lines = [
        f"Goal: {goal.objective}",
        f"Status: {goal.status}",
        f"Thread: {goal.thread_id}",
        f"Budget: {budget}",
    ]
    if goal.summary:
        lines.append(f"Summary: {goal.summary}")
    return "\n".join(lines)


def render_goal_context(goal: GoalState) -> str:
    """Render hidden per-turn context injected into agent input."""
    budget = (
        f"{goal.tokens_used} / {goal.token_budget}"
        if goal.token_budget is not None
        else f"{goal.tokens_used} / unlimited"
    )
    return (
        "<active_goal>\n"
        f"Objective: {goal.objective}\n"
        f"Status: {goal.status}\n"
        f"Token budget: {budget}\n"
        "Rules:\n"
        "- Keep this turn aligned with the active goal.\n"
        "- Decompose the goal into concrete work when useful, then keep moving.\n"
        "- Maintain continuity across turns and report meaningful progress.\n"
        "- Ask before switching away from this goal if the user request conflicts.\n"
        "- Do not consider the goal complete unless the objective is actually achieved.\n"
        "</active_goal>"
    )


def wrap_goal_context(message: str, goal: GoalState | None) -> str:
    """Prepend goal context to a model-facing user message."""
    if goal is None or not goal.is_active:
        return message
    return f"{render_goal_context(goal)}\n\nUser message:\n{message}"


def build_goal_kickoff_prompt(goal: GoalState) -> str:
    """Build the first main-agent message after a goal is created."""
    return (
        "A new active goal has been created. Restate the goal briefly, break it "
        "into a practical execution path, then begin the first useful step.\n\n"
        f"Goal: {goal.objective}"
    )
