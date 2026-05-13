"""Shell-command helpers for the Textual app."""

from __future__ import annotations

from pathlib import Path
from typing import Literal


INTERACTIVE_COMMANDS: frozenset[str] = frozenset({
    "vi", "vim", "nvim", "neovim",
    "nano", "pico", "emacs", "micro",
    "less", "more", "most",
    "top", "htop", "btop", "btm", "glances",
    "python", "python3", "ipython", "bpython",
    "node", "irb", "pry",
    "sqlite3", "psql", "mysql", "redis-cli",
    "mc", "midnight-commander",
    "tig", "lazygit", "gitui",
    "ranger", "nnn", "lf",
    "screen", "tmux",
    "man", "info",
})
"""Commands that require an interactive terminal (TTY)."""


def is_interactive_command(command: str) -> bool:
    """Return whether a shell command appears to require an interactive TTY."""
    cmd_name = command.split()[0] if command.split() else ""
    base_name = Path(cmd_name).name
    return base_name in INTERACTIVE_COMMANDS


def should_start_new_shell_session(platform: str) -> bool:
    """Return whether subprocess shells should run in a new process group."""
    return platform != "win32"


def shell_termination_strategy(platform: str) -> Literal["process_group", "process"]:
    """Return how a running shell process should be terminated."""
    return "process" if platform == "win32" else "process_group"


def format_shell_output(stdout: bytes | None, stderr: bytes | None) -> str:
    """Decode and combine shell stdout/stderr for chat display."""
    output = (stdout or b"").decode(errors="replace").strip()
    stderr_text = (stderr or b"").decode(errors="replace").strip()
    if stderr_text:
        output += f"\n[stderr]\n{stderr_text}"
    return output
