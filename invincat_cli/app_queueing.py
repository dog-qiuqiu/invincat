"""Input queueing helpers for the Textual app."""

from __future__ import annotations


def can_bypass_busy_queue(
    value: str,
    *,
    connecting: bool,
    agent_running: bool,
    shell_running: bool,
) -> bool:
    """Return whether a command can run while the app is busy/connecting."""
    from invincat_cli.command_registry import (
        BYPASS_WHEN_CONNECTING,
        IMMEDIATE_UI,
        SIDE_EFFECT_FREE,
    )

    cmd = value.split(maxsplit=1)[0] if value else ""
    if cmd in BYPASS_WHEN_CONNECTING:
        return connecting and not (agent_running or shell_running)
    if cmd in IMMEDIATE_UI:
        return value == cmd
    if cmd == "/plan":
        return value == cmd
    return cmd in SIDE_EFFECT_FREE
