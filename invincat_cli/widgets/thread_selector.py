"""Interactive thread selector screen for /threads command."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from typing import TYPE_CHECKING

from rich.cells import cell_len
from textual.color import Color as TColor
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.fuzzy import Matcher
from textual.screen import ModalScreen
from textual.style import Style as TStyle
from textual.widgets import Checkbox, Input, Static

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from invincat_cli.sessions import ThreadInfo

from invincat_cli import theme
from invincat_cli.config import (
    build_langsmith_thread_url,
    get_glyphs,
    is_ascii_mode,
)
from invincat_cli.i18n import t
from invincat_cli.widgets._links import open_style_link
from invincat_cli.widgets.thread_delete_confirm import DeleteThreadConfirmScreen
from invincat_cli.widgets.thread_selector_actions import ThreadSelectorActionMixin
from invincat_cli.widgets.thread_selector_data import ThreadSelectorDataMixin
from invincat_cli.widgets.thread_selector_layout import ThreadSelectorLayoutMixin
from invincat_cli.widgets.thread_selector_option import ThreadOption
from invincat_cli.widgets.thread_selector_render import ThreadSelectorRenderMixin
from invincat_cli.widgets.thread_selector_style import (
    THREAD_SELECTOR_BINDINGS,
    THREAD_SELECTOR_CSS,
)

__all__ = [
    "DeleteThreadConfirmScreen",
    "Matcher",
    "NoMatches",
    "Checkbox",
    "Content",
    "Horizontal",
    "Input",
    "Static",
    "TColor",
    "TStyle",
    "Vertical",
    "VerticalScroll",
    "get_glyphs",
    "is_ascii_mode",
    "theme",
    "ThreadOption",
    "ThreadSelectorScreen",
    "build_langsmith_thread_url",
    "contextlib",
    "open_style_link",
    "sqlite3",
]

logger = logging.getLogger(__name__)

_URL_FETCH_TIMEOUT = 2.0
"""Seconds to wait for LangSmith thread-URL resolution before giving up."""

_column_widths_cache: (
    tuple[
        tuple[tuple[str, str | None], ...],  # (thread_id, checkpoint_id) fingerprint
        frozenset[str],  # visible column keys
        bool,  # relative_time
        dict[str, int | None],  # computed widths
    ]
    | None
) = None
"""Module-level cache so repeated `/threads` opens skip column-width computation
when the inputs (thread data + config) haven't changed."""

_COL_TID = 10
_COL_AGENT = 12
_COL_MSGS = 5
_COL_BRANCH = 16
_COL_TIMESTAMP = None
_MAX_SEARCH_TEXT_LEN = 200
_COL_PROMPT = None
_AUTO_WIDTH_COLUMNS = {"agent_name", "created_at", "updated_at", "cwd"}
_COLUMN_ORDER = (
    "thread_id",
    "agent_name",
    "messages",
    "created_at",
    "updated_at",
    "git_branch",
    "cwd",
    "initial_prompt",
)
_COLUMN_WIDTHS: dict[str, int | None] = {
    "thread_id": _COL_TID,
    "agent_name": _COL_AGENT,
    "messages": _COL_MSGS,
    "created_at": _COL_TIMESTAMP,
    "updated_at": _COL_TIMESTAMP,
    "git_branch": _COL_BRANCH,
    "cwd": None,
    "initial_prompt": _COL_PROMPT,
}
_COLUMN_LABELS_KEYS = {
    "thread_id": "thread.column_thread_id",
    "agent_name": "thread.column_agent",
    "messages": "thread.column_messages",
    "created_at": "thread.column_created",
    "updated_at": "thread.column_updated",
    "git_branch": "thread.column_branch",
    "cwd": "thread.column_location",
    "initial_prompt": "thread.column_prompt",
}
_COLUMN_TOGGLE_LABELS_KEYS = {
    "thread_id": "thread.column_thread_id",
    "agent_name": "thread.column_agent",
    "messages": "thread.column_messages",
    "created_at": "thread.column_created",
    "updated_at": "thread.column_updated",
    "git_branch": "thread.column_branch",
    "cwd": "thread.column_location",
    "initial_prompt": "thread.column_prompt",
}


def _get_column_labels() -> dict[str, str]:
    """Get translated column labels."""
    return {k: t(v) for k, v in _COLUMN_LABELS_KEYS.items()}


def _get_column_toggle_labels() -> dict[str, str]:
    """Get translated column toggle labels."""
    return {k: t(v) for k, v in _COLUMN_TOGGLE_LABELS_KEYS.items()}


_COLUMN_LABELS = _get_column_labels()
_COLUMN_TOGGLE_LABELS = _get_column_toggle_labels()
# Reserved for future right-aligned columns (e.g., message counts).
_RIGHT_ALIGNED_COLUMNS: set[str] = set()
_SWITCH_ID_PREFIX = "thread-column-"
_SORT_SWITCH_ID = "thread-sort-toggle"
_RELATIVE_TIME_SWITCH_ID = "thread-relative-time"
_CELL_PADDING_RIGHT = 1

_FormatFns = tuple[
    "Callable[[str | None], str]",  # format_path
    "Callable[[str | None], str]",  # format_relative_timestamp
    "Callable[[str | None], str]",  # format_timestamp
]
"""Cached `(format_path, format_relative_timestamp, format_timestamp)`.

Resolved once on first use via `_get_format_fns()` to avoid the overhead of
a per-call deferred import inside the hot `_format_column_value` loop.
"""

_format_fns_cache: _FormatFns | None = None
"""Cached format functions, populated on first call to `_get_format_fns()`."""


def _get_format_fns() -> _FormatFns:
    """Return cached `(format_path, format_relative_timestamp, format_timestamp)`."""
    global _format_fns_cache  # noqa: PLW0603
    if _format_fns_cache is not None:
        return _format_fns_cache
    from invincat_cli.sessions import (
        format_path,
        format_relative_timestamp,
        format_timestamp,
    )

    _format_fns_cache = (format_path, format_relative_timestamp, format_timestamp)
    return _format_fns_cache


def _apply_column_width(
    cell: Static, key: str, column_widths: Mapping[str, int | None]
) -> None:
    """Apply an explicit width to a table cell when one is configured.

    Args:
        cell: The cell widget to size.
        key: Column key for the cell.
        column_widths: Effective column widths for the current table state.
    """
    width = column_widths.get(key)
    if width is not None:
        cell.styles.width = width
        if key in _AUTO_WIDTH_COLUMNS:
            cell.styles.min_width = width


def _active_sort_key(sort_by_updated: bool) -> str:
    """Return the active timestamp field used for sorting."""
    return "updated_at" if sort_by_updated else "created_at"


def _visible_column_keys(columns: dict[str, bool]) -> list[str]:
    """Return visible columns in the on-screen order.

    Args:
        columns: Column visibility settings keyed by column name.

    Returns:
        Visible column keys in display order.
    """
    return [key for key in _COLUMN_ORDER if columns.get(key)]


def _collapse_whitespace(value: str) -> str:
    """Normalize a text value onto a single display line.

    Args:
        value: Raw text to display in a single cell.

    Returns:
        The input text collapsed to a single line.
    """
    return " ".join(value.split())


def _truncate_value(value: str, width: int | None) -> str:
    """Trim text to fit a fixed-width column.

    Args:
        value: Raw cell text.
        width: Maximum column width, or `None` for no truncation.

    Returns:
        The possibly truncated display string.
    """
    if width is None:
        return value

    display = _collapse_whitespace(value)
    display_width = cell_len(display)
    if display_width <= width:
        return display

    glyphs = get_glyphs()
    ellipsis = glyphs.ellipsis
    ellipsis_width = cell_len(ellipsis)
    if width <= ellipsis_width:
        return display[:width]

    result = ""
    result_width = 0
    for char in display:
        char_width = cell_len(char)
        if result_width + char_width + ellipsis_width > width:
            break
        result += char
        result_width += char_width
    return result + ellipsis


def _format_column_value(
    thread: ThreadInfo, key: str, *, relative_time: bool = False
) -> str:
    """Return the display text for one thread column.

    Args:
        thread: Thread metadata for the row.
        key: Column key to format.
        relative_time: Use relative timestamps instead of absolute.

    Returns:
        Formatted display text for the column cell.
    """
    format_path, format_relative_ts, format_ts = _get_format_fns()
    fmt = format_relative_ts if relative_time else format_ts

    value: str
    if key == "thread_id":
        # Strip UUID separators in the compact table preview so truncation
        # never leaves a dangling trailing hyphen in the thread ID column.
        value = thread["thread_id"].replace("-", "")
    elif key == "agent_name":
        value = thread.get("agent_name") or "unknown"
    elif key == "messages":
        raw_count = thread.get("message_count")
        value = str(raw_count) if raw_count is not None else "..."
    elif key == "created_at":
        value = fmt(thread.get("created_at"))
    elif key == "updated_at":
        value = fmt(thread.get("updated_at"))
    elif key == "git_branch":
        value = thread.get("git_branch") or ""
    elif key == "cwd":
        value = format_path(thread.get("cwd"))
    elif key == "initial_prompt":
        value = _collapse_whitespace(thread.get("initial_prompt") or "")
    else:
        value = ""

    return _truncate_value(value, _COLUMN_WIDTHS.get(key))


def _format_header_label(key: str) -> str:
    """Return the rendered header label for a column."""
    return _truncate_value(_COLUMN_LABELS[key], _COLUMN_WIDTHS[key])


def _header_cell_classes(key: str, *, sort_key: str) -> str:
    """Return CSS classes for a header cell.

    Args:
        key: Column key for the header cell.
        sort_key: Currently active sort column.

    Returns:
        Space-delimited classes for the header cell widget.
    """
    classes = f"thread-cell thread-cell-{key}"
    if key == sort_key:
        classes += " thread-cell-sorted"
    return classes


class ThreadSelectorScreen(
    ThreadSelectorActionMixin,
    ThreadSelectorDataMixin,
    ThreadSelectorRenderMixin,
    ThreadSelectorLayoutMixin,
    ModalScreen[str | None]
):
    """Modal dialog for browsing and resuming threads.

    Displays recent threads with keyboard navigation, fuzzy search,
    configurable columns, and delete support.

    Returns a `thread_id` string on selection, or `None` on cancel.
    """

    BINDINGS = THREAD_SELECTOR_BINDINGS
    CSS = THREAD_SELECTOR_CSS

    def __init__(
        self,
        current_thread: str | None = None,
        *,
        thread_limit: int | None = None,
        initial_threads: list[ThreadInfo] | None = None,
    ) -> None:
        """Initialize the `ThreadSelectorScreen`.

        Args:
            current_thread: The currently active thread ID (to highlight).
            thread_limit: Maximum number of rows to fetch when querying DB.
            initial_threads: Optional preloaded rows to render immediately.
        """
        super().__init__()
        self._current_thread = current_thread
        self._thread_limit = thread_limit
        self._threads: list[ThreadInfo] = (
            list(initial_threads) if initial_threads is not None else []
        )
        self._filtered_threads: list[ThreadInfo] = list(self._threads)
        self._has_initial_threads = initial_threads is not None
        self._selected_index = 0
        self._option_widgets: list[ThreadOption] = []
        self._filter_text = ""
        self._confirming_delete = False
        self._render_lock = asyncio.Lock()
        self._filter_input: Input | None = None
        self._filter_controls: list[Input | Checkbox] | None = None
        self._cell_text: dict[tuple[str, str], str] = {}

        from invincat_cli.model_config import load_thread_config

        cfg = load_thread_config()
        self._columns = dict(cfg.columns)
        self._relative_time = cfg.relative_time
        self._sort_by_updated = cfg.sort_order == "updated_at"

        # Cached threads are pre-sorted by updated_at DESC (the only sort
        # order the cache stores).  Skip the O(n log n) re-sort when that
        # matches the user's preference.
        if not (self._has_initial_threads and self._sort_by_updated):
            self._apply_sort()
        self._sync_selected_index()
        self._column_widths = self._compute_column_widths()
