"""Autocomplete system for @ mentions and / commands.

This is a custom implementation that handles trigger-based completion
for slash commands (/) and file mentions (@).
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil

# S404: subprocess is required for git ls-files to get project file list
import subprocess  # noqa: S404
from difflib import SequenceMatcher
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from invincat_cli.project_utils import find_project_root


def _get_git_executable() -> str | None:
    """Get full path to git executable using shutil.which().

    Returns:
        Full path to git executable, or None if not found.
    """
    return shutil.which("git")


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


class SlashCommandController:
    """Controller for / slash command completion."""

    def __init__(
        self,
        commands: list[tuple[str, str, str]],
        view: CompletionView,
    ) -> None:
        """Initialize the slash command controller.

        Args:
            commands: List of `(command, description, hidden_keywords)` tuples.
            view: View to render suggestions to.
        """
        self._commands = commands
        self._view = view
        self._suggestions: list[tuple[str, str]] = []
        self._selected_index = 0

    def update_commands(self, commands: list[tuple[str, str, str]]) -> None:
        """Replace the commands list and reset suggestions.

        Used to merge dynamically discovered skill commands with
        the static command registry at runtime.

        Args:
            commands: New list of `(command, description, hidden_keywords)` tuples.
        """
        self._commands = commands
        self.reset()

    @staticmethod
    def can_handle(text: str, cursor_index: int) -> bool:  # noqa: ARG004  # Required by AutocompleteProvider interface
        """Handle input that starts with /.

        Returns:
            True if text starts with slash, indicating a command.
        """
        return text.startswith("/")

    def reset(self) -> None:
        """Clear suggestions."""
        if self._suggestions:
            self._suggestions.clear()
            self._selected_index = 0
            self._view.clear_completion_suggestions()

    @staticmethod
    def _score_command(search: str, cmd: str, desc: str, keywords: str = "") -> float:
        """Score a command against a search string. Higher = better match.

        Args:
            search: Lowercase search string (without leading `/`).
            cmd: Command name (e.g. `'/help'`).
            desc: Command description text.
            keywords: Space-separated hidden keywords for matching.

        Returns:
            Score value where higher indicates better match quality.
        """
        if not search:
            return 0.0
        name = cmd.lstrip("/").lower()
        lower_desc = desc.lower()
        # Prefix match on command name — highest priority
        if name.startswith(search):
            return 200.0
        # Substring match on command name
        if search in name:
            return 150.0
        # Hidden keyword match — treated like a word-boundary description match
        if keywords and len(search) >= _MIN_DESC_SEARCH_LEN:
            for kw in keywords.lower().split():
                if kw.startswith(search) or search in kw:
                    return 120.0
        # Substring match on description (require ≥2 chars to avoid single-letter noise)
        if len(search) >= _MIN_DESC_SEARCH_LEN and search in lower_desc:
            idx = lower_desc.find(search)
            # Word-boundary bonus: match at start of description or after a space
            if idx == 0 or lower_desc[idx - 1] == " ":
                return 110.0
            return 90.0
        # Fuzzy match via SequenceMatcher on name + desc
        name_ratio = SequenceMatcher(None, search, name).ratio()
        desc_ratio = SequenceMatcher(None, search, lower_desc).ratio()
        best = max(name_ratio * 60, desc_ratio * 30)
        return best if best >= _MIN_SLASH_FUZZY_SCORE else 0.0

    def on_text_changed(self, text: str, cursor_index: int) -> None:
        """Update suggestions when text changes."""
        if cursor_index < 0 or cursor_index > len(text):
            self.reset()
            return

        if not self.can_handle(text, cursor_index):
            self.reset()
            return

        # Get the search string (text after /)
        search = text[1:cursor_index].lower()

        if not search:
            # No search text — show all commands (display only cmd + desc)
            suggestions = [(cmd, desc) for cmd, desc, _ in self._commands][
                :MAX_SUGGESTIONS
            ]
        else:
            # Score and filter commands using fuzzy matching
            scored = [
                (score, cmd, desc)
                for cmd, desc, kw in self._commands
                if (score := self._score_command(search, cmd, desc, kw)) > 0
            ]
            scored.sort(key=lambda x: -x[0])
            suggestions = [(cmd, desc) for _, cmd, desc in scored[:MAX_SUGGESTIONS]]

        if suggestions:
            self._suggestions = suggestions
            self._selected_index = 0
            self._view.render_completion_suggestions(
                self._suggestions, self._selected_index
            )
        else:
            self.reset()

    def on_key(
        self, event: events.Key, _text: str, cursor_index: int
    ) -> CompletionResult:
        """Handle key events for navigation and selection.

        Returns:
            CompletionResult indicating how the key was handled.
        """
        if not self._suggestions:
            return CompletionResult.IGNORED

        match event.key:
            case "tab":
                if self._apply_selected_completion(cursor_index):
                    return CompletionResult.HANDLED
                return CompletionResult.IGNORED
            case "enter":
                if self._apply_selected_completion(cursor_index):
                    return CompletionResult.SUBMIT
                return CompletionResult.HANDLED
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

    def _apply_selected_completion(self, cursor_index: int) -> bool:
        """Apply the currently selected completion.

        Returns:
            True if completion was applied, False if no suggestions.
        """
        if not self._suggestions:
            return False

        command, _ = self._suggestions[self._selected_index]
        # Replace from start to cursor with the command
        self._view.replace_completion_range(0, cursor_index, command)
        self.reset()
        return True


# ============================================================================
# Fuzzy File Completion (from project root)
# ============================================================================

# Constants for fuzzy file completion
_MAX_FALLBACK_FILES = 1000
"""Hard cap on files returned by the non-git glob fallback."""

_MIN_FUZZY_SCORE = 15
"""Minimum score to include in file-completion results."""

_MIN_FUZZY_RATIO = 0.4
"""SequenceMatcher threshold for filename-only fuzzy matches."""


def _get_project_files(root: Path) -> list[str]:
    """Get project files using git ls-files or fallback to glob.

    Returns:
        List of relative file paths from project root.
    """
    git_path = _get_git_executable()
    if git_path:
        try:
            # S603: git_path is validated via shutil.which(), args are hardcoded
            result = subprocess.run(  # noqa: S603
                [git_path, "ls-files"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                files = result.stdout.strip().split("\n")
                return [f for f in files if f]  # Filter empty strings
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    # Fallback: simple glob (limited depth to avoid slowness)
    files = []
    try:
        for pattern in ["*", "*/*", "*/*/*", "*/*/*/*"]:
            for p in root.glob(pattern):
                if p.is_file() and not any(part.startswith(".") for part in p.parts):
                    files.append(p.relative_to(root).as_posix())
                if len(files) >= _MAX_FALLBACK_FILES:
                    break
            if len(files) >= _MAX_FALLBACK_FILES:
                break
    except OSError:
        pass
    return files


def _fuzzy_score(query: str, candidate: str) -> float:
    """Score a candidate against query. Higher = better match.

    Returns:
        Score value where higher indicates better match quality.
    """
    query_lower = query.lower()
    # Normalize path separators for cross-platform support
    candidate_normalized = candidate.replace("\\", "/")
    candidate_lower = candidate_normalized.lower()

    # Extract filename for matching (prioritize filename over full path)
    filename = candidate_normalized.rsplit("/", 1)[-1].lower()
    filename_start = candidate_lower.rfind("/") + 1

    # Check filename first (higher priority)
    if query_lower in filename:
        idx = filename.find(query_lower)
        # Bonus for being at start of filename
        if idx == 0:
            return 150 + (1 / len(candidate))
        # Bonus for word boundary in filename
        if idx > 0 and filename[idx - 1] in "_-.":
            return 120 + (1 / len(candidate))
        return 100 + (1 / len(candidate))

    # Check full path
    if query_lower in candidate_lower:
        idx = candidate_lower.find(query_lower)
        # At start of filename
        if idx == filename_start:
            return 80 + (1 / len(candidate))
        # At word boundary in path
        if idx == 0 or candidate[idx - 1] in "/_-.":
            return 60 + (1 / len(candidate))
        return 40 + (1 / len(candidate))

    # Fuzzy match on filename only (more relevant)
    filename_ratio = SequenceMatcher(None, query_lower, filename).ratio()
    if filename_ratio > _MIN_FUZZY_RATIO:
        return filename_ratio * 30

    # Fallback: fuzzy on full path
    ratio = SequenceMatcher(None, query_lower, candidate_lower).ratio()
    return ratio * 15


def _is_dotpath(path: str) -> bool:
    """Check if path contains dotfiles/dotdirs (e.g., .github/...).

    Returns:
        True if path contains hidden directories or files.
    """
    return any(part.startswith(".") for part in path.split("/"))


def _path_depth(path: str) -> int:
    """Get depth of path (number of / separators).

    Returns:
        Number of path separators in the path.
    """
    return path.count("/")


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
    # Filter dotfiles unless explicitly searching for them
    filtered = (
        candidates
        if include_dotfiles
        else [c for c in candidates if not _is_dotpath(c)]
    )

    if not query:
        # Empty query: show root-level files first, sorted by depth then name
        sorted_files = sorted(filtered, key=lambda p: (_path_depth(p), p.lower()))
        return sorted_files[:limit]

    scored = [
        (score, c)
        for c in filtered
        if (score := _fuzzy_score(query, c)) >= _MIN_FUZZY_SCORE
    ]
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:limit]]


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
            self._file_cache = _get_project_files(self._project_root)
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
                _get_project_files, self._project_root
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
        if cursor_index <= at_index:
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
        matches = _fuzzy_search(
            search, files, limit=MAX_SUGGESTIONS, include_dotfiles=include_dots
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

import os
import re


def _get_system_commands() -> list[str]:
    """Get all executable commands from PATH.

    Returns:
        List of command names available in PATH.
    """
    commands: set[str] = set()
    path_env = os.environ.get("PATH", "")

    for path_dir in path_env.split(os.pathsep):
        if not path_dir:
            continue
        try:
            path_obj = Path(path_dir)
            if not path_obj.is_dir():
                continue
            for entry in path_obj.iterdir():
                if entry.is_file() and os.access(entry, os.X_OK):
                    commands.add(entry.name)
        except OSError:
            continue

    return sorted(commands)


def _get_common_commands() -> list[str]:
    """Get a list of common shell commands for faster matching.

    Returns:
        List of commonly used command names.
    """
    return [
        "ls", "cd", "pwd", "cat", "echo", "mkdir", "rm", "rmdir",
        "cp", "mv", "touch", "find", "grep", "sed", "awk", "sort",
        "head", "tail", "wc", "diff", "chmod", "chown", "ln",
        "ps", "kill", "top", "htop", "df", "du", "free", "uname",
        "date", "cal", "which", "whereis", "whoami", "id",
        "tar", "gzip", "gunzip", "zip", "unzip",
        "curl", "wget", "ssh", "scp", "rsync", "ftp",
        "git", "svn", "hg",
        "python", "python3", "pip", "pip3", "node", "npm", "yarn",
        "docker", "docker-compose", "kubectl",
        "make", "cmake", "gcc", "g++", "clang", "clang++",
        "vi", "vim", "nvim", "nano", "emacs", "code",
        "man", "less", "more", "clear", "history", "alias",
        "export", "source", "env", "printenv",
        "xargs", "tee", "tr", "cut", "paste", "join",
        "nohup", "bg", "fg", "jobs",
    ]


def _escape_path(path: str) -> str:
    """Escape special characters in a path for shell.

    Args:
        path: Path string to escape.

    Returns:
        Escaped path safe for shell use.
    """
    if not path:
        return path

    needs_escape = bool(re.search(r'[ \t\n!$`&*()\\|;"\'<>?{}]', path))
    if needs_escape:
        escaped = path.replace("'", "'\"'\"'")
        return f"'{escaped}'"
    return path


def _unescape_token(token: str) -> str:
    """Unescape a shell token to get the raw string.

    Args:
        token: Escaped token from shell command line.

    Returns:
        Unescaped raw string.
    """
    if not token:
        return token

    if len(token) >= 2 and token[0] == "'" and token[-1] == "'":
        return token[1:-1].replace("'\"'\"'", "'")

    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        result = token[1:-1]
        result = result.replace("\\$", "$")
        result = result.replace("\\`", "`")
        result = result.replace('\\"', '"')
        result = result.replace("\\\\", "\\")
        return result

    result = token.replace("\\ ", " ")
    result = result.replace("\\$", "$")
    result = result.replace("\\`", "`")
    result = result.replace('\\"', '"')
    result = result.replace("\\'", "'")
    result = result.replace("\\\\", "\\")
    return result


def _parse_shell_tokens(text: str) -> list[str]:
    """Parse shell command line into tokens.

    Handles quoted strings and escaped characters.

    Args:
        text: Shell command line text.

    Returns:
        List of parsed tokens.
    """
    tokens: list[str] = []
    current = ""
    in_single_quote = False
    in_double_quote = False
    i = 0

    while i < len(text):
        char = text[i]

        if char == "\\" and i + 1 < len(text):
            if in_single_quote:
                current += char
            else:
                current += char + text[i + 1]
                i += 1
        elif char == "'" and not in_double_quote:
            if in_single_quote:
                in_single_quote = False
            else:
                in_single_quote = True
            current += char
        elif char == '"' and not in_single_quote:
            if in_double_quote:
                in_double_quote = False
            else:
                in_double_quote = True
            current += char
        elif char in " \t" and not in_single_quote and not in_double_quote:
            if current:
                tokens.append(current)
                current = ""
        else:
            current += char

        i += 1

    if current:
        tokens.append(current)

    return tokens


def _get_longest_common_prefix(strings: list[str]) -> str:
    """Get the longest common prefix of a list of strings.

    Args:
        strings: List of strings to find common prefix.

    Returns:
        Longest common prefix string.
    """
    if not strings:
        return ""
    if len(strings) == 1:
        return strings[0]

    first = strings[0]
    for i, char in enumerate(first):
        for s in strings[1:]:
            if i >= len(s) or s[i] != char:
                return first[:i]
    return first


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
            self._command_cache = _get_system_commands()
        return self._command_cache

    def refresh_cache(self) -> None:
        """Force refresh of command cache."""
        self._command_cache = None

    async def warm_cache(self) -> None:
        """Pre-populate the command cache off the event loop."""
        if self._command_cache is not None:
            return
        with contextlib.suppress(Exception):
            self._command_cache = await asyncio.to_thread(_get_system_commands)

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

        tokens = _parse_shell_tokens(text_before_cursor)
        if not tokens:
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
            common = _get_common_commands()[:MAX_SUGGESTIONS]
            return [(cmd, "command") for cmd in common]

        raw_prefix = _unescape_token(prefix)
        commands = self._get_commands()
        matches = [cmd for cmd in commands if cmd.startswith(raw_prefix.lower())]

        return [(cmd, "command") for cmd in matches[:MAX_SUGGESTIONS]]

    def _get_path_suggestions(self, prefix: str) -> list[tuple[str, str]]:
        """Get file/directory path suggestions.

        Args:
            prefix: Path prefix to match.

        Returns:
            List of (path, type) tuples.
        """
        raw_prefix = _unescape_token(prefix)

        if raw_prefix.startswith("~"):
            raw_prefix = str(Path.home() / raw_prefix[1:])
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

            if len(suggestions) >= MAX_SUGGESTIONS:
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
                        stripped_text, prefix_len = self._strip_prefix(text[:cursor_index])
                        tokens = _parse_shell_tokens(stripped_text)
                        
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
            tokens = _parse_shell_tokens(stripped_text)
            
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

        escaped = _escape_path(label)

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
