"""Bindings and CSS for the model selector."""

from __future__ import annotations

from textual.binding import Binding, BindingType

MODEL_SELECTOR_BINDINGS: list[BindingType] = [
    Binding("up", "move_up", "Up", show=False, priority=True),
    Binding("k", "move_up", "Up", show=False, priority=True),
    Binding("down", "move_down", "Down", show=False, priority=True),
    Binding("j", "move_down", "Down", show=False, priority=True),
    Binding("tab", "tab_complete", "Tab complete", show=False, priority=True),
    Binding("pageup", "page_up", "Page up", show=False, priority=True),
    Binding("pagedown", "page_down", "Page down", show=False, priority=True),
    Binding("1", "target_primary", "Primary target", show=False, priority=True),
    Binding("2", "target_memory", "Memory target", show=False, priority=True),
    Binding("enter", "select", "Select", show=False, priority=True),
    Binding(
        "ctrl+n", "register_model", "Register model", show=False, priority=True
    ),
    Binding("ctrl+e", "edit_model", "Edit model", show=False, priority=True),
    Binding("escape", "cancel", "Cancel", show=False, priority=True),
]

MODEL_SELECTOR_CSS = """
ModelSelectorScreen {
    align: center middle;
}

ModelSelectorScreen > Vertical {
    width: 80;
    max-width: 90%;
    height: 80%;
    background: $surface;
    border: solid $primary;
    padding: 1 2;
}

ModelSelectorScreen .model-selector-title {
    text-style: bold;
    color: $primary;
    text-align: center;
    margin-bottom: 1;
}

ModelSelectorScreen #model-filter {
    margin-bottom: 1;
    border: solid $primary-lighten-2;
}

ModelSelectorScreen #model-filter:focus {
    border: solid $primary;
}

ModelSelectorScreen .model-list {
    height: 1fr;
    min-height: 5;
    scrollbar-gutter: stable;
    background: $background;
}

ModelSelectorScreen #model-options {
    height: auto;
}

ModelSelectorScreen .model-provider-header {
    color: $primary;
    margin-top: 1;
}

ModelSelectorScreen #model-options > .model-provider-header:first-child {
    margin-top: 0;
}

ModelSelectorScreen .model-option {
    height: 1;
    padding: 0 1;
}

ModelSelectorScreen .model-option:hover {
    background: $surface-lighten-1;
}

ModelSelectorScreen .model-option-selected {
    background: $primary;
    color: $background;
    text-style: bold;
}

ModelSelectorScreen .model-option-selected:hover {
    background: $primary-lighten-1;
}

ModelSelectorScreen .model-option-current {
    text-style: italic;
}

ModelSelectorScreen .model-selector-help {
    height: 2;
    color: $text-muted;
    text-style: italic;
    margin-top: 1;
    text-align: center;
}

ModelSelectorScreen .model-detail-footer {
    height: 4;
    padding: 0 2;
    margin-top: 1;
}
"""

