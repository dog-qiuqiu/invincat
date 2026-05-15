"""Chat input widget for deepagents-cli with autocomplete and history support."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static, TextArea

from invincat_cli import theme
from invincat_cli.config import (
    MODE_PREFIXES,
    PREFIX_TO_MODE,
    is_ascii_mode,
)
from invincat_cli.widgets.autocomplete import (
    FuzzyFileController,
    MultiCompletionManager,
    ShellCompletionController,
    SlashCommandController,
)
from invincat_cli.widgets.chat_completion import CompletionOption, CompletionPopup
from invincat_cli.widgets.chat_input_completion import ChatInputCompletionMixin
from invincat_cli.widgets.chat_input_paths import ChatInputPathMixin
from invincat_cli.widgets.chat_input_styles import CHAT_INPUT_CSS
from invincat_cli.widgets.chat_text_area import (
    _BACKSLASH_ENTER_GAP_SECONDS,
    _PASTE_BURST_CHAR_GAP_SECONDS,
    _PASTE_BURST_FLUSH_DELAY_SECONDS,
    ChatTextArea,
)
from invincat_cli.widgets.history import HistoryManager

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from invincat_cli.io.input import MediaTracker

__all__ = [
    "ChatInput",
    "ChatTextArea",
    "CompletionOption",
    "CompletionPopup",
    "_BACKSLASH_ENTER_GAP_SECONDS",
    "_CompletionViewAdapter",
    "_PASTE_BURST_CHAR_GAP_SECONDS",
    "_PASTE_BURST_FLUSH_DELAY_SECONDS",
    "asyncio",
    "time",
]

logger = logging.getLogger(__name__)


def _default_history_path() -> Path:
    """Return the default history file path.

    Extracted as a function so tests can monkeypatch it to a temp path,
    preventing test runs from polluting `~/.invincat/history.jsonl`.
    """
    return Path.home() / ".invincat" / "history.jsonl"


class _CompletionViewAdapter:
    """Translate completion-space replacements to text-area coordinates."""

    def __init__(self, chat_input: ChatInput) -> None:
        """Initialize adapter with its owning `ChatInput`."""
        self._chat_input = chat_input

    def render_completion_suggestions(
        self, suggestions: list[tuple[str, str]], selected_index: int
    ) -> None:
        """Delegate suggestion rendering to `ChatInput`."""
        self._chat_input.render_completion_suggestions(suggestions, selected_index)

    def clear_completion_suggestions(self) -> None:
        """Delegate completion clearing to `ChatInput`."""
        self._chat_input.clear_completion_suggestions()

    def replace_completion_range(self, start: int, end: int, replacement: str) -> None:
        """Map completion indices to text-area indices before replacing text."""
        self._chat_input.replace_completion_range(
            self._chat_input._completion_index_to_text_index(start),
            self._chat_input._completion_index_to_text_index(end),
            replacement,
        )


class ChatInput(ChatInputCompletionMixin, ChatInputPathMixin, Vertical):
    """Chat input widget with prompt, multi-line text, autocomplete, and history.

    Features:
    - Multi-line input with TextArea
    - Enter to submit, modifier key for newlines (see `config.newline_shortcut`)
    - Up/Down arrows for command history at input boundaries (start/end of text)
    - Autocomplete for @ (files) and / (commands)
    """

    DEFAULT_CSS = CHAT_INPUT_CSS
    """Border and prompt glyph change color per mode for immediate visual feedback."""

    class Submitted(Message):
        """Message sent when input is submitted."""

        def __init__(self, value: str, mode: str = "normal") -> None:
            """Initialize with value and mode."""
            super().__init__()
            self.value = value
            self.mode = mode

    class ModeChanged(Message):
        """Message sent when input mode changes."""

        def __init__(self, mode: str) -> None:
            """Initialize with new mode."""
            super().__init__()
            self.mode = mode

    class Typing(Message):
        """Posted when the user presses a printable key or backspace in the input.

        The app uses this to delay approval widgets while the user is actively
        typing, preventing accidental key presses (e.g. `y`, `n`) from
        triggering approval decisions.
        """

    mode: reactive[str] = reactive("normal")

    def __init__(
        self,
        cwd: str | Path | None = None,
        history_file: Path | None = None,
        image_tracker: MediaTracker | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the chat input widget.

        Args:
            cwd: Current working directory for file completion
            history_file: Path to history file (default: ~/.invincat/history.jsonl)
            image_tracker: Optional tracker for attached images
            **kwargs: Additional arguments for parent
        """
        super().__init__(**kwargs)
        self._cwd = Path(cwd) if cwd else Path.cwd()
        self._image_tracker = image_tracker
        self._text_area: ChatTextArea | None = None
        self._popup: CompletionPopup | None = None
        self._completion_manager: MultiCompletionManager | None = None
        self._completion_view: _CompletionViewAdapter | None = None
        self._slash_controller: SlashCommandController | None = None

        # Guard flag: set True before programmatically stripping the mode
        # prefix character so the resulting text-change event does not
        # re-evaluate mode.
        self._stripping_prefix = False

        # When the user submits, we clear the text area which fires a
        # text-change event. Without this guard the tracker would see the
        # now-empty text, assume all media were deleted, and discard them
        # before the app has a chance to send them. Each submit bumps the
        # counter by one; the next text-change event decrements it and
        # skips the sync.
        self._skip_media_sync_events = 0

        # Number of virtual prefix characters currently injected for
        # completion controller calls (0 for normal, 1 for shell/command).
        self._completion_prefix_len = 0

        # Guard flag: set while replacing a dropped path payload with an
        # inline image placeholder so the resulting change event doesn't
        # immediately recurse into the same replacement path.
        self._applying_inline_path_replacement = False

        # Guard flag: set while applying completion so the resulting change
        # event doesn't re-trigger completion suggestions.
        self._applying_completion = False

        # Track current suggestions for click handling
        self._current_suggestions: list[tuple[str, str]] = []
        self._current_selected_index = 0

        # Set up history manager
        if history_file is None:
            history_file = _default_history_path()
        self._history = HistoryManager(history_file)

    def compose(self) -> ComposeResult:  # noqa: PLR6301  # Textual widget method convention
        """Compose the chat input layout.

        Yields:
            Widgets for the input row and completion popup.
        """
        with Horizontal(classes="input-row"):
            yield Static(">", classes="input-prompt", id="prompt")
            yield ChatTextArea(id="chat-input")

        yield CompletionPopup(id="completion-popup")

    def on_mount(self) -> None:
        """Initialize components after mount."""
        if is_ascii_mode():
            colors = theme.get_theme_colors(self)
            self.styles.border = ("ascii", colors.primary)

        self._text_area = self.query_one("#chat-input", ChatTextArea)
        self._popup = self.query_one("#completion-popup", CompletionPopup)

        # Both controllers implement the CompletionController protocol but have
        # different concrete types; the list-item warning is a false positive.
        self._completion_view = _CompletionViewAdapter(self)
        self._file_controller = FuzzyFileController(
            self._completion_view, cwd=self._cwd
        )
        self._slash_controller = SlashCommandController([], self._completion_view)
        self._shell_controller = ShellCompletionController(
            self._completion_view, cwd=self._cwd
        )
        self._completion_manager = MultiCompletionManager(
            [
                self._slash_controller,
                self._file_controller,
            ]  # type: ignore[list-item]  # Controller types are compatible at runtime
        )

        self.run_worker(
            self._file_controller.warm_cache(),
            exclusive=False,
            exit_on_error=False,
        )
        self.run_worker(
            self._shell_controller.warm_cache(),
            exclusive=False,
            exit_on_error=False,
        )
        self._text_area.focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Detect input mode and update completions."""
        text = event.text_area.text
        self._sync_media_tracker_to_text(text)

        # History handlers explicitly decide mode and stripped display text.
        # Skip mode detection here so recalled entries don't inherit stale mode.
        if self._text_area and self._text_area._skip_history_change_events > 0:
            self._text_area._skip_history_change_events -= 1
            if self._completion_manager:
                self._completion_manager.reset()
            self.scroll_visible()
            return
        if self._text_area and self._text_area._skip_history_change_events < 0:
            logger.warning(
                "_skip_history_change_events is negative (%d); resetting to 0",
                self._text_area._skip_history_change_events,
            )
            self._text_area._skip_history_change_events = 0

        if self._applying_inline_path_replacement:
            self._applying_inline_path_replacement = False
        elif self._apply_inline_dropped_path_replacement(text):
            return

        # Skip completion updates when applying a completion
        if self._applying_completion:
            self._applying_completion = False
            return

        # Checked after the guards above so we skip the (potentially slow)
        # filesystem lookup when the text change came from history navigation
        # or prefix stripping, which never need path detection.
        is_path_payload = self._is_dropped_path_payload(text)

        # Guard: skip mode re-detection after we programmatically stripped
        # a prefix character.
        if self._stripping_prefix:
            self._stripping_prefix = False
        elif text and text[0] in PREFIX_TO_MODE:
            if text[0] == "/" and is_path_payload:
                # Absolute dropped paths stay normal input, not slash-command mode.
                if self.mode != "normal":
                    self.mode = "normal"
            else:
                # Detected a mode-trigger prefix (e.g. "!" or "/").
                # Strip it unconditionally -- even when already in the correct
                # mode -- because completion controllers may write replacement
                # text that re-includes the trigger character.  The
                # _stripping_prefix guard prevents the resulting change event
                # from looping back here.
                detected = PREFIX_TO_MODE[text[0]]
                if self.mode != detected:
                    self.mode = detected
                self._strip_mode_prefix()
                # Fall through to update completion suggestions in the same
                # refresh cycle as the mode/glyph change rather than waiting
                # for the next text-change event caused by the prefix strip.
                # Note: the strip's text-change event will also call
                # on_text_changed (idempotently) since _stripping_prefix only
                # skips mode detection, not the completion block below.
        # Update completion suggestions using completion-space text/cursor.
        if self._completion_manager and self._text_area:
            if is_path_payload:
                self._completion_manager.reset()
            else:
                vtext, vcursor = self._completion_text_and_cursor()
                self._completion_manager.on_text_changed(vtext, vcursor)

        # Scroll input into view when content changes (handles text wrap)
        self.scroll_visible()

    def _submit_value(self, value: str) -> None:
        """Prepend mode prefix, save to history, post message, and reset input.

        This is the single path for all submission flows so the prefix-prepend +
        history + post + clear + mode-reset logic stays in one place.

        Args:
            value: The stripped text to submit (without mode prefix).
        """
        if not value:
            return

        if self._completion_manager:
            self._completion_manager.reset()

        value = self._replace_submitted_paths_with_images(value)

        # Prepend mode prefix so the app layer receives the original trigger
        # form (e.g. "!ls", "/help"). The value may already contain the prefix
        # when a completion controller wrote it back into the text area before
        # the strip handler ran.
        prefix = MODE_PREFIXES.get(self.mode, "")
        if prefix and not value.startswith(prefix):
            value = prefix + value

        self._history.add(value)
        self.post_message(self.Submitted(value, self.mode))

        if self._text_area:
            # Preserve submission-time attachments until adapter consumes them.
            self._skip_media_sync_events += 1
            self._text_area.clear_text()
        # Keep shell mode active for consecutive commands; reset other modes
        if self.mode != "shell":
            self.mode = "normal"

    def on_chat_text_area_typing(
        self,
        event: ChatTextArea.Typing,  # noqa: ARG002  # Textual event handler signature
    ) -> None:
        """Relay typing activity to the app as `ChatInput.Typing`."""
        self.post_message(self.Typing())

    def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted) -> None:
        """Handle text submission.

        Always posts the Submitted event - the app layer decides whether to
        process immediately or queue based on agent status.
        """
        self._submit_value(event.value)

    def on_chat_text_area_history_previous(
        self, event: ChatTextArea.HistoryPrevious
    ) -> None:
        """Handle history previous request."""
        entry = self._history.get_previous(event.current_text, query=event.current_text)
        if entry is not None and self._text_area:
            mode, display_text = self._history_entry_mode_and_text(entry)
            self.mode = mode
            self._text_area.set_text_from_history(display_text)
        # No-match path: don't reset the counter — a pending Changed event
        # from a prior set_text_from_history call may still be in flight.
        # Keep text area's _in_history in sync with the history manager.
        if self._text_area:
            self._text_area._in_history = self._history.in_history

    def on_chat_text_area_history_next(
        self,
        event: ChatTextArea.HistoryNext,  # noqa: ARG002  # Textual event handler signature
    ) -> None:
        """Handle history next request."""
        entry = self._history.get_next()
        if entry is not None and self._text_area:
            mode, display_text = self._history_entry_mode_and_text(entry)
            self.mode = mode
            self._text_area.set_text_from_history(display_text)
        # No-match path: don't reset the counter — a pending Changed event
        # from a prior set_text_from_history call may still be in flight.
        # Keep text area's _in_history in sync with the history manager.
        # When the user presses Down past the newest entry, get_next()
        # resets navigation internally, so in_history becomes False.
        if self._text_area:
            self._text_area._in_history = self._history.in_history

    @staticmethod
    def _history_entry_mode_and_text(entry: str) -> tuple[str, str]:
        """Return mode and stripped display text for a history entry.

        Args:
            entry: Raw entry value read from history storage.

        Returns:
            Tuple of `(mode, display_text)` where mode-trigger prefixes are
                removed from `display_text`.
        """
        for prefix, mode in PREFIX_TO_MODE.items():
            # Small dict; loop is fine. No need to over-engineer right now
            if entry.startswith(prefix):
                return mode, entry[len(prefix) :]
        return "normal", entry

    def focus_input(self) -> None:
        """Focus the input field."""
        if self._text_area:
            self._text_area.focus()

    @property
    def value(self) -> str:
        """Get the current input value.

        Returns:
            Current text in the input field.
        """
        if self._text_area:
            return self._text_area.text
        return ""

    @value.setter
    def value(self, val: str) -> None:
        """Set the input value."""
        if self._text_area:
            self._text_area.text = val

    @property
    def input_widget(self) -> ChatTextArea | None:
        """Get the underlying TextArea widget.

        Returns:
            The ChatTextArea widget or None if not mounted.
        """
        return self._text_area

    def set_disabled(self, *, disabled: bool) -> None:
        """Enable or disable the input widget."""
        if self._text_area:
            self._text_area.disabled = disabled
            if disabled:
                self._text_area.blur()
                if self._completion_manager:
                    self._completion_manager.reset()

    def set_cursor_active(self, *, active: bool) -> None:
        """Toggle input focus state (e.g., unfocus while agent is working).

        Args:
            active: Whether the input should be focused and accepting input.
        """
        if self._text_area:
            self._text_area.set_app_focus(has_focus=active)
