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


def rewrite_skill_creator_command(command: str) -> str:
    """Rewrite `/skill-creator` alias to the canonical skill command."""
    args = command.strip()[len("/skill-creator") :].strip()
    return f"/skill:skill-creator {args}" if args else "/skill:skill-creator"


def route_slash_command(command: str) -> CommandRoute:
    """Classify a slash command without executing side effects."""
    cmd = command.lower().strip()

    if cmd in {"/quit", "/q"}:
        return CommandRoute(kind="quit", normalized=cmd)
    if cmd == "/help":
        return CommandRoute(kind="help", normalized=cmd)
    if cmd in {"/changelog", "/docs", "/feedback"}:
        return CommandRoute(kind="url", normalized=cmd)
    if cmd == "/version":
        return CommandRoute(kind="version", normalized=cmd)
    if cmd == "/clear":
        return CommandRoute(kind="clear", normalized=cmd)
    if cmd == "/editor":
        return CommandRoute(kind="editor", normalized=cmd)
    if cmd in {"/offload", "/compact"}:
        return CommandRoute(kind="offload", normalized=cmd)
    if cmd == "/plan":
        return CommandRoute(kind="plan", normalized=cmd)
    if cmd == "/exit-plan":
        return CommandRoute(kind="exit_plan", normalized=cmd)
    if cmd == "/threads":
        return CommandRoute(kind="threads", normalized=cmd)
    if cmd == "/trace":
        return CommandRoute(kind="trace", normalized=cmd)
    if cmd == "/update":
        return CommandRoute(kind="update", normalized=cmd)
    if cmd == "/auto-update":
        return CommandRoute(kind="auto_update", normalized=cmd)
    if cmd == "/tokens":
        return CommandRoute(kind="tokens", normalized=cmd)
    if cmd == "/skill-creator" or cmd.startswith("/skill-creator "):
        return CommandRoute(
            kind="skill_creator",
            normalized=cmd,
            rewritten_command=rewrite_skill_creator_command(command),
        )
    if cmd == "/mcp":
        return CommandRoute(kind="mcp", normalized=cmd)
    if cmd == "/memory":
        return CommandRoute(kind="memory", normalized=cmd)
    if cmd == "/wecombot-start":
        return CommandRoute(kind="wecom", normalized=cmd, wecom_action="start")
    if cmd == "/wecombot-status":
        return CommandRoute(kind="wecom", normalized=cmd, wecom_action="status")
    if cmd == "/wecombot-stop":
        return CommandRoute(kind="wecom", normalized=cmd, wecom_action="stop")
    if cmd == "/schedule" or cmd.startswith("/schedule "):
        return CommandRoute(kind="schedule", normalized=cmd)
    if cmd == "/theme":
        return CommandRoute(kind="theme", normalized=cmd)
    if cmd == "/language":
        return CommandRoute(kind="language", normalized=cmd)
    if cmd == "/model" or cmd.startswith("/model "):
        return CommandRoute(kind="model", normalized=cmd)
    if cmd == "/reload":
        return CommandRoute(kind="reload", normalized=cmd)
    if cmd.startswith("/skill:"):
        return CommandRoute(kind="skill", normalized=cmd)
    return CommandRoute(kind="unknown", normalized=cmd)
