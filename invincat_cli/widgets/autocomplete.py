"""Autocomplete system for @ mentions and / commands.

This is a custom implementation that handles trigger-based completion
for slash commands (/) and file mentions (@).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from invincat_cli.widgets import autocomplete_file_utils as _file_utils
from invincat_cli.widgets.autocomplete_shell_utils import (
    _escape_path,
    _get_common_commands,
    _get_longest_common_prefix,
    _get_system_commands,
    _parse_shell_tokens,
    _unescape_token,
)

if TYPE_CHECKING:
    from textual import events


class CompletionResult(StrEnum):
    """Result of handling a key event in the completion system."""

    IGNORED = "ignored"  # Key not handled, let default behavior proceed
    HANDLED = "handled"  # Key handled, prevent default
    SUBMIT = "submit"  # Key triggers submission (e.g., Enter on slash command)


class CompletionView(Protocol):
    """Protocol for views that can display completion suggestions."""

    def render_completion_suggestions(
        self, suggestions: list[tuple[str, str]], selected_index: int
    ) -> None:
        """Render the completion suggestions popup.

        Args:
            suggestions: List of (label, description) tuples
            selected_index: Index of currently selected item
        """
        ...

    def clear_completion_suggestions(self) -> None:
        """Hide/clear the completion suggestions popup."""
        ...

    def replace_completion_range(self, start: int, end: int, replacement: str) -> None:
        """Replace text in the input from start to end with replacement.

        Args:
            start: Start index in the input text
            end: End index in the input text
            replacement: Text to insert
        """
        ...


class CompletionController(Protocol):
    """Protocol for completion controllers."""

    def can_handle(self, text: str, cursor_index: int) -> bool:
        """Check if this controller can handle the current input state."""
        ...

    def on_text_changed(self, text: str, cursor_index: int) -> None:
        """Called when input text changes."""
        ...

    def on_key(
        self, event: events.Key, text: str, cursor_index: int
    ) -> CompletionResult:
        """Handle a key event. Returns how the event was handled."""
        ...

    def reset(self) -> None:
        """Reset/clear the completion state."""
        ...


# ============================================================================
# Slash Command Completion
# ============================================================================


MAX_SUGGESTIONS = 25
"""UI cap so the completion popup doesn't get unwieldy."""

_MIN_SLASH_FUZZY_SCORE = 25
"""Minimum score for slash-command fuzzy matches."""

_MIN_DESC_SEARCH_LEN = 2
"""Minimum query length to search command descriptions (avoids single-char noise)."""

from invincat_cli.widgets.autocomplete_slash import SlashCommandController

__all__ = [
    "CompletionController",
    "CompletionResult",
    "CompletionView",
    "FuzzyFileController",
    "MultiCompletionManager",
    "PathCompletionController",
    "ShellCompletionController",
    "SlashCommandController",
    "_MAX_FALLBACK_FILES",
    "_MIN_FUZZY_RATIO",
    "_MIN_FUZZY_SCORE",
    "_escape_path",
    "_fuzzy_score",
    "_fuzzy_search",
    "_get_common_commands",
    "_get_git_executable",
    "_get_longest_common_prefix",
    "_get_project_files",
    "_get_system_commands",
    "_is_dotpath",
    "_parse_shell_tokens",
    "_path_depth",
    "_unescape_token",
    "asyncio",
    "os",
    "shutil",
    "subprocess",
]


_MAX_FALLBACK_FILES = _file_utils._MAX_FALLBACK_FILES
"""Hard cap on files returned by the non-git glob fallback."""

_MIN_FUZZY_SCORE = _file_utils._MIN_FUZZY_SCORE
"""Minimum score to include in file-completion results."""

_MIN_FUZZY_RATIO = _file_utils._MIN_FUZZY_RATIO
"""SequenceMatcher threshold for filename-only fuzzy matches."""


def _get_git_executable() -> str | None:
    """Get full path to git executable using shutil.which().

    Returns:
        Full path to git executable, or None if not found.
    """
    return shutil.which("git")


def _get_project_files(root: Path) -> list[str]:
    """Get project files using git ls-files or fallback to glob.

    Returns:
        List of relative file paths from project root.
    """
    return _file_utils._get_project_files(
        root,
        get_git_executable=_get_git_executable,
        max_fallback_files=_MAX_FALLBACK_FILES,
        run_command=subprocess.run,
    )


def _fuzzy_score(query: str, candidate: str) -> float:
    """Score a candidate against query. Higher = better match.

    Returns:
        Score value where higher indicates better match quality.
    """
    return _file_utils._fuzzy_score(
        query,
        candidate,
        min_fuzzy_ratio=_MIN_FUZZY_RATIO,
    )


def _is_dotpath(path: str) -> bool:
    """Check if path contains dotfiles/dotdirs (e.g., .github/...).

    Returns:
        True if path contains hidden directories or files.
    """
    return _file_utils._is_dotpath(path)


def _path_depth(path: str) -> int:
    """Get depth of path (number of / separators).

    Returns:
        Number of path separators in the path.
    """
    return _file_utils._path_depth(path)


def _fuzzy_search(
    query: str,
    candidates: list[str],
    limit: int = 10,
    *,
    include_dotfiles: bool = False,
) -> list[str]:
    """Return top matches sorted by score.

    Args:
        query: Search query
        candidates: List of file paths to search
        limit: Max results to return
        include_dotfiles: Whether to include dotfiles (default False)

    Returns:
        List of matching file paths sorted by relevance score.
    """
    return _file_utils._fuzzy_search(
        query,
        candidates,
        limit=limit,
        include_dotfiles=include_dotfiles,
        min_fuzzy_score=_MIN_FUZZY_SCORE,
        fuzzy_score=_fuzzy_score,
    )


from invincat_cli.widgets.autocomplete_files import FuzzyFileController

PathCompletionController = FuzzyFileController


from invincat_cli.widgets.autocomplete_shell import ShellCompletionController


class MultiCompletionManager:
    """Manages multiple completion controllers, delegating to the active one."""

    def __init__(self, controllers: list[CompletionController]) -> None:
        """Initialize with a list of controllers.

        Args:
            controllers: List of completion controllers (checked in order)
        """
        self._controllers = controllers
        self._active: CompletionController | None = None

    def on_text_changed(self, text: str, cursor_index: int) -> None:
        """Handle text change, activating the appropriate controller."""
        # Find the first controller that can handle this input
        candidate = None
        for controller in self._controllers:
            if controller.can_handle(text, cursor_index):
                candidate = controller
                break

        # No controller can handle - reset if we had one active
        if candidate is None:
            if self._active is not None:
                self._active.reset()
                self._active = None
            return

        # Switch to new controller if different
        if candidate is not self._active:
            if self._active is not None:
                self._active.reset()
            self._active = candidate

        # Let the active controller process the change
        candidate.on_text_changed(text, cursor_index)

    def on_key(
        self, event: events.Key, text: str, cursor_index: int
    ) -> CompletionResult:
        """Handle key event, delegating to active controller.

        Returns:
            CompletionResult from active controller, or IGNORED if none active.
        """
        if self._active is None:
            return CompletionResult.IGNORED
        return self._active.on_key(event, text, cursor_index)

    def reset(self) -> None:
        """Reset all controllers."""
        if self._active is not None:
            self._active.reset()
            self._active = None
