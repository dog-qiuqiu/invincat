"""Textual key bindings for the main application."""

from __future__ import annotations

from textual.binding import Binding, BindingType

APP_BINDINGS: list[BindingType] = [
    Binding("escape", "interrupt", "Interrupt", show=False, priority=True),
    Binding(
        "ctrl+c",
        "quit_or_interrupt",
        "Quit/Interrupt",
        show=False,
        priority=True,
    ),
    Binding("ctrl+d", "quit_app", "Quit", show=False, priority=True),
    Binding("ctrl+t", "toggle_auto_approve", "Toggle Auto-Approve", show=False),
    Binding(
        "shift+tab",
        "toggle_auto_approve",
        "Toggle Auto-Approve",
        show=False,
        priority=True,
    ),
    Binding(
        "ctrl+o",
        "toggle_tool_output",
        "Toggle Tool Output",
        show=False,
        priority=True,
    ),
    Binding(
        "ctrl+x",
        "open_editor",
        "Open Editor",
        show=False,
        priority=True,
    ),
    Binding("up", "approval_up", "Up", show=False),
    Binding("k", "approval_up", "Up", show=False),
    Binding("down", "approval_down", "Down", show=False),
    Binding("j", "approval_down", "Down", show=False),
    Binding("enter", "approval_select", "Select", show=False),
    Binding("y", "approval_yes", "Yes", show=False),
    Binding("1", "approval_yes", "Yes", show=False),
    Binding("2", "approval_auto", "Auto", show=False),
    Binding("a", "approval_auto", "Auto", show=False),
    Binding("3", "approval_no", "No", show=False),
    Binding("n", "approval_no", "No", show=False),
]
"""App-level bindings for interrupt, quit, toggles, and approval navigation."""
