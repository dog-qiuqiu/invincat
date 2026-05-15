"""CSS snippets for chat input widgets."""

COMPLETION_OPTION_CSS = """
CompletionOption {
    height: 1;
    padding: 0 1;
}

CompletionOption:hover {
    background: $surface-lighten-1;
}

CompletionOption.completion-option-selected {
    background: $primary;
    color: $background;
    text-style: bold;
}

CompletionOption.completion-option-selected:hover {
    background: $primary-lighten-1;
}
"""

COMPLETION_POPUP_CSS = """
CompletionPopup {
    display: none;
    height: auto;
    max-height: 12;
}
"""

CHAT_INPUT_CSS = """
ChatInput {
    height: auto;
    min-height: 3;
    max-height: 25;
    padding: 0;
    background: $surface;
    border: solid $primary;
}

ChatInput.mode-shell {
    border: solid $mode-bash;
}

ChatInput.mode-command {
    border: solid $mode-command;
}

ChatInput .input-row {
    height: auto;
    width: 100%;
}

ChatInput .input-prompt {
    width: 3;
    height: 1;
    padding: 0 1;
    color: $primary;
    text-style: bold;
}

ChatInput.mode-shell .input-prompt {
    color: $mode-bash;
}

ChatInput.mode-command .input-prompt {
    color: $mode-command;
}

ChatInput ChatTextArea {
    width: 1fr;
    height: auto;
    min-height: 1;
    max-height: 8;
    border: none;
    background: transparent;
    padding: 0;
}

ChatInput ChatTextArea:focus {
    border: none;
}
"""
