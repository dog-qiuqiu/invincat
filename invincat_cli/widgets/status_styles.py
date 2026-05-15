"""CSS for the status bar widget."""

STATUS_BAR_CSS = """
StatusBar {
    height: 1;
    dock: bottom;
    background: $surface;
    padding: 0 1;
}

StatusBar .status-mode {
    width: auto;
    padding: 0 1;
}

StatusBar .status-mode.normal {
    display: none;
}

StatusBar .status-mode.shell {
    background: $mode-bash;
    color: white;
    text-style: bold;
}

StatusBar .status-mode.command {
    background: $mode-command;
    color: white;
}

StatusBar .status-auto-approve {
    width: auto;
    padding: 0 1;
}

StatusBar .status-auto-approve.on {
    background: $success;
    color: $background;
}

StatusBar .status-auto-approve.off {
    background: $warning;
    color: $background;
}

StatusBar .status-plan-mode {
    width: auto;
    padding: 0 1;
    background: $primary;
    color: $background;
    text-style: bold;
    display: none;
}

StatusBar .status-plan-mode.on {
    display: block;
}

StatusBar .status-message {
    width: auto;
    padding: 0 1;
    color: $text-muted;
}

StatusBar .status-message.thinking {
    color: $warning;
}

StatusBar .status-message.memory {
    color: $success;
}

StatusBar .status-cwd {
    width: auto;
    text-align: right;
    color: $text-muted;
}

StatusBar .status-branch {
    width: auto;
    color: $text-muted;
    padding: 0 1;
}

StatusBar .status-left-collapsible {
    width: 1fr;
    min-width: 0;
    height: 1;
    overflow-x: hidden;
}

StatusBar .status-tokens {
    width: auto;
    padding: 0 0 0 1;
    color: $text-muted;
}

StatusBar .status-tokens.warn {
    color: $warning;
}

StatusBar .status-tokens.danger {
    color: $error;
}

StatusBar .status-message-count {
    width: auto;
    padding: 0 0 0 1;
    color: $text-muted;
}

StatusBar ModelLabel {
    width: auto;
    padding: 0 0 0 1;
    color: $text-muted;
    text-align: right;
}

StatusBar #memory-model-display {
    color: $text-muted;
}
"""
