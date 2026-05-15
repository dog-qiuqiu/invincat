"""Bindings and CSS for the model registration form."""

from __future__ import annotations

from textual.binding import Binding, BindingType

PROVIDER_SELECT_BINDINGS: list[BindingType] = [
    Binding("enter,space", "show_overlay", "Show menu", show=False),
]

MODEL_REGISTER_BINDINGS: list[BindingType] = [
    Binding("escape", "cancel", "Cancel", show=False, priority=True),
    Binding("ctrl+s", "submit", "Submit", show=False, priority=True),
    Binding("tab", "next_field", "Next field", show=False, priority=True),
    Binding("shift+tab", "prev_field", "Prev field", show=False, priority=True),
]

MODEL_REGISTER_CSS = """
ModelRegisterScreen {
    align: center middle;
}

ModelRegisterScreen > Vertical {
    width: 70;
    max-width: 90%;
    height: auto;
    background: $surface;
    border: solid $primary;
    padding: 1 2;
}

ModelRegisterScreen .register-title {
    text-style: bold;
    color: $primary;
    text-align: center;
    margin-bottom: 1;
}

ModelRegisterScreen .register-field-label {
    color: $text;
    margin-top: 1;
}

ModelRegisterScreen .register-field-hint {
    color: $text-muted;
    text-style: italic;
    margin-bottom: 0;
}

ModelRegisterScreen .register-input {
    margin-bottom: 0;
}

ModelRegisterScreen .register-error {
    color: $error;
    margin-top: 1;
}

ModelRegisterScreen .register-help {
    color: $text-muted;
    text-style: italic;
    margin-top: 1;
    text-align: center;
}
"""

