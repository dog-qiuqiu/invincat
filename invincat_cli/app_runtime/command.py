"""Slash-command routing helpers for the Textual app."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CommandRouteKind = Literal[
    "quit",
    "help",
    "url",
    "version",
    "clear",
    "editor",
    "offload",
    "plan",
    "exit_plan",
    "threads",
    "trace",
    "update",
    "auto_update",
    "tokens",
    "skill_creator",
    "mcp",
    "memory",
    "wecom",
    "schedule",
    "theme",
    "language",
    "model",
    "reload",
    "skill",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class CommandRoute:
    """Resolved route for a slash command."""

    kind: CommandRouteKind
    normalized: str
    wecom_action: str | None = None
    rewritten_command: str | None = None
    plan_task: str | None = None


_EXACT_COMMAND_ROUTES: dict[str, CommandRouteKind] = {
    "/quit": "quit",
    "/q": "quit",
    "/help": "help",
    "/changelog": "url",
    "/docs": "url",
    "/feedback": "url",
    "/version": "version",
    "/clear": "clear",
    "/editor": "editor",
    "/offload": "offload",
    "/compact": "offload",
    "/plan": "plan",
    "/exit-plan": "exit_plan",
    "/threads": "threads",
    "/trace": "trace",
    "/update": "update",
    "/auto-update": "auto_update",
    "/tokens": "tokens",
    "/mcp": "mcp",
    "/memory": "memory",
    "/schedule": "schedule",
    "/theme": "theme",
    "/language": "language",
    "/model": "model",
    "/reload": "reload",
}

_WECOM_COMMAND_ACTIONS = {
    "/wecombot-start": "start",
    "/wecombot-status": "status",
    "/wecombot-stop": "stop",
}


def rewrite_skill_creator_command(command: str) -> str:
    """Rewrite `/skill-creator` alias to the canonical skill command."""
    args = command.strip()[len("/skill-creator") :].strip()
    return f"/skill:skill-creator {args}" if args else "/skill:skill-creator"


def route_slash_command(command: str) -> CommandRoute:
    """Classify a slash command without executing side effects."""
    cmd = command.lower().strip()

    if kind := _EXACT_COMMAND_ROUTES.get(cmd):
        return CommandRoute(kind=kind, normalized=cmd)
    if cmd.startswith("/plan "):
        return CommandRoute(
            kind="plan",
            normalized="/plan",
            plan_task=command.strip()[len("/plan") :].strip(),
        )
    if cmd == "/skill-creator" or cmd.startswith("/skill-creator "):
        return CommandRoute(
            kind="skill_creator",
            normalized=cmd,
            rewritten_command=rewrite_skill_creator_command(command),
        )
    if action := _WECOM_COMMAND_ACTIONS.get(cmd):
        return CommandRoute(kind="wecom", normalized=cmd, wecom_action=action)
    if cmd.startswith("/schedule "):
        return CommandRoute(kind="schedule", normalized=cmd)
    if cmd.startswith("/model "):
        return CommandRoute(kind="model", normalized=cmd)
    if cmd.startswith("/skill:"):
        return CommandRoute(kind="skill", normalized=cmd)
    return CommandRoute(kind="unknown", normalized=cmd)
