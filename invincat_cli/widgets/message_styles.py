"""CSS snippets for message widgets."""

from __future__ import annotations

USER_MESSAGE_CSS = """
    UserMessage {
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
        background: transparent;
        border-left: wide $primary;
    }
"""

QUEUED_USER_MESSAGE_CSS = """
    QueuedUserMessage {
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
        background: transparent;
        border-left: wide $panel;
        opacity: 0.6;
    }
"""

SKILL_MESSAGE_CSS = """
    SkillMessage {
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
        background: transparent;
        border-left: wide $skill;
    }

    SkillMessage .skill-header {
        height: auto;
    }

    SkillMessage .skill-description {
        color: $text-muted;
        margin-left: 3;
    }

    SkillMessage .skill-args {
        margin-left: 3;
        margin-top: 0;
    }

    SkillMessage #skill-md {
        margin-left: 3;
        margin-top: 0;
        padding: 0;
        display: none;
    }

    SkillMessage .skill-hint {
        margin-left: 3;
        color: $text-muted;
    }

    SkillMessage.-expanded #skill-md {
        display: block;
    }

    SkillMessage:hover {
        border-left: wide $skill-hover;
    }
"""

ASSISTANT_MESSAGE_CSS = """
    AssistantMessage {
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    AssistantMessage Markdown {
        padding: 0;
        margin: 0;
    }

    AssistantMessage .assistant-reasoning {
        padding: 0;
        margin: 1 0 0 0;
        color: $text-muted;
        display: none;
    }
"""

TOOL_CALL_MESSAGE_CSS = """
    ToolCallMessage {
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
        background: transparent;
        border-left: wide $tool;
    }

    ToolCallMessage .tool-header {
        height: auto;
        color: $tool;
        text-style: bold;
    }

    ToolCallMessage .tool-task-desc {
        color: $text-muted;
        margin-left: 3;
        text-style: italic;
    }

    ToolCallMessage .tool-args {
        color: $text-muted;
        margin-left: 3;
    }

    ToolCallMessage .tool-status {
        margin-left: 3;
    }

    ToolCallMessage .tool-status.generating {
        color: $text-muted;
    }

    ToolCallMessage .tool-status.pending {
        color: $warning;
    }

    ToolCallMessage .tool-status.success {
        color: $success;
    }

    ToolCallMessage .tool-status.error {
        color: $error;
    }

    ToolCallMessage .tool-status.rejected {
        color: $warning;
    }

    ToolCallMessage .tool-output {
        margin-left: 0;
        margin-top: 0;
        padding: 0;
        height: auto;
    }

    ToolCallMessage .tool-output-preview {
        margin-left: 0;
        margin-top: 0;
    }

    ToolCallMessage .tool-output-hint {
        margin-left: 0;
        color: $text-muted;
    }

    ToolCallMessage:hover {
        border-left: wide $tool-hover;
    }
"""

DIFF_MESSAGE_CSS = """
    DiffMessage {
        height: auto;
        padding: 1;
        margin: 0 0 1 0;
        background: $surface;
        border: solid $primary;
    }

    DiffMessage .diff-header {
        text-style: bold;
        margin-bottom: 1;
    }

    DiffMessage .diff-hint {
        color: $text-muted;
        text-style: italic;
        margin-top: 1;
    }

    DiffMessage .diff-add {
        color: $text-success;
        background: $success-muted;
    }

    DiffMessage .diff-remove {
        color: $text-error;
        background: $error-muted;
    }

    DiffMessage .diff-context {
        color: $text-muted;
    }

    DiffMessage .diff-hunk {
        color: $secondary;
        text-style: bold;
    }
"""

ERROR_MESSAGE_CSS = """
    ErrorMessage {
        height: auto;
        padding: 1;
        margin: 0 0 1 0;
        background: $error-muted;
        color: white;
        border-left: wide $error;
    }
"""

APP_MESSAGE_CSS = """
    AppMessage {
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
        color: $text-muted;
        text-style: italic;
    }
"""

SUMMARIZATION_MESSAGE_CSS = """
    SummarizationMessage {
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
        color: $primary;
        background: $surface;
        border-left: wide $primary;
        text-style: bold;
    }
"""

