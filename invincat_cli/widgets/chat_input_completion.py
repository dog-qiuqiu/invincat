"""Completion and input-mode helpers for chat input."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from textual.css.query import NoMatches
from textual.widgets import Static

from invincat_cli.config import MODE_DISPLAY_GLYPHS, MODE_PREFIXES
from invincat_cli.widgets.autocomplete import CompletionResult, MultiCompletionManager
from invincat_cli.widgets.chat_completion import CompletionPopup

if TYPE_CHECKING:
    from textual import events

logger = logging.getLogger(__name__)


class ChatInputCompletionMixin:
    """Handle completion navigation, rendering, and mode display."""

    def update_slash_commands(self, commands: list[tuple[str, str, str]]) -> None:
        """Update the slash command controller's command list.

        Called by the app after discovering skills to merge static
        commands with dynamic `/skill:` entries.

        Args:
            commands: Full list of `(command, description, hidden_keywords)` tuples.
        """
        if self._slash_controller:
            self._slash_controller.update_commands(commands)
        else:
            logger.warning(
                "Cannot update slash commands: controller not initialized "
                "(widget not yet mounted)"
            )

    def _strip_mode_prefix(self) -> None:
        """Remove the first character (mode trigger) from the text area.

        Sets the `_stripping_prefix` guard so the resulting text-change event is
        not misinterpreted as new input.
        """
        if not self._text_area:
            return
        if self._stripping_prefix:
            logger.warning(
                "Previous _stripping_prefix guard was never cleared; "
                "resetting. This may indicate a missed text-change event."
            )
        text = self._text_area.text
        if not text:
            return
        row, col = self._text_area.cursor_location
        self._stripping_prefix = True
        self._text_area.text = text[1:]
        if row == 0 and col > 0:
            col -= 1
        self._text_area.move_cursor((row, col))

    def _completion_text_and_cursor(self) -> tuple[str, int]:
        """Return controller-facing text/cursor in completion space.

        Also updates `_completion_prefix_len` so that subsequent calls to
        `_completion_index_to_text_index` use the matching offset.
        """
        if not self._text_area:
            self._completion_prefix_len = 0
            return "", 0

        text = self._text_area.text
        cursor = self._get_cursor_offset()
        prefix = MODE_PREFIXES.get(self.mode, "")
        self._completion_prefix_len = len(prefix)

        if prefix:
            return prefix + text, cursor + len(prefix)
        return text, cursor

    def _completion_index_to_text_index(self, index: int) -> int:
        """Translate completion-space index into text-area index.

        Args:
            index: Cursor/index position in completion space.

        Returns:
            Clamped index in text-area space.
        """
        if not self._text_area:
            return 0

        mapped = index - self._completion_prefix_len
        text_len = len(self._text_area.text)
        if mapped < 0 or mapped > text_len:
            logger.warning(
                "Completion index %d mapped to %d, outside [0, %d]; "
                "clamping (prefix_len=%d, mode=%s)",
                index,
                mapped,
                text_len,
                self._completion_prefix_len,
                self.mode,
            )
        return max(0, min(mapped, text_len))

    async def on_key(self, event: events.Key) -> None:
        """Handle key events for completion navigation."""
        if not self._completion_manager or not self._text_area:
            return

        # Backspace at cursor position 0 (or on empty input) exits command
        # mode.  Shell mode is excluded — only Escape exits shell mode.
        if (
            event.key == "backspace"
            and self.mode != "normal"
            and self.mode != "shell"
            and self._get_cursor_offset() == 0
        ):

            def _deferred_reset() -> None:
                if self._completion_manager is not None:
                    self._completion_manager.reset()

            self.call_after_refresh(_deferred_reset)
            self.mode = "normal"
            event.prevent_default()
            event.stop()
            return

        if event.key == "escape" and self.mode == "shell":
            if self._completion_manager is not None:
                self._completion_manager.reset()
            self.mode = "normal"
            event.prevent_default()
            event.stop()
            return

        text, cursor = self._completion_text_and_cursor()
        result = self._completion_manager.on_key(event, text, cursor)

        match result:
            case CompletionResult.HANDLED:
                event.prevent_default()
                event.stop()
            case CompletionResult.SUBMIT:
                event.prevent_default()
                event.stop()
                self._submit_value(self._text_area.text.strip())
            case CompletionResult.IGNORED if event.key == "enter":
                # Handle Enter when completion is not active (shell/normal modes)
                value = self._text_area.text.strip()
                if value:
                    event.prevent_default()
                    event.stop()
                    self._submit_value(value)

    def _get_cursor_offset(self) -> int:
        """Get the cursor offset as a single integer.

        Returns:
            Cursor position as character offset from start of text.
        """
        if not self._text_area:
            return 0

        text = self._text_area.text
        row, col = self._text_area.cursor_location

        if not text:
            return 0

        lines = text.split("\n")
        row = max(0, min(row, len(lines) - 1))
        col = max(0, col)

        offset = sum(len(lines[i]) + 1 for i in range(row))
        return offset + min(col, len(lines[row]))

    def watch_mode(self, mode: str) -> None:
        """Post mode changed message and update prompt indicator.

        The prompt glyph update is deferred via `call_after_refresh` so that
        callers which also schedule deferred work (e.g. the completion popup)
        can coalesce both visual changes into a single refresh.
        """
        glyph = MODE_DISPLAY_GLYPHS.get(mode)
        if not glyph and mode != "normal":
            logger.warning(
                "No display glyph for mode %r; falling back to '>'",
                mode,
            )

        # Switch completion manager based on mode
        if mode == "shell":
            self._completion_manager = MultiCompletionManager(
                [self._shell_controller]  # type: ignore[list-item]
            )
        else:
            self._completion_manager = MultiCompletionManager(
                [
                    self._slash_controller,
                    self._file_controller,
                ]  # type: ignore[list-item]
            )

        def _apply() -> None:
            self.remove_class("mode-shell", "mode-command")
            if glyph:
                self.add_class(f"mode-{mode}")
            try:
                prompt = self.query_one("#prompt", Static)
            except NoMatches:
                logger.warning("watch_mode._apply: #prompt widget not found")
                return
            prompt.update(glyph or ">")

        self.call_after_refresh(_apply)
        self.post_message(self.ModeChanged(mode))

    def exit_mode(self) -> bool:
        """Exit the current input mode (command/shell) back to normal.

        Returns:
            True if mode was non-normal and has been reset.
        """
        if self.mode == "normal":
            return False
        self.mode = "normal"
        if self._completion_manager:
            self._completion_manager.reset()
        self.clear_completion_suggestions()
        return True

    def dismiss_completion(self) -> bool:
        """Dismiss completion: clear view and reset controller state.

        Returns:
            True if completion was active and has been dismissed.
        """
        if not self._current_suggestions:
            return False
        if self._completion_manager:
            self._completion_manager.reset()
        # Always clear local state so the popup is hidden even if the
        # manager's active controller was already None (no-op reset).
        self.clear_completion_suggestions()
        return True

    def render_completion_suggestions(
        self, suggestions: list[tuple[str, str]], selected_index: int
    ) -> None:
        """Render completion suggestions in the popup."""
        prev_suggestions = self._current_suggestions
        self._current_suggestions = suggestions
        self._current_selected_index = selected_index

        if self._popup:
            # If only the selection changed (same items), skip full rebuild
            if suggestions == prev_suggestions:
                self._popup.update_selection(selected_index)
            else:
                self._popup.update_suggestions(suggestions, selected_index)
        # Tell TextArea that completion is active so it yields navigation keys
        if self._text_area:
            self._text_area.set_completion_active(active=bool(suggestions))

    def clear_completion_suggestions(self) -> None:
        """Clear/hide the completion popup."""
        self._current_suggestions = []
        self._current_selected_index = 0

        if self._popup:
            self._popup.hide()
        # Tell TextArea that completion is no longer active
        if self._text_area:
            self._text_area.set_completion_active(active=False)

    def on_completion_popup_option_clicked(
        self, event: CompletionPopup.OptionClicked
    ) -> None:
        """Handle click on a completion option."""
        if not self._current_suggestions or not self._text_area:
            return

        index = event.index
        if index < 0 or index >= len(self._current_suggestions):
            return

        # Get the selected completion
        label, _ = self._current_suggestions[index]
        text = self._text_area.text
        cursor = self._get_cursor_offset()

        # Determine replacement range based on completion type.
        # Slash completions use completion-space coordinates and are translated
        # through the completion view adapter.
        if label.startswith("/"):
            if self._completion_view is None:
                logger.warning(
                    "Slash completion clicked but _completion_view is not "
                    "initialized; this indicates a widget lifecycle issue."
                )
                return
            _, virtual_cursor = self._completion_text_and_cursor()
            self._completion_view.replace_completion_range(0, virtual_cursor, label)
        elif label.startswith("@"):
            # File mention: replace from @ to cursor
            at_index = text[:cursor].rfind("@")
            if at_index >= 0:
                self.replace_completion_range(at_index, cursor, label)

        # Reset completion state
        if self._completion_manager:
            self._completion_manager.reset()

        # Re-focus the text input after click
        self._text_area.focus()

    def replace_completion_range(self, start: int, end: int, replacement: str) -> None:
        """Replace text in the input field."""
        if not self._text_area:
            return

        text = self._text_area.text

        start = max(0, min(start, len(text)))
        end = max(start, min(end, len(text)))

        prefix = text[:start]
        suffix = text[end:]

        # Use replacement as-is; controllers handle space insertion
        insertion = replacement

        new_text = f"{prefix}{insertion}{suffix}"

        # Set flag to prevent change event from re-triggering completion
        self._applying_completion = True
        self._text_area.text = new_text

        # Calculate new cursor position and move cursor
        new_offset = start + len(insertion)
        lines = new_text.split("\n")
        remaining = new_offset
        for row, line in enumerate(lines):
            if remaining <= len(line):
                self._text_area.move_cursor((row, remaining))
                break
            remaining -= len(line) + 1
