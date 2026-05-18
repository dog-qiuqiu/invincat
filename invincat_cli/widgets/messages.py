"""Message widgets for invincat-cli."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from textual.containers import Vertical
from textual.content import Content
from textual.events import Click
from textual.widgets import Static

from invincat_cli import theme
from invincat_cli.config import (
    MODE_DISPLAY_GLYPHS,
    PREFIX_TO_MODE,
    get_glyphs,
    is_ascii_mode,
)
from invincat_cli.i18n import t
from invincat_cli.io.input import EMAIL_PREFIX_PATTERN, INPUT_HIGHLIGHT_PATTERN
from invincat_cli.widgets._links import open_style_link
from invincat_cli.widgets.diff import compose_diff_lines
from invincat_cli.widgets.message_styles import (
    APP_MESSAGE_CSS,
    DIFF_MESSAGE_CSS,
    ERROR_MESSAGE_CSS,
    QUEUED_USER_MESSAGE_CSS,
    SUMMARIZATION_MESSAGE_CSS,
    USER_MESSAGE_CSS,
)
from invincat_cli.widgets.output_formatters import FormattedOutput
from invincat_cli.widgets.tool_call_message import ToolCallMessage

if TYPE_CHECKING:
    from textual.app import ComposeResult

logger = logging.getLogger(__name__)

__all__ = [
    "AppMessage",
    "AssistantMessage",
    "DiffMessage",
    "ErrorMessage",
    "FormattedOutput",
    "QueuedUserMessage",
    "SkillMessage",
    "SummarizationMessage",
    "ToolCallMessage",
    "UserMessage",
]


def _show_timestamp_toast(widget: Static | Vertical) -> None:
    """Show a toast with the message's creation timestamp.

    No-ops silently if the widget is not mounted or has no associated message
    data in the store.

    Args:
        widget: The message widget whose timestamp to display.
    """
    from datetime import UTC, datetime

    try:
        app = widget.app
    except Exception:  # noqa: BLE001  # Textual raises when widget has no app
        return
    if not widget.id:
        return
    store = app._message_store  # type: ignore[attr-defined]
    data = store.get_message(widget.id)
    if not data:
        return
    dt = datetime.fromtimestamp(data.timestamp, tz=UTC).astimezone()
    label = f"{dt:%b} {dt.day}, {dt.hour % 12 or 12}:{dt:%M:%S} {dt:%p}"
    app.notify(label, timeout=3)


class _TimestampClickMixin:
    """Mixin that shows a timestamp toast on click.

    Add to any message widget that should display its creation timestamp when
    clicked. Widgets needing additional click behavior (e.g. `ToolCallMessage`,
    `AppMessage`) should override `on_click` and call `_show_timestamp_toast`
    directly instead.
    """

    def on_click(self, event: Click) -> None:  # noqa: ARG002  # Textual event handler
        """Show timestamp toast on click."""
        _show_timestamp_toast(self)  # type: ignore[arg-type]


def _mode_color(mode: str | None, widget_or_app: object | None = None) -> str:
    """Return the hex color string for a mode, falling back to primary.

    Args:
        mode: Mode name (e.g. `'shell'`, `'command'`) or `None`.
        widget_or_app: Textual widget or `App` for theme-aware lookup.

    Returns:
        Color string from the active theme's `ThemeColors`.
    """
    colors = theme.get_theme_colors(widget_or_app)
    if not mode:
        return colors.primary
    if mode == "shell":
        return colors.mode_bash
    if mode == "command":
        return colors.mode_command
    logger.warning("Missing color for mode '%s'; falling back to primary.", mode)
    return colors.primary


# Maximum number of tool arguments to display inline
_MAX_INLINE_ARGS = 3

# Tools that have their key info already in the header (no need for args line)
_TOOLS_WITH_HEADER_INFO: set[str] = {
    # Filesystem tools
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "execute",  # sandbox shell
    # Shell tools
    "shell",  # local shell
    # Web tools
    "web_search",
    "fetch_url",
    # Agent tools
    "task",
    "write_todos",
}


_SUCCESS_EXIT_RE = re.compile(r"\n?\[Command succeeded with exit code 0\]\s*$")
"""Strip the SDK's `[Command succeeded with exit code 0]` trailer from tool output."""


def _strip_success_exit_line(text: str) -> str:
    """Remove the `[Command succeeded with exit code 0]` trailer.

    Non-zero exit codes are left intact (they come through `set_error`).

    Args:
        text: Raw tool output string.

    Returns:
        Text with the success exit-code trailer removed, if present.
    """
    return _SUCCESS_EXIT_RE.sub("", text)


class UserMessage(_TimestampClickMixin, Static):
    """Widget displaying a user message."""

    DEFAULT_CSS = USER_MESSAGE_CSS

    def __init__(self, content: str, **kwargs: Any) -> None:
        """Initialize a user message.

        Args:
            content: The message content
            **kwargs: Additional arguments passed to parent
        """
        super().__init__(**kwargs)
        self._content = content

    def on_mount(self) -> None:
        """Add CSS classes for mode-specific border and ASCII border type."""
        mode = PREFIX_TO_MODE.get(self._content[:1]) if self._content else None
        if mode:
            self.add_class(f"-mode-{mode}")
        if is_ascii_mode():
            self.add_class("-ascii")

    def render(self) -> Content:
        """Render the styled user message.

        Returns:
            Styled Content with mode prefix and highlighted mentions.
        """
        colors = theme.get_theme_colors(self)
        parts: list[str | tuple[str, str]] = []
        content = self._content

        # Use mode-specific prefix indicator when content starts with a
        # mode trigger character (e.g. "!" for shell, "/" for commands).
        # The display glyph may differ from the trigger (e.g. "$" for shell).
        mode = PREFIX_TO_MODE.get(content[:1]) if content else None
        if mode:
            glyph = MODE_DISPLAY_GLYPHS.get(mode, content[0])
            parts.append((f"{glyph} ", f"bold {_mode_color(mode, self)}"))
            content = content[1:]
        else:
            parts.append(("> ", f"bold {colors.primary}"))

        # Highlight @mentions and /commands in the content
        last_end = 0
        for match in INPUT_HIGHLIGHT_PATTERN.finditer(content):
            start, end = match.span()
            token = match.group()

            # Skip @mentions that look like email addresses
            if token.startswith("@") and start > 0:
                char_before = content[start - 1]
                if EMAIL_PREFIX_PATTERN.match(char_before):
                    continue

            # Add text before the match (unstyled)
            if start > last_end:
                parts.append(content[last_end:start])

            # The regex only matches tokens starting with / or @
            if token.startswith("/") and start == 0:
                # /command at start
                parts.append((token, f"bold {colors.warning}"))
            elif token.startswith("@"):
                # @file mention
                parts.append((token, f"bold {colors.primary}"))
            last_end = end

        # Add remaining text after last match
        if last_end < len(content):
            parts.append(content[last_end:])

        return Content.assemble(*parts)


class QueuedUserMessage(Static):
    """Widget displaying a queued (pending) user message in grey.

    This is an ephemeral widget that gets removed when the message is dequeued.
    """

    DEFAULT_CSS = QUEUED_USER_MESSAGE_CSS
    """Dimmed border + reduced opacity to distinguish queued messages from sent ones."""

    def __init__(self, content: str, **kwargs: Any) -> None:
        """Initialize a queued user message.

        Args:
            content: The message content
            **kwargs: Additional arguments passed to parent
        """
        super().__init__(**kwargs)
        self._content = content

    def on_mount(self) -> None:
        """Add ASCII border class when in ASCII mode."""
        if is_ascii_mode():
            self.add_class("-ascii")

    def render(self) -> Content:
        """Render the queued user message (greyed out).

        Returns:
            Styled Content with dimmed prefix and body.
        """
        colors = theme.get_theme_colors(self)
        content = self._content
        mode = PREFIX_TO_MODE.get(content[:1]) if content else None
        if mode:
            glyph = MODE_DISPLAY_GLYPHS.get(mode, content[0])
            prefix = (f"{glyph} ", f"bold {colors.muted}")
            content = content[1:]
        else:
            prefix = ("> ", f"bold {colors.muted}")
        return Content.assemble(prefix, (content, colors.muted))


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter delimited by `---` markers.

    Args:
        text: Raw `SKILL.md` content.

    Returns:
        Body text with frontmatter removed and leading whitespace stripped.
    """
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return text
    # Find closing --- (skip the opening line)
    end = stripped.find("\n---", 3)
    if end == -1:
        return text
    # Skip past the closing --- and its trailing newline
    after = end + 4  # len("\n---")
    return stripped[after:].lstrip("\n")


from invincat_cli.widgets.assistant_message import AssistantMessage  # noqa: E402
from invincat_cli.widgets.skill_message import SkillMessage  # noqa: E402


class DiffMessage(_TimestampClickMixin, Static):
    """Widget displaying a diff with syntax highlighting."""

    DEFAULT_CSS = DIFF_MESSAGE_CSS
    """Diff syntax coloring per theme: additions, removals, muted context."""

    _PREVIEW_LINES = 6

    def __init__(self, diff_content: str, file_path: str = "", **kwargs: Any) -> None:
        """Initialize a diff message.

        Args:
            diff_content: The unified diff content
            file_path: Path to the file being modified
            **kwargs: Additional arguments passed to parent
        """
        super().__init__(**kwargs)
        self._diff_content = diff_content
        self._file_path = file_path
        self._expanded = False
        self._total_lines = self._count_diff_lines(diff_content)

    def _count_diff_lines(self, diff: str) -> int:
        """Count meaningful diff lines (excluding headers)."""
        count = 0
        for line in diff.splitlines():
            if line.startswith(("---", "+++", "@@")):
                continue
            if line.startswith(("+", "-", " ")) or line.strip() == "...":
                count += 1
        return count

    def compose(self) -> ComposeResult:
        """Compose the diff message layout.

        Yields:
            Widgets displaying the diff header and formatted content.
        """
        if self._file_path:
            yield Static(
                Content.from_markup("[bold]File: $path[/bold]", path=self._file_path),
                classes="diff-header",
            )

        max_lines = None if self._expanded else self._PREVIEW_LINES
        yield from compose_diff_lines(self._diff_content, max_lines=max_lines)

        if self._total_lines > self._PREVIEW_LINES:
            glyphs = get_glyphs()
            if self._expanded:
                hint = "click or Ctrl+O to collapse"
            else:
                hint = f"{glyphs.ellipsis} {self._total_lines - self._PREVIEW_LINES} more lines — click or Ctrl+O to expand"
            yield Static(Content.styled(hint, "dim italic"), classes="diff-hint")

    def on_mount(self) -> None:
        """Set border style based on charset mode."""
        if is_ascii_mode():
            colors = theme.get_theme_colors(self)
            self.styles.border = ("ascii", colors.primary)

    def toggle_expand(self) -> None:
        """Toggle expanded state and refresh display."""
        self._expanded = not self._expanded
        self.refresh(recompose=True)

    def on_click(self) -> None:
        """Handle click to toggle expand/collapse."""
        if self._total_lines > self._PREVIEW_LINES:
            self.toggle_expand()

    def on_key(self, event: Any) -> None:
        """Handle Ctrl+O to toggle expand/collapse."""
        if event.key == "ctrl+o" and self._total_lines > self._PREVIEW_LINES:
            self.toggle_expand()
            event.stop()


class ErrorMessage(_TimestampClickMixin, Static):
    """Widget displaying an error message."""

    DEFAULT_CSS = ERROR_MESSAGE_CSS
    """Tinted background + left border to visually separate errors from output."""

    def __init__(self, error: str, **kwargs: Any) -> None:
        """Initialize an error message.

        Args:
            error: The error message
            **kwargs: Additional arguments passed to parent
        """
        # Store raw content for serialization
        self._content = error
        super().__init__(**kwargs)

    def render(self) -> Content:
        """Render with theme-aware colors.

        Returns:
            Styled error content with theme-appropriate color.
        """
        colors = theme.get_theme_colors(self)
        return Content.assemble(
            Content.styled(t("message.error"), f"bold {colors.error}"),
            self._content,
        )

    def on_mount(self) -> None:
        """Set border style based on charset mode."""
        if is_ascii_mode():
            colors = theme.get_theme_colors(self)
            self.styles.border_left = ("ascii", colors.error)


class AppMessage(Static):
    """Widget displaying an app message."""

    # Disable Textual's auto_links to prevent a flicker cycle: Style.__add__
    # calls .copy() for linked styles, generating a fresh random _link_id on
    # each render. This means highlight_link_id never stabilizes, causing an
    # infinite hover-refresh loop.
    auto_links = False

    DEFAULT_CSS = APP_MESSAGE_CSS

    def __init__(self, message: str | Content, **kwargs: Any) -> None:
        """Initialize a system message.

        Args:
            message: The system message as a string or pre-styled `Content`.
            **kwargs: Additional arguments passed to parent
        """
        # Store raw content for serialization
        self._content = message
        rendered = (
            message
            if isinstance(message, Content)
            else Content.styled(message, "dim italic")
        )
        super().__init__(rendered, **kwargs)

    def on_click(self, event: Click) -> None:
        """Open style-embedded hyperlinks on single click and show timestamp."""
        open_style_link(event)
        _show_timestamp_toast(self)


class SummarizationMessage(AppMessage):
    """Widget displaying a summarization completion notification."""

    DEFAULT_CSS = SUMMARIZATION_MESSAGE_CSS

    def __init__(self, message: str | Content | None = None, **kwargs: Any) -> None:
        """Initialize a summarization notification message.

        Args:
            message: Optional message override used when rehydrating from the
                message store.

                Defaults to the standard summary notification.
            **kwargs: Additional arguments passed to parent.
        """
        self._raw_message = message
        # Pass the default text to AppMessage for _content serialization;
        # render() supplies theme-aware styling at display time.
        super().__init__(message or "✓ Conversation offloaded", **kwargs)

    def render(self) -> Content:
        """Render with theme-aware colors.

        Returns:
            Styled summarization content with theme-appropriate color.
        """
        colors = theme.get_theme_colors(self)
        if self._raw_message is None:
            return Content.styled("✓ Conversation offloaded", f"bold {colors.primary}")
        if isinstance(self._raw_message, Content):
            return self._raw_message
        return Content.styled(self._raw_message, f"bold {colors.primary}")
