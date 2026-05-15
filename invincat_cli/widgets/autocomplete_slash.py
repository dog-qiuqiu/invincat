"""Slash-command completion controller."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from invincat_cli.widgets import autocomplete as _autocomplete
from invincat_cli.widgets.autocomplete import CompletionResult, CompletionView

if TYPE_CHECKING:
    from textual import events


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
        if keywords and len(search) >= _autocomplete._MIN_DESC_SEARCH_LEN:
            for kw in keywords.lower().split():
                if kw.startswith(search) or search in kw:
                    return 120.0
        # Substring match on description (require ≥2 chars to avoid single-letter noise)
        if len(search) >= _autocomplete._MIN_DESC_SEARCH_LEN and search in lower_desc:
            idx = lower_desc.find(search)
            # Word-boundary bonus: match at start of description or after a space
            if idx == 0 or lower_desc[idx - 1] == " ":
                return 110.0
            return 90.0
        # Fuzzy match via SequenceMatcher on name + desc
        name_ratio = SequenceMatcher(None, search, name).ratio()
        desc_ratio = SequenceMatcher(None, search, lower_desc).ratio()
        best = max(name_ratio * 60, desc_ratio * 30)
        return best if best >= _autocomplete._MIN_SLASH_FUZZY_SCORE else 0.0

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
                :_autocomplete.MAX_SUGGESTIONS
            ]
        else:
            # Score and filter commands using fuzzy matching
            scored = [
                (score, cmd, desc)
                for cmd, desc, kw in self._commands
                if (score := self._score_command(search, cmd, desc, kw)) > 0
            ]
            scored.sort(key=lambda x: -x[0])
            suggestions = [(cmd, desc) for _, cmd, desc in scored[:_autocomplete.MAX_SUGGESTIONS]]

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
