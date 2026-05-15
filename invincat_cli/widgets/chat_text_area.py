"""Custom text area behavior for chat input."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea

from invincat_cli.io.input import IMAGE_PLACEHOLDER_PATTERN, VIDEO_PLACEHOLDER_PATTERN

if TYPE_CHECKING:
    from textual import events
    from textual.timer import Timer


_PASTE_BURST_CHAR_GAP_SECONDS = 0.03
"""Maximum time between chars to treat input as a paste-like burst."""

_PASTE_BURST_FLUSH_DELAY_SECONDS = 0.08
"""Idle timeout before flushing buffered burst text."""

_PASTE_BURST_START_CHARS = {"'", '"'}
"""Characters that can start dropped-path payloads."""

_BACKSLASH_ENTER_GAP_SECONDS = 0.15
"""Maximum gap between a `\\` key and a following `enter` key to treat the
pair as a terminal-emitted shift+enter sequence.

Some terminals (e.g. VSCode's built-in terminal) send a literal backslash
followed by enter when the user presses shift+enter.  The gap is
generous (150 ms) because the terminal emits both characters nearly
simultaneously; a human deliberately typing `\\` then pressing Enter would
have a much larger gap."""

class ChatTextArea(TextArea):
    """TextArea subclass with custom key handling for chat input."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding(
            "shift+enter,alt+enter,ctrl+enter,ctrl+j",
            "insert_newline",
            "New Line",
            show=False,
            priority=True,
        ),
    ]
    """Key bindings for the chat text area.

    These are the single source of truth for shortcut keys. `_NEWLINE_KEYS`
    is derived from this list so that `_on_key` stays in sync automatically.
    """

    _NEWLINE_KEYS: ClassVar[frozenset[str]] = frozenset(
        key
        for b in BINDINGS
        if b.action == "insert_newline"
        for key in b.key.split(",")
    )
    """Flattened set of keys that insert a newline, derived from `BINDINGS`."""

    _skip_history_change_events: int
    """Counter incremented before a history-driven text replacement so the
    resulting `TextArea.Changed` event (which fires on the next message-loop
    iteration) can be suppressed.  `ChatInput.on_text_area_changed` decrements
    the counter.
    """

    _in_history: bool
    """Persistent flag that stays `True` while the user is browsing history.

    Relaxes cursor-boundary checks so Up/Down work from either end of
    the text.

    Reset to `False` when navigating past the newest entry, submitting,
    or clearing.
    """

    class Submitted(Message):
        """Message sent when text is submitted."""

        def __init__(self, value: str) -> None:
            """Initialize with submitted value."""
            self.value = value
            super().__init__()

    class HistoryPrevious(Message):
        """Request previous history entry."""

        def __init__(self, current_text: str) -> None:
            """Initialize with current text for saving."""
            self.current_text = current_text
            super().__init__()

    class HistoryNext(Message):
        """Request next history entry."""

    class PastedPaths(Message):
        """Message sent when paste payload resolves to file paths."""

        def __init__(self, raw_text: str, paths: list[Path]) -> None:
            """Initialize with raw pasted text and parsed file paths."""
            self.raw_text = raw_text
            self.paths = paths
            super().__init__()

    class Typing(Message):
        """Posted when the user presses a printable key or backspace.

        Relayed by `ChatInput` as `ChatInput.Typing` for the app to track
        typing activity.
        """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the chat text area."""
        # Remove placeholder if passed, TextArea doesn't support it the same way
        kwargs.pop("placeholder", None)
        super().__init__(**kwargs)
        self._skip_history_change_events = 0
        self._in_history = False
        self._completion_active = False
        # Buffer quote-prefixed high-frequency key bursts from terminals that
        # emulate paste via rapid key events instead of dispatching a paste
        # event.
        self._paste_burst_buffer = ""
        self._paste_burst_last_char_time: float | None = None
        self._paste_burst_timer: Timer | None = None
        # See _BACKSLASH_ENTER_GAP_SECONDS for context.
        self._backslash_pending_time: float | None = None

    def set_app_focus(self, *, has_focus: bool) -> None:
        """Set whether the app should show the cursor as active.

        Args:
            has_focus: Whether the app input should be focused.
        """
        self._backslash_pending_time = None
        if has_focus and not self.has_focus:
            self.call_after_refresh(self.focus)

    def set_completion_active(self, *, active: bool) -> None:
        """Set whether completion suggestions are visible."""
        self._completion_active = active

    def action_insert_newline(self) -> None:
        """Insert a newline character."""
        self.insert("\n")

    def _cancel_paste_burst_timer(self) -> None:
        """Cancel any scheduled paste-burst flush timer."""
        if self._paste_burst_timer is None:
            return
        self._paste_burst_timer.stop()
        self._paste_burst_timer = None

    def _schedule_paste_burst_flush(self) -> None:
        """Schedule idle-time flush for buffered paste-burst text."""
        self._cancel_paste_burst_timer()
        self._paste_burst_timer = self.set_timer(
            _PASTE_BURST_FLUSH_DELAY_SECONDS, self._flush_paste_burst
        )

    def _start_paste_burst(self, char: str, now: float) -> None:
        """Start buffering a paste-like keystroke burst."""
        self._paste_burst_buffer = char
        self._paste_burst_last_char_time = now
        self._schedule_paste_burst_flush()

    def _append_paste_burst(self, text: str, now: float) -> None:
        """Append text to an active paste-burst buffer."""
        if not self._paste_burst_buffer:
            self._start_paste_burst(text, now)
            return
        self._paste_burst_buffer += text
        self._paste_burst_last_char_time = now
        self._schedule_paste_burst_flush()

    def _should_start_paste_burst(self, char: str) -> bool:
        """Return whether a keypress should start paste-burst buffering.

        Restricting to quote-prefixed input at an empty cursor reduces false
        positives for normal typing and slash-command entry.
        """
        if char not in _PASTE_BURST_START_CHARS:
            return False
        if self.text or not self.selection.is_empty:
            return False
        row, col = self.cursor_location
        return row == 0 and col == 0

    async def _flush_paste_burst(self) -> None:
        """Flush buffered burst text through dropped-path parsing.

        When parsing fails, the buffered text is inserted unchanged so regular
        typing behavior is preserved.
        """
        payload = self._paste_burst_buffer
        self._paste_burst_buffer = ""
        self._paste_burst_last_char_time = None
        self._cancel_paste_burst_timer()
        if not payload:
            return

        from invincat_cli.io.input import parse_pasted_path_payload

        try:
            parsed = await asyncio.to_thread(parse_pasted_path_payload, payload)
        except Exception:  # noqa: BLE001  # Treat thread failure as non-path text
            parsed = None
        if parsed is not None:
            self.post_message(self.PastedPaths(payload, parsed.paths))
            return

        self.insert(payload)

    def _delete_preceding_backslash(self) -> bool:
        """Delete the backslash character immediately before the cursor.

        Caller must ensure a backslash is expected at this position. The
        method verifies the character before deleting it.

        Returns:
            `True` if a backslash was found and deleted, `False` otherwise.
        """
        row, col = self.cursor_location
        if col > 0:
            start = (row, col - 1)
            if self.document.get_text_range(start, self.cursor_location) == "\\":
                self.delete(start, self.cursor_location)
                return True
        elif row > 0:
            prev_line = self.document.get_line(row - 1)
            start = (row - 1, len(prev_line) - 1)
            end = (row - 1, len(prev_line))
            if self.document.get_text_range(start, end) == "\\":
                self.delete(start, self.cursor_location)
                return True
        return False

    async def _on_key(self, event: events.Key) -> None:
        """Handle key events."""
        # VS Code 1.110 incorrectly sends space as a CSI u escape code
        # (`\x1b[32u`) instead of a plain ` ` character.  Textual parses
        # this as Key(key='space', character=None, is_printable=False), so
        # the TextArea never inserts the space.  Per the kitty keyboard
        # protocol spec, keys that generate text (like space) should NOT
        # use CSI u encoding — VS Code is the outlier here.
        #
        # This workaround should be safe to keep indefinitely: once VS Code or
        # Textual fixes the issue upstream, `character` will be `' '` and
        # this branch simply won't match.
        #
        # Upstream: https://github.com/Textualize/textual/issues/6408
        if event.key == "space" and event.character is None:
            event.prevent_default()
            event.stop()
            self.insert(" ")
            self.post_message(self.Typing())
            return

        now = time.monotonic()

        # Signal typing activity for printable keys and backspace so the app
        # can defer approval widgets while the user is actively editing.
        if event.is_printable or event.key == "backspace":
            self.post_message(self.Typing())

        if self._paste_burst_buffer:
            if event.key == "enter":
                self._append_paste_burst("\n", now)
                event.prevent_default()
                event.stop()
                return

            if event.is_printable and event.character is not None:
                last_time = self._paste_burst_last_char_time
                if (
                    last_time is not None
                    and (now - last_time) <= _PASTE_BURST_CHAR_GAP_SECONDS
                ):
                    self._append_paste_burst(event.character, now)
                    event.prevent_default()
                    event.stop()
                    return

            await self._flush_paste_burst()

        if (
            event.is_printable
            and event.character is not None
            and self._should_start_paste_burst(event.character)
        ):
            self._start_paste_burst(event.character, now)
            event.prevent_default()
            event.stop()
            return

        # Some terminals (e.g. VSCode built-in) send a literal backslash
        # followed by enter for shift+enter.  When enter arrives shortly
        # after a backslash, delete the backslash and insert a newline.
        if (
            event.key == "enter"
            and not self._completion_active
            and self._backslash_pending_time is not None
            and (now - self._backslash_pending_time) <= _BACKSLASH_ENTER_GAP_SECONDS
        ):
            self._backslash_pending_time = None
            if self._delete_preceding_backslash():
                event.prevent_default()
                event.stop()
                self.insert("\n")
                return
        self._backslash_pending_time = None

        if event.key == "backslash" and event.character == "\\":
            self._backslash_pending_time = now

        # Modifier+Enter inserts newline — keys derived from BINDINGS
        if event.key in self._NEWLINE_KEYS:
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return

        if event.key == "backspace" and self._delete_image_placeholder(backwards=True):
            event.prevent_default()
            event.stop()
            return

        if event.key == "delete" and self._delete_image_placeholder(backwards=False):
            event.prevent_default()
            event.stop()
            return

        # If completion is active, let parent handle navigation keys
        if self._completion_active and event.key in {"up", "down", "tab", "enter"}:
            # Prevent TextArea's default behavior (e.g., Enter inserting newline)
            # but let event bubble to ChatInput for completion handling
            event.prevent_default()
            return

        # Plain Enter submits
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            value = self.text.strip()
            if value:
                self.post_message(self.Submitted(value))
            return

        # Up/Down arrow: only navigate history at input boundaries.
        # Up requires cursor at position (0, 0); Down requires cursor at
        # the very end.  When already browsing history, either boundary
        # allows navigation in both directions.
        if event.key in {"up", "down"}:
            row, col = self.cursor_location
            text = self.text
            lines = text.split("\n")
            last_row = len(lines) - 1
            at_start = row == 0 and col == 0
            at_end = row == last_row and col == len(lines[last_row])
            navigate = (
                event.key == "up" and (at_start or (self._in_history and at_end))
            ) or (event.key == "down" and (at_end or (self._in_history and at_start)))

            if navigate:
                event.prevent_default()
                event.stop()
                if event.key == "up":
                    self.post_message(self.HistoryPrevious(self.text))
                else:
                    self.post_message(self.HistoryNext())
                return

        await super()._on_key(event)

    def _delete_image_placeholder(self, *, backwards: bool) -> bool:
        """Delete a full image placeholder token in one keypress.

        Args:
            backwards: Whether the delete action is backwards (`backspace`) or
                forwards (`delete`).

        Returns:
            `True` when a placeholder token was deleted.
        """
        if not self.text or not self.selection.is_empty:
            return False

        cursor_offset = self.document.get_index_from_location(self.cursor_location)  # type: ignore[attr-defined]  # Document has this method; DocumentBase stub is narrower
        span = self._find_image_placeholder_span(cursor_offset, backwards=backwards)
        if span is None:
            return False

        start, end = span
        start_location = self.document.get_location_from_index(start)  # type: ignore[attr-defined]  # Document has this method; DocumentBase stub is narrower
        end_location = self.document.get_location_from_index(end)  # type: ignore[attr-defined]
        self.delete(start_location, end_location)
        self.move_cursor(start_location)
        return True

    def _find_image_placeholder_span(
        self, cursor_offset: int, *, backwards: bool
    ) -> tuple[int, int] | None:
        """Return placeholder span to delete for current cursor and key direction.

        Args:
            cursor_offset: Character offset of the cursor from the start of text.
            backwards: Whether the delete action is backwards (backspace) or
                forwards (delete).
        """
        text = self.text
        # Check both image and video placeholders
        for pattern in (IMAGE_PLACEHOLDER_PATTERN, VIDEO_PLACEHOLDER_PATTERN):
            for match in pattern.finditer(text):
                start, end = match.span()
                if backwards:
                    # Cursor is inside token or right after a trailing space inserted
                    # with the token.
                    if start < cursor_offset <= end:
                        return start, end
                    if cursor_offset > 0:
                        previous_index = cursor_offset - 1
                        if (
                            previous_index < len(text)
                            and previous_index == end
                            and text[previous_index].isspace()
                        ):
                            return start, cursor_offset
                elif start <= cursor_offset < end:
                    return start, end
        return None

    async def _on_paste(self, event: events.Paste) -> None:
        """Handle paste events and detect dragged file paths."""
        self._backslash_pending_time = None
        if self._paste_burst_buffer:
            await self._flush_paste_burst()

        from invincat_cli.io.input import parse_pasted_path_payload

        try:
            parsed = await asyncio.to_thread(parse_pasted_path_payload, event.text)
        except Exception:  # noqa: BLE001  # Treat thread failure as non-path text
            parsed = None
        if parsed is None:
            # Don't call super() here — Textual's MRO dispatch already calls
            # TextArea._on_paste after this handler returns. Calling super()
            # would insert the text a second time, duplicating the paste.
            return

        event.prevent_default()
        event.stop()
        self.post_message(self.PastedPaths(event.text, parsed.paths))

    def set_text_from_history(self, text: str) -> None:
        """Set text from history navigation."""
        self._paste_burst_buffer = ""
        self._paste_burst_last_char_time = None
        self._cancel_paste_burst_timer()
        self._backslash_pending_time = None
        self._skip_history_change_events += 1
        self.text = text
        # Move cursor to end
        lines = text.split("\n")
        last_row = len(lines) - 1
        last_col = len(lines[last_row])
        self.move_cursor((last_row, last_col))

    def clear_text(self) -> None:
        """Clear the text area."""
        self._in_history = False
        # Increment (not reset) so any pending Changed event from a prior
        # set_text_from_history is still suppressed, plus one for the
        # self.text = "" assignment below.
        self._skip_history_change_events += 1
        self._paste_burst_buffer = ""
        self._paste_burst_last_char_time = None
        self._cancel_paste_burst_timer()
        self._backslash_pending_time = None
        self.text = ""
        self.move_cursor((0, 0))
