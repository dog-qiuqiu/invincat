"""Memory management viewer modal with item deletion support."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from rich.markup import escape
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Static

from invincat_cli.i18n import t
from invincat_cli.widgets.memory_viewer_models import MemoryItemView, MemoryScopeView
from invincat_cli.widgets.memory_viewer_sort import SORT_MODES, apply_sort
from invincat_cli.widgets.memory_viewer_store import (
    MAX_CONTENT_PREVIEW_CHARS,
    delete_memory_item,
    iso_to_local,
    load_memory_snapshot,
    normalize_score,
    normalize_tier,
    trim,
)
from invincat_cli.widgets.memory_viewer_styles import MEMORY_VIEWER_CSS

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.timer import Timer

_MAX_CONTENT_PREVIEW_CHARS = MAX_CONTENT_PREVIEW_CHARS
_REFRESH_INTERVAL_SECONDS = 1.5
_SORT_MODES = SORT_MODES
_iso_to_local = iso_to_local
_trim = trim
_normalize_tier = normalize_tier
_normalize_score = normalize_score
_delete_memory_item = delete_memory_item
_apply_sort = apply_sort

_STATUS_COLORS: dict[str, str] = {"active": "#58D68D", "archived": "#EC7063"}


def _markup_text(lines: list[str]) -> Content:
    return Content.from_markup("\n".join(lines))


def _format_item_status(status: str) -> str:
    color = _STATUS_COLORS.get(status, "#F5B041")
    return f"[bold {color}]{escape(status)}[/bold {color}]"


class MemoryViewerScreen(ModalScreen[None]):
    """Modal memory viewer with periodic refresh."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False, priority=True),
        Binding("down", "move_down", "Down", show=False, priority=True),
        Binding("r", "refresh", "Refresh", show=False, priority=True),
        Binding("a", "toggle_archived", "Toggle archived", show=False, priority=True),
        Binding("s", "cycle_sort", "Cycle sort", show=False, priority=True),
        Binding("1", "show_user_scope", "User scope", show=False, priority=True),
        Binding("2", "show_project_scope", "Project scope", show=False, priority=True),
        Binding("tab", "next_scope", "Next scope", show=False, priority=True),
        Binding("d", "delete_item", "Delete", show=False, priority=True),
        Binding("escape", "cancel", "Close", show=False, priority=True),
    ]

    CSS = MEMORY_VIEWER_CSS

    def __init__(self, *, memory_store_paths: dict[str, str]) -> None:
        super().__init__()
        self._memory_store_paths = dict(memory_store_paths)
        self._show_archived = True
        self._current_scope = "user"
        self._sort_mode: str = _SORT_MODES[0]
        self._refresh_timer: Timer | None = None
        self._selected_index: int = 0
        self._visible_items: list[MemoryItemView] = []
        self._pending_delete_id: str | None = None
        self._status_message: str = ""

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="memory-title", classes="memory-title")
            yield Static("", id="memory-summary", classes="memory-summary")
            with VerticalScroll(id="memory-list", classes="memory-list"):
                yield Static(t("memory.viewer.loading"))
            yield Static(
                t("memory.viewer.help"),
                classes="memory-help",
            )

    def on_mount(self) -> None:
        self._render_snapshot()
        self._refresh_timer = self.set_interval(
            _REFRESH_INTERVAL_SECONDS,
            self._render_snapshot,
        )

    def on_unmount(self) -> None:
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None

    def _render_snapshot(self) -> None:
        snapshots = load_memory_snapshot(self._memory_store_paths)
        title = self.query_one("#memory-title", Static)
        summary = self.query_one("#memory-summary", Static)
        container = self.query_one("#memory-list", VerticalScroll)

        total_scopes = len(snapshots)
        valid_scopes = sum(1 for view in snapshots.values() if view.valid)
        ordered_scopes = [scope for scope in ("user", "project") if scope in snapshots]
        if not ordered_scopes:
            self._current_scope = "user"
        elif self._current_scope not in ordered_scopes:
            self._current_scope = ordered_scopes[0]

        sort_label = t(f"memory.viewer.sort.{self._sort_mode}")
        title.update(
            t("memory.viewer.title").format(scope=self._current_scope, sort=sort_label)
        )

        lines: list[str] = []
        view = snapshots.get(self._current_scope)
        if view is None:
            summary.update(
                t("memory.viewer.summary_unavailable").format(
                    valid=valid_scopes,
                    total=total_scopes,
                    scope=self._current_scope,
                )
            )
            lines.append(t("memory.viewer.no_scope_configured"))
            self._visible_items = []
        else:
            self._append_scope_lines(summary, lines, view, valid_scopes, total_scopes)

        content = (
            "\n".join(lines).strip()
            if lines
            else t("memory.viewer.no_stores_configured")
        )
        children = list(container.children)
        if children and isinstance(children[0], Static):
            children[0].update(_markup_text([content]))
        self._update_help()

    def _append_scope_lines(
        self,
        summary: Static,
        lines: list[str],
        view: MemoryScopeView,
        valid_scopes: int,
        total_scopes: int,
    ) -> None:
        latest = _iso_to_local(view.latest_updated_at) or "-"
        summary.update(
            t("memory.viewer.summary").format(
                valid=valid_scopes,
                total=total_scopes,
                path=view.path,
                items_total=view.total,
                active=view.active,
                archived=view.archived,
                latest=latest,
            )
        )
        lines.extend(
            [
                _label_line("scope", view.scope, "#5DADE2"),
                _label_line("path", view.path, "#5DADE2"),
            ]
        )
        if not view.exists:
            lines.append(
                _label_line("status", t("memory.viewer.status.missing"), "#F5B041")
            )
            self._visible_items = []
        elif not view.valid:
            err = escape(view.error or "unknown error")
            lines.append(
                _label_line(
                    "status",
                    t("memory.viewer.status.invalid").format(error=err),
                    "#EC7063",
                )
            )
            self._visible_items = []
        else:
            lines.append(_label_line("status", t("memory.viewer.status.ok"), "#58D68D"))
            self._append_item_lines(lines, view)

    def _append_item_lines(self, lines: list[str], view: MemoryScopeView) -> None:
        sorted_items = _apply_sort(view.items, self._sort_mode)
        visible = [
            item
            for item in sorted_items
            if item.status == "active" or self._show_archived
        ]
        self._visible_items = visible
        if self._selected_index >= len(visible):
            self._selected_index = max(0, len(visible) - 1)
        if not visible:
            lines.append(f"  - {t('memory.viewer.no_visible_items')}")
            return
        for idx, item in enumerate(visible):
            self._append_one_item(lines, item, selected=idx == self._selected_index)

    def _append_one_item(
        self, lines: list[str], item: MemoryItemView, *, selected: bool
    ) -> None:
        cursor = "▶ " if selected else "  "
        sel_open = "[reverse]" if selected else ""
        sel_close = "[/reverse]" if selected else ""
        lines.append(
            f"{cursor}{sel_open}"
            f"{_inline_label('status')}={_format_item_status(item.status)}  "
            f"{_inline_label('id')}={escape(item.item_id)}  "
            f"{_inline_label('section')}={escape(item.section)}  "
            f"{_inline_label('tier')}={escape(item.tier)}  "
            f"{_inline_label('score')}={item.score}{sel_close}"
        )
        lines.append(f"    {_inline_label('content')}={escape(item.content)}")
        if item.reason:
            lines.append(f"    {_inline_label('reason')}={escape(item.reason)}")
        lines.append(
            f"    {_inline_label('last_scored_at')}={escape(item.last_scored_at or '-')}"
        )

    def _update_help(self) -> None:
        help_widget = self.query_one(".memory-help", Static)
        if self._status_message:
            help_widget.update(self._status_message)
        elif self._pending_delete_id:
            help_widget.update(
                t("memory.viewer.delete.confirm").format(
                    item_id=escape(self._pending_delete_id)
                )
            )
        else:
            help_widget.update(t("memory.viewer.help"))

    def action_refresh(self) -> None:
        self._render_snapshot()

    def action_toggle_archived(self) -> None:
        self._show_archived = not self._show_archived
        self._render_snapshot()

    def action_cycle_sort(self) -> None:
        idx = (
            _SORT_MODES.index(self._sort_mode) if self._sort_mode in _SORT_MODES else 0
        )
        self._sort_mode = _SORT_MODES[(idx + 1) % len(_SORT_MODES)]
        self._render_snapshot()

    def action_move_up(self) -> None:
        self._pending_delete_id = None
        self._status_message = ""
        if self._visible_items:
            self._selected_index = max(0, self._selected_index - 1)
        self._render_snapshot()

    def action_move_down(self) -> None:
        self._pending_delete_id = None
        self._status_message = ""
        if self._visible_items:
            self._selected_index = min(
                len(self._visible_items) - 1, self._selected_index + 1
            )
        self._render_snapshot()

    def action_delete_item(self) -> None:
        if not self._visible_items:
            self._status_message = t("memory.viewer.delete.no_selection")
            self._render_snapshot()
            return

        item = self._visible_items[self._selected_index]
        if self._pending_delete_id == item.item_id:
            store_path = self._memory_store_paths.get(item.scope, "")
            try:
                _delete_memory_item(store_path, item.item_id)
                self._status_message = t("memory.viewer.delete.success").format(
                    item_id=item.item_id
                )
                self._selected_index = max(0, self._selected_index - 1)
            except Exception as exc:  # noqa: BLE001
                self._status_message = t("memory.viewer.delete.error").format(error=exc)
            finally:
                self._pending_delete_id = None
        else:
            self._pending_delete_id = item.item_id
            self._status_message = ""
        self._render_snapshot()

    def action_show_user_scope(self) -> None:
        self._pending_delete_id = None
        self._status_message = ""
        self._current_scope = "user"
        self._render_snapshot()

    def action_show_project_scope(self) -> None:
        self._pending_delete_id = None
        self._status_message = ""
        self._current_scope = "project"
        self._render_snapshot()

    def action_next_scope(self) -> None:
        ordered_scopes = [
            scope for scope in ("user", "project") if scope in self._memory_store_paths
        ]
        if not ordered_scopes:
            return
        self._pending_delete_id = None
        self._status_message = ""
        if self._current_scope not in ordered_scopes:
            self._current_scope = ordered_scopes[0]
        else:
            idx = ordered_scopes.index(self._current_scope)
            self._current_scope = ordered_scopes[(idx + 1) % len(ordered_scopes)]
        self._render_snapshot()

    def action_cancel(self) -> None:
        self.dismiss(None)


def _inline_label(label_key: str) -> str:
    return f"[bold #AF7AC5]{escape(t(f'memory.viewer.label.{label_key}'))}[/bold #AF7AC5]"


def _label_line(label_key: str, value: str, color: str) -> str:
    label = escape(t(f"memory.viewer.label.{label_key}"))
    return f"[bold {color}]{label}[/bold {color}]: {escape(value)}"


__all__ = [
    "MemoryItemView",
    "MemoryScopeView",
    "MemoryViewerScreen",
    "_apply_sort",
    "_delete_memory_item",
    "_format_item_status",
    "_iso_to_local",
    "_normalize_score",
    "_normalize_tier",
    "_trim",
    "load_memory_snapshot",
]
