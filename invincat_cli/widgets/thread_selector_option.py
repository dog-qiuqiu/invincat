"""Thread row widget for the thread selector."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Static

from invincat_cli.widgets import thread_selector as _thread_selector

if TYPE_CHECKING:
    from collections.abc import Mapping

    from textual.app import ComposeResult
    from textual.events import Click

    from invincat_cli.sessions import ThreadInfo


class ThreadOption(Horizontal):
    """A clickable thread option in the selector."""

    def __init__(
        self,
        thread: ThreadInfo,
        index: int,
        *,
        columns: dict[str, bool],
        column_widths: Mapping[str, int | None],
        selected: bool,
        current: bool,
        relative_time: bool = False,
        cell_text: dict[tuple[str, str], str] | None = None,
        classes: str = "",
    ) -> None:
        """Initialize a thread option row.

        Args:
            thread: Thread metadata for the row.
            index: The index of this option in the filtered list.
            columns: Column visibility settings.
            column_widths: Effective widths for the visible columns.
            selected: Whether the row is highlighted.
            current: Whether the row is the active thread.
            relative_time: Use relative timestamps.
            cell_text: Pre-formatted cell values keyed by `(thread_id, key)`.
            classes: CSS classes for styling.
        """
        super().__init__(classes=classes)
        self.thread = thread
        self.thread_id = thread["thread_id"]
        self.index = index
        self._columns = dict(columns)
        self._column_widths = dict(column_widths)
        self._selected = selected
        self._current = current
        self._relative_time = relative_time
        self._cell_text = cell_text

    class Clicked(Message):
        """Message sent when a thread option is clicked."""

        def __init__(self, thread_id: str, index: int) -> None:
            """Initialize the Clicked message.

            Args:
                thread_id: The thread identifier.
                index: The index of the clicked option.
            """
            super().__init__()
            self.thread_id = thread_id
            self.index = index

    def compose(self) -> ComposeResult:
        """Compose the row cells.

        Yields:
            Static cells for each visible column.
        """
        yield Static(
            self._cursor_text(),
            classes="thread-cell thread-cell-cursor",
            markup=False,
        )
        tid = self.thread_id
        for key in _thread_selector._visible_column_keys(self._columns):
            if self._cell_text is not None and (tid, key) in self._cell_text:
                text = self._cell_text[tid, key]
            else:
                text = _thread_selector._format_column_value(
                    self.thread, key, relative_time=self._relative_time
                )
            cell = Static(
                text,
                classes=f"thread-cell thread-cell-{key}",
                expand=key == "initial_prompt",
                markup=False,
            )
            _thread_selector._apply_column_width(cell, key, self._column_widths)
            yield cell

    def _cursor_text(self) -> str:
        """Return the cursor indicator for the row."""
        return _thread_selector.get_glyphs().cursor if self._selected else ""

    def set_selected(self, selected: bool) -> None:
        """Update row selection styling without rebuilding the row.

        Args:
            selected: Whether the row should be highlighted.
        """
        self._selected = selected
        if selected:
            self.add_class("thread-option-selected")
        else:
            self.remove_class("thread-option-selected")

        try:
            cursor = self.query_one(".thread-cell-cursor", Static)
        except NoMatches:
            return
        cursor.update(self._cursor_text())

    def on_click(self, event: Click) -> None:
        """Handle click on this option.

        Args:
            event: The click event.
        """
        event.stop()
        self.post_message(self.Clicked(self.thread_id, self.index))
