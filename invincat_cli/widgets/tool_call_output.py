"""Output formatting helpers for tool-call messages."""

from __future__ import annotations

from textual.content import Content

from invincat_cli.config import get_glyphs
from invincat_cli.widgets.output_formatters import (
    FormattedOutput,
    format_tool_output,
    prefix_tool_output,
)


class ToolCallOutputMixin:
    """Mixin for formatting and rendering tool-call output previews."""

    def _format_output(
        self,
        output: str,
        *,
        is_preview: bool = False,
    ) -> FormattedOutput:
        """Format tool output based on tool type for nicer display."""
        return format_tool_output(
            self._tool_name,
            output,
            is_preview=is_preview,
            preview_lines=self._PREVIEW_LINES,
            preview_chars=self._PREVIEW_CHARS,
            theme_context=self,
        )

    def _prefix_output(self, content: Content) -> Content:
        """Prefix output with output marker and indent continuation lines."""
        return prefix_tool_output(content)

    def _update_output_display(self) -> None:
        """Update the output display based on expanded state."""
        if (
            not self._output
            or not self._preview_widget
            or not self._full_widget
            or not self._hint_widget
        ):
            return

        output_stripped = self._output.strip()
        lines = output_stripped.split("\n")
        total_lines = len(lines)
        total_chars = len(output_stripped)

        needs_truncation = (
            total_lines > self._PREVIEW_LINES or total_chars > self._PREVIEW_CHARS
        )

        if self._expanded:
            self._preview_widget.display = False
            result = self._format_output(self._output, is_preview=False)
            prefixed = self._prefix_output(result.content)
            self._full_widget.update(prefixed)
            self._full_widget.display = True
            self._hint_widget.update(
                Content.styled("click or Ctrl+O to collapse", "dim italic")
            )
            self._hint_widget.display = True
            return

        self._full_widget.display = False
        if needs_truncation:
            result = self._format_output(self._output, is_preview=True)
            prefixed = self._prefix_output(result.content)
            self._preview_widget.update(prefixed)
            self._preview_widget.display = True

            if result.truncation:
                ellipsis = get_glyphs().ellipsis
                hint = Content.styled(
                    f"{ellipsis} {result.truncation} — click or Ctrl+O to expand",
                    "dim",
                )
            else:
                hint = Content.styled("click or Ctrl+O to expand", "dim italic")
            self._hint_widget.update(hint)
            self._hint_widget.display = True
        elif output_stripped:
            result = self._format_output(output_stripped, is_preview=False)
            prefixed = self._prefix_output(result.content)
            self._preview_widget.update(prefixed)
            self._preview_widget.display = True
            self._hint_widget.display = False
        else:
            self._preview_widget.display = False
            self._hint_widget.display = False

    @property
    def has_output(self) -> bool:
        """Check if this tool message has output to display."""
        return bool(self._output)
