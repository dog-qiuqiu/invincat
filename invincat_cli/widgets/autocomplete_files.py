"""File-path completion controller."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

from invincat_cli.project_utils import find_project_root
from invincat_cli.widgets import autocomplete as _autocomplete
from invincat_cli.widgets.autocomplete import CompletionResult, CompletionView

if TYPE_CHECKING:
    from textual import events


class FuzzyFileController:
    """Controller for @ file completion with fuzzy matching from project root."""

    def __init__(
        self,
        view: CompletionView,
        cwd: Path | None = None,
    ) -> None:
        """Initialize the fuzzy file controller.

        Args:
            view: View to render suggestions to
            cwd: Starting directory to find project root from
        """
        self._view = view
        self._cwd = cwd or Path.cwd()
        self._project_root = find_project_root(self._cwd) or self._cwd
        self._suggestions: list[tuple[str, str]] = []
        self._selected_index = 0
        self._file_cache: list[str] | None = None

    def _get_files(self) -> list[str]:
        """Get cached file list or refresh.

        Returns:
            List of project file paths.
        """
        if self._file_cache is None:
            self._file_cache = _autocomplete._get_project_files(self._project_root)
        return self._file_cache

    def refresh_cache(self) -> None:
        """Force refresh of file cache."""
        self._file_cache = None

    async def warm_cache(self) -> None:
        """Pre-populate the file cache off the event loop."""
        if self._file_cache is not None:
            return
        # Best-effort; _get_files() falls back to sync on failure.
        with contextlib.suppress(Exception):
            self._file_cache = await asyncio.to_thread(
                _autocomplete._get_project_files, self._project_root
            )

    @staticmethod
    def can_handle(text: str, cursor_index: int) -> bool:
        """Handle input that contains @ not followed by space.

        Returns:
            True if cursor is after @ and within a file mention context.
        """
        if cursor_index <= 0 or cursor_index > len(text):
            return False

        before_cursor = text[:cursor_index]
        if "@" not in before_cursor:
            return False

        at_index = before_cursor.rfind("@")
        if cursor_index <= at_index:  # pragma: no cover - rfind is bounded by slice end
            return False

        # Fragment from @ to cursor must not contain spaces
        fragment = before_cursor[at_index:cursor_index]
        return bool(fragment) and " " not in fragment

    def reset(self) -> None:
        """Clear suggestions."""
        if self._suggestions:
            self._suggestions.clear()
            self._selected_index = 0
            self._view.clear_completion_suggestions()

    def on_text_changed(self, text: str, cursor_index: int) -> None:
        """Update suggestions when text changes."""
        if not self.can_handle(text, cursor_index):
            self.reset()
            return

        before_cursor = text[:cursor_index]
        at_index = before_cursor.rfind("@")
        search = before_cursor[at_index + 1 :]

        suggestions = self._get_fuzzy_suggestions(search)

        if suggestions:
            self._suggestions = suggestions
            self._selected_index = 0
            self._view.render_completion_suggestions(
                self._suggestions, self._selected_index
            )
        else:
            self.reset()

    def _get_fuzzy_suggestions(self, search: str) -> list[tuple[str, str]]:
        """Get fuzzy file suggestions.

        Returns:
            List of (label, type_hint) tuples for matching files.
        """
        files = self._get_files()
        # Include dotfiles only if query starts with "."
        include_dots = search.startswith(".")
        matches = _autocomplete._fuzzy_search(
            search, files, limit=_autocomplete.MAX_SUGGESTIONS, include_dotfiles=include_dots
        )

        suggestions: list[tuple[str, str]] = []
        for path in matches:
            # Get file extension for type hint
            ext = Path(path).suffix.lower()
            type_hint = ext[1:] if ext else "file"
            suggestions.append((f"@{path}", type_hint))

        return suggestions

    def on_key(
        self, event: events.Key, text: str, cursor_index: int
    ) -> CompletionResult:
        """Handle key events for navigation and selection.

        Returns:
            CompletionResult indicating how the key was handled.
        """
        if not self._suggestions:
            return CompletionResult.IGNORED

        match event.key:
            case "tab" | "enter":
                if self._apply_selected_completion(text, cursor_index):
                    return CompletionResult.HANDLED
                return CompletionResult.IGNORED
            case "down":
                self._move_selection(1)
                return CompletionResult.HANDLED
            case "up":
                self._move_selection(-1)
                return CompletionResult.HANDLED
            case "escape":
                self.reset()
                return CompletionResult.HANDLED
            case _:
                return CompletionResult.IGNORED

    def _move_selection(self, delta: int) -> None:
        """Move selection up or down."""
        if not self._suggestions:
            return
        count = len(self._suggestions)
        self._selected_index = (self._selected_index + delta) % count
        self._view.render_completion_suggestions(
            self._suggestions, self._selected_index
        )

    def _apply_selected_completion(self, text: str, cursor_index: int) -> bool:
        """Apply the currently selected completion.

        Returns:
            True if completion was applied, False if no suggestions or invalid state.
        """
        if not self._suggestions:
            return False

        label, _ = self._suggestions[self._selected_index]
        before_cursor = text[:cursor_index]
        at_index = before_cursor.rfind("@")

        if at_index < 0:
            return False

        # Replace from @ to cursor with the completion
        self._view.replace_completion_range(at_index, cursor_index, label)
        self.reset()
        return True


# Keep old name as alias for backwards compatibility
PathCompletionController = FuzzyFileController


# ============================================================================
# Shell Command Completion (for ! mode)
# ============================================================================
