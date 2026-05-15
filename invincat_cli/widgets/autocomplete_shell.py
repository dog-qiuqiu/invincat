"""Shell-command completion controller."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

from invincat_cli.widgets import autocomplete as _autocomplete
from invincat_cli.widgets.autocomplete import CompletionResult, CompletionView

if TYPE_CHECKING:
    from textual import events


class ShellCompletionController:
    """Controller for shell command completion (! mode).

    Provides:
    - Command name completion (from PATH)
    - File/directory path completion
    - Bash-like Tab behavior (double Tab shows all options)
    """

    def __init__(
        self,
        view: CompletionView,
        cwd: Path | None = None,
    ) -> None:
        """Initialize the shell completion controller.

        Args:
            view: View to render suggestions to
            cwd: Current working directory for path completion
        """
        self._view = view
        self._cwd = cwd or Path.cwd()
        self._suggestions: list[tuple[str, str]] = []
        self._selected_index = 0
        self._command_cache: list[str] | None = None
        self._tab_count = 0
        self._last_text = ""
        self._completion_start = 0
        self._is_cycling = False
        self._original_token = ""
        self._original_completion_start = 0
        self._current_completion_end = 0

    def _get_commands(self) -> list[str]:
        """Get cached command list or refresh.

        Returns:
            List of available shell commands.
        """
        if self._command_cache is None:
            self._command_cache = _autocomplete._get_system_commands()
        return self._command_cache

    def refresh_cache(self) -> None:
        """Force refresh of command cache."""
        self._command_cache = None

    async def warm_cache(self) -> None:
        """Pre-populate the command cache off the event loop."""
        if self._command_cache is not None:
            return
        with contextlib.suppress(Exception):
            self._command_cache = await _autocomplete.asyncio.to_thread(_autocomplete._get_system_commands)

    @staticmethod
    def can_handle(text: str, cursor_index: int) -> bool:
        """Always handle in shell mode.

        Returns:
            Always True - shell completion is active when mode is 'shell'.
        """
        return True

    def reset(self) -> None:
        """Clear suggestions and reset tab count."""
        if self._suggestions:
            self._suggestions.clear()
            self._selected_index = 0
            self._tab_count = 0
            self._last_text = ""
            self._is_cycling = False
            self._original_token = ""
            self._original_completion_start = 0
            self._current_completion_end = 0

    def _strip_prefix(self, text: str) -> tuple[str, int]:
        """Strip the ! prefix from shell command text.

        Args:
            text: Text that may start with ! prefix.

        Returns:
            Tuple of (stripped_text, prefix_length).
        """
        if text.startswith("!"):
            return text[1:], 1
        return text, 0

    def on_text_changed(self, text: str, cursor_index: int) -> None:
        """Update suggestions when text changes."""
        if self._is_cycling:
            self._is_cycling = False
            self._original_token = ""
            self._current_completion_end = 0

        self._tab_count = 0

        # Strip ! prefix for shell mode
        stripped_text, prefix_len = self._strip_prefix(text)
        stripped_cursor = max(0, cursor_index - prefix_len)
        text_before_cursor = stripped_text[:stripped_cursor]
        self._last_text = text_before_cursor

        if not stripped_text.strip():
            self.reset()
            return

        # Check if cursor is after a space (typing arguments)
        # e.g., "ls " means user is about to type a path argument
        if text_before_cursor.endswith(" ") or text_before_cursor.endswith("\t"):
            # Store suggestions but don't show popup
            suggestions = self._get_path_suggestions("")
            if suggestions:
                self._suggestions = suggestions
                self._selected_index = 0
                self._completion_start = cursor_index
            else:
                self.reset()
            return

        tokens = _autocomplete._parse_shell_tokens(text_before_cursor)
        if not tokens:  # pragma: no cover - non-blank shell text always yields a token
            self.reset()
            return

        last_token = tokens[-1]
        is_first_token = len(tokens) == 1

        if is_first_token:
            suggestions = self._get_command_suggestions(last_token)
        else:
            suggestions = self._get_path_suggestions(last_token)

        if suggestions:
            self._suggestions = suggestions
            self._selected_index = 0
            # Calculate completion start in original text space (with prefix)
            self._completion_start = prefix_len + stripped_cursor - len(last_token)
        else:
            self.reset()

    def _get_command_suggestions(self, prefix: str) -> list[tuple[str, str]]:
        """Get command name suggestions.

        Args:
            prefix: Command prefix to match.

        Returns:
            List of (command, description) tuples.
        """
        if not prefix:
            common = _autocomplete._get_common_commands()[:_autocomplete.MAX_SUGGESTIONS]
            return [(cmd, "command") for cmd in common]

        raw_prefix = _autocomplete._unescape_token(prefix)
        commands = self._get_commands()
        matches = [cmd for cmd in commands if cmd.startswith(raw_prefix.lower())]

        return [(cmd, "command") for cmd in matches[:_autocomplete.MAX_SUGGESTIONS]]

    def _get_path_suggestions(self, prefix: str) -> list[tuple[str, str]]:
        """Get file/directory path suggestions.

        Args:
            prefix: Path prefix to match.

        Returns:
            List of (path, type) tuples.
        """
        raw_prefix = _autocomplete._unescape_token(prefix)

        if raw_prefix == "~":
            raw_prefix = str(Path.home())
        elif raw_prefix.startswith("~/"):
            raw_prefix = str(Path.home() / raw_prefix[2:])
        elif not raw_prefix.startswith("/"):
            raw_prefix = str(self._cwd / raw_prefix)

        if raw_prefix.endswith("/"):
            dir_path = Path(raw_prefix)
            file_prefix = ""
        else:
            dir_path = Path(raw_prefix).parent
            file_prefix = Path(raw_prefix).name

        if not dir_path.is_dir():
            return []

        try:
            entries = list(dir_path.iterdir())
        except OSError:
            return []

        suggestions: list[tuple[str, str]] = []
        for entry in sorted(entries, key=lambda e: e.name.lower()):
            name = entry.name
            if file_prefix and not name.startswith(file_prefix):
                continue
            if name.startswith(".") and not file_prefix.startswith("."):
                continue

            if entry.is_dir():
                suggestions.append((name + "/", "dir"))
            else:
                suggestions.append((name, "file"))

            if len(suggestions) >= _autocomplete.MAX_SUGGESTIONS:
                break

        return suggestions

    def on_key(
        self, event: events.Key, text: str, cursor_index: int
    ) -> CompletionResult:
        """Handle key events for navigation and selection.

        Returns:
            CompletionResult indicating how the key was handled.
        """
        match event.key:
            case "tab":
                return self._handle_tab(text, cursor_index)
            case "enter":
                if self._suggestions:
                    # Initialize original token if not already done
                    if not self._original_token:
                        stripped_text, prefix_len = self._strip_prefix(
                            text[:cursor_index]
                        )
                        tokens = _autocomplete._parse_shell_tokens(stripped_text)

                        # Check if cursor is after a space (typing arguments)
                        if stripped_text.endswith(" ") or stripped_text.endswith("\t"):
                            self._original_token = ""
                        elif tokens:
                            self._original_token = tokens[-1]
                        else:
                            self._original_token = ""

                        self._original_completion_start = self._completion_start
                        self._current_completion_end = cursor_index
                    self._apply_completion_for_token()
                    self.reset()
                    return CompletionResult.SUBMIT
                return CompletionResult.IGNORED
            case "down":
                if self._suggestions:
                    self._move_selection(1)
                    return CompletionResult.HANDLED
                return CompletionResult.IGNORED
            case "up":
                if self._suggestions:
                    self._move_selection(-1)
                    return CompletionResult.HANDLED
                return CompletionResult.IGNORED
            case "escape":
                self.reset()
                return CompletionResult.HANDLED
            case _:
                return CompletionResult.IGNORED

    def _handle_tab(self, text: str, cursor_index: int) -> CompletionResult:
        """Handle Tab key with cycle completion behavior.

        Tab: Cycle through suggestions one by one

        Args:
            text: Current input text.
            cursor_index: Current cursor position.

        Returns:
            CompletionResult indicating how the event was handled.
        """
        if not self._suggestions:
            self.on_text_changed(text, cursor_index)
            if not self._suggestions:
                return CompletionResult.IGNORED

        # Initialize cycling state on first tab (when not already cycling)
        if not self._original_token:
            # Extract the original token (without prefix) for cycling
            stripped_text, prefix_len = self._strip_prefix(text[:cursor_index])
            tokens = _autocomplete._parse_shell_tokens(stripped_text)

            # Check if cursor is after a space (typing arguments)
            # In this case, the token to complete is empty
            if stripped_text.endswith(" ") or stripped_text.endswith("\t"):
                self._original_token = ""
            elif tokens:
                self._original_token = tokens[-1]
            else:
                self._original_token = ""

            self._original_completion_start = self._completion_start
            self._current_completion_end = cursor_index
            self._selected_index = 0

        # If only one suggestion, apply it and finish
        if len(self._suggestions) == 1:
            self._is_cycling = True
            self._completion_start = self._original_completion_start
            self._apply_completion_for_token()
            self.reset()
            return CompletionResult.HANDLED

        # Apply current suggestion
        self._is_cycling = True
        self._completion_start = self._original_completion_start
        self._apply_completion_for_token()
        # Move to next suggestion for next tab
        self._selected_index = (self._selected_index + 1) % len(self._suggestions)
        return CompletionResult.HANDLED

    def _apply_completion_for_token(self) -> bool:
        """Apply the currently selected completion based on original token.

        Returns:
            True if completion was applied, False if no suggestions.
        """
        if not self._suggestions:
            return False

        label, type_hint = self._suggestions[self._selected_index]

        escaped = _autocomplete._escape_path(label)

        # Determine if this is a command (first token)
        is_command = type_hint == "command"
        is_dir = type_hint == "dir"

        if is_command:
            # Add space after command completion
            escaped += " "
        elif is_dir:
            # Directory completion: no space, user may want to continue typing path
            pass
        else:
            # File completion: add space after file name
            escaped += " "

        # Replace from completion start to current completion end
        # This handles both first completion and cycling through suggestions
        self._view.replace_completion_range(
            self._completion_start,
            self._current_completion_end,
            escaped,
        )
        # Update the end position for the next cycle
        self._current_completion_end = self._completion_start + len(escaped)
        return True

    def _move_selection(self, delta: int) -> None:
        """Move selection up or down."""
        if not self._suggestions:
            return
        count = len(self._suggestions)
        self._selected_index = (self._selected_index + delta) % count


# ============================================================================
# Multi-Completion Manager
# ============================================================================
