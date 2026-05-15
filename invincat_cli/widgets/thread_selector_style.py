"""Bindings and CSS for the thread selector widgets."""

from __future__ import annotations

from textual.binding import Binding, BindingType

DELETE_THREAD_CONFIRM_BINDINGS: list[BindingType] = [
    Binding("enter", "confirm", "Confirm", show=False, priority=True),
    Binding("escape", "cancel", "Cancel", show=False, priority=True),
]

DELETE_THREAD_CONFIRM_CSS = """
DeleteThreadConfirmScreen {
    align: center middle;
}

DeleteThreadConfirmScreen > Vertical {
    width: 50;
    height: auto;
    background: $surface;
    border: solid red;
    padding: 1 2;
}

DeleteThreadConfirmScreen .thread-confirm-text {
    text-align: center;
    margin-bottom: 1;
}

DeleteThreadConfirmScreen .thread-confirm-help {
    text-align: center;
    color: $text-muted;
    text-style: italic;
}
"""

THREAD_SELECTOR_BINDINGS: list[BindingType] = [
    Binding("up", "move_up", "Up", show=False, priority=True),
    Binding("k", "move_up", "Up", show=False, priority=True),
    Binding("down", "move_down", "Down", show=False, priority=True),
    Binding("j", "move_down", "Down", show=False, priority=True),
    Binding("pageup", "page_up", "Page up", show=False, priority=True),
    Binding("pagedown", "page_down", "Page down", show=False, priority=True),
    Binding("enter", "select", "Select", show=False, priority=True),
    Binding("escape", "cancel", "Cancel", show=False, priority=True),
    Binding("ctrl+d", "delete_thread", "Delete", show=False, priority=True),
    Binding("tab", "focus_next_filter", "Next filter", show=False, priority=True),
    Binding(
        "shift+tab",
        "focus_previous_filter",
        "Previous filter",
        show=False,
        priority=True,
    ),
]

THREAD_SELECTOR_CSS = """
ThreadSelectorScreen {
    align: center middle;
}

ThreadSelectorScreen #thread-selector-shell {
    width: 100%;
    max-width: 98%;
    height: 90%;
    background: $surface;
    border: solid $primary;
    padding: 1 2;
}

ThreadSelectorScreen .thread-selector-title {
    text-style: bold;
    color: $primary;
    text-align: center;
    margin-bottom: 1;
}

ThreadSelectorScreen #thread-filter {
    margin-bottom: 1;
    border: solid $primary-lighten-2;
}

ThreadSelectorScreen #thread-filter:focus {
    border: solid $primary;
}

ThreadSelectorScreen .thread-selector-body {
    height: 1fr;
}

ThreadSelectorScreen .thread-table-pane {
    width: 1fr;
    min-width: 40;
    height: 1fr;
}

ThreadSelectorScreen .thread-controls {
    width: 28;
    min-width: 24;
    height: 1fr;
    margin-left: 1;
    padding-left: 1;
    border-left: solid $primary-lighten-2;
}

ThreadSelectorScreen .thread-controls-title {
    text-style: bold;
    color: $primary;
    margin-bottom: 1;
}

ThreadSelectorScreen .thread-controls-help {
    color: $text-muted;
    margin-bottom: 1;
}

ThreadSelectorScreen .thread-column-toggle {
    width: 1fr;
    height: auto;
}

ThreadSelectorScreen .thread-list-header {
    height: 1;
    padding: 0 1;
    color: $text-muted;
    text-style: bold;
    width: 100%;
    overflow-x: hidden;
}

ThreadSelectorScreen .thread-list-header .thread-cell-sorted {
    color: $primary;
}

ThreadSelectorScreen .thread-list {
    height: 1fr;
    min-height: 5;
    scrollbar-gutter: stable;
    background: $background;
}

ThreadSelectorScreen .thread-option {
    height: 1;
    width: 100%;
    padding: 0 1;
    overflow-x: hidden;
}

ThreadSelectorScreen .thread-option:hover {
    background: $surface-lighten-1;
}

ThreadSelectorScreen .thread-option-selected {
    background: $primary;
    color: $background;
    text-style: bold;
}

ThreadSelectorScreen .thread-option-selected:hover {
    background: $primary-lighten-1;
}

ThreadSelectorScreen .thread-option-current {
    text-style: italic;
}

ThreadSelectorScreen .thread-cell {
    height: 1;
    padding-right: 1;
}

ThreadSelectorScreen .thread-cell-cursor {
    width: 2;
    color: $primary;
}

ThreadSelectorScreen .thread-cell-thread_id {
    width: 10;
}

ThreadSelectorScreen .thread-cell-agent_name {
    width: auto;
    overflow-x: hidden;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}

ThreadSelectorScreen .thread-cell-messages {
    width: 5;
}

ThreadSelectorScreen .thread-cell-created_at,
ThreadSelectorScreen .thread-cell-updated_at {
    width: auto;
}

ThreadSelectorScreen .thread-cell-git_branch {
    width: 17;
    overflow-x: hidden;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}

ThreadSelectorScreen .thread-cell-initial_prompt {
    width: 1fr;
    min-width: 1;
    overflow-x: hidden;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}

ThreadSelectorScreen .thread-selector-help {
    height: auto;
    color: $text-muted;
    text-style: italic;
    margin-top: 1;
    text-align: center;
}

ThreadSelectorScreen .thread-empty {
    color: $text-muted;
    text-align: center;
    margin-top: 2;
}

ThreadSelectorScreen .thread-loading-overlay {
    height: 1fr;
    width: 100%;
    align: center middle;
    color: $text-muted;
}

ThreadSelectorScreen .thread-loading-overlay Static {
    text-align: center;
}
"""
