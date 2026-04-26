"""Memory management viewer modal with item deletion support."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from rich.markup import escape
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Static

from invincat_cli.i18n import t

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.timer import Timer

_MAX_CONTENT_PREVIEW_CHARS = 140
_REFRESH_INTERVAL_SECONDS = 1.5
_ALLOWED_STATUS = frozenset({"active", "archived"})
_ALLOWED_TIER = frozenset({"hot", "warm", "cold"})
_SORT_MODES: tuple[str, ...] = (
    "score_desc",
    "score_asc",
    "last_scored_desc",
    "last_scored_asc",
)
_TIER_RANK: dict[str, int] = {"hot": 0, "warm": 1, "cold": 2}


@dataclass(frozen=True, slots=True)
class MemoryItemView:
    """Single memory item rendered in the viewer."""

    scope: str
    item_id: str
    section: str
    status: str
    content: str
    tier: str
    score: int
    score_reason: str
    last_scored_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class MemoryScopeView:
    """Snapshot for one memory scope."""

    scope: str
    path: str
    exists: bool
    valid: bool
    error: str | None
    total: int
    active: int
    archived: int
    latest_updated_at: str | None
    items: list[MemoryItemView]


def _iso_to_local(iso_value: str | None) -> str | None:
    if not iso_value:
        return None
    try:
        normalized = iso_value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _trim(text: Any, max_chars: int) -> str:
    if not isinstance(text, str):
        return ""
    normalized = " ".join(text.strip().split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _normalize_tier(value: Any) -> str:
    if isinstance(value, str):
        tier = value.strip().lower()
        if tier in _ALLOWED_TIER:
            return tier
    return "warm"


def _normalize_score(value: Any) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        score = 50
    return max(0, min(100, score))


def _markup_text(lines: list[str]) -> Content:
    return Content.from_markup("\n".join(lines))


def _load_scope_snapshot(scope: str, path: str) -> MemoryScopeView:
    store_path = Path(path)
    if not store_path.exists():
        return MemoryScopeView(
            scope=scope,
            path=str(store_path),
            exists=False,
            valid=False,
            error="store not found",
            total=0,
            active=0,
            archived=0,
            latest_updated_at=None,
            items=[],
        )

    try:
        raw = store_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return MemoryScopeView(
            scope=scope,
            path=str(store_path),
            exists=True,
            valid=False,
            error="store unreadable",
            total=0,
            active=0,
            archived=0,
            latest_updated_at=None,
            items=[],
        )

    items_raw = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items_raw, list):
        return MemoryScopeView(
            scope=scope,
            path=str(store_path),
            exists=True,
            valid=False,
            error="invalid schema: items is not a list",
            total=0,
            active=0,
            archived=0,
            latest_updated_at=None,
            items=[],
        )

    items: list[MemoryItemView] = []
    for raw_item in items_raw:
        if not isinstance(raw_item, dict):
            continue
        item_id = _trim(raw_item.get("id"), max_chars=64)
        section = _trim(raw_item.get("section"), max_chars=80)
        content = _trim(raw_item.get("content"), max_chars=_MAX_CONTENT_PREVIEW_CHARS)
        status = _trim(raw_item.get("status"), max_chars=16).lower()
        if not item_id or not section or not content or status not in _ALLOWED_STATUS:
            continue
        updated_at = _trim(raw_item.get("updated_at"), max_chars=64)
        tier = _normalize_tier(raw_item.get("tier"))
        score = _normalize_score(raw_item.get("score"))
        score_reason = _trim(raw_item.get("score_reason"), max_chars=160)
        last_scored_at = _trim(raw_item.get("last_scored_at"), max_chars=64)
        if not last_scored_at:
            last_scored_at = updated_at
        items.append(
            MemoryItemView(
                scope=scope,
                item_id=item_id,
                section=section,
                status=status,
                content=content,
                tier=tier,
                score=score,
                score_reason=score_reason,
                last_scored_at=last_scored_at,
                updated_at=updated_at,
            )
        )

    active = sum(1 for item in items if item.status == "active")
    archived = sum(1 for item in items if item.status == "archived")
    latest_updated_at = None
    for value in sorted(
        (item.updated_at for item in items if item.updated_at),
        reverse=True,
    ):
        latest_updated_at = value
        break

    return MemoryScopeView(
        scope=scope,
        path=str(store_path),
        exists=True,
        valid=True,
        error=None,
        total=len(items),
        active=active,
        archived=archived,
        latest_updated_at=latest_updated_at,
        items=items,
    )


def load_memory_snapshot(memory_store_paths: dict[str, str]) -> dict[str, MemoryScopeView]:
    """Load user/project memory snapshots for viewer rendering."""
    snapshots: dict[str, MemoryScopeView] = {}
    for scope in ("user", "project"):
        raw_path = memory_store_paths.get(scope)
        if isinstance(raw_path, str) and raw_path.strip():
            snapshots[scope] = _load_scope_snapshot(scope, raw_path)
    return snapshots


def _delete_memory_item(store_path: str, item_id: str) -> None:
    """Remove item with ``item_id`` from the store at ``store_path`` atomically."""
    path = Path(store_path)
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    items = payload.get("items")
    if not isinstance(items, list):
        msg = "invalid store schema: items is not a list"
        raise ValueError(msg)
    payload["items"] = [
        item for item in items
        if not (isinstance(item, dict) and item.get("id") == item_id)
    ]
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_name = tmp.name
    os.replace(tmp_name, path)


def _apply_sort(items: list[MemoryItemView], sort_mode: str) -> list[MemoryItemView]:
    """Sort items keeping active before archived, then apply the chosen sort mode."""
    active = [i for i in items if i.status == "active"]
    archived = [i for i in items if i.status == "archived"]

    def _key_score_asc(i: MemoryItemView) -> tuple:
        return (i.score, _TIER_RANK.get(i.tier, 1), i.section.casefold(), i.item_id)

    def _key_last_scored(i: MemoryItemView) -> tuple:
        return (i.last_scored_at or "", i.section.casefold(), i.item_id)

    def _key_score_desc(i: MemoryItemView) -> tuple:
        return (-i.score, _TIER_RANK.get(i.tier, 1), i.section.casefold(), i.item_id)

    if sort_mode == "score_asc":
        key, reverse = _key_score_asc, False
    elif sort_mode == "last_scored_desc":
        key, reverse = _key_last_scored, True
    elif sort_mode == "last_scored_asc":
        key, reverse = _key_last_scored, False
    else:  # score_desc (default)
        key, reverse = _key_score_desc, False

    return sorted(active, key=key, reverse=reverse) + sorted(archived, key=key, reverse=reverse)


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

    CSS = """
    MemoryViewerScreen {
        align: left top;
    }

    MemoryViewerScreen > Vertical {
        width: 100%;
        height: 100%;
        background: $surface;
        border: none;
        padding: 1 2;
    }

    MemoryViewerScreen .memory-title {
        text-style: bold;
        color: $primary;
        text-align: center;
        margin-bottom: 1;
    }

    MemoryViewerScreen .memory-summary {
        color: $text-muted;
        margin-bottom: 1;
    }

    MemoryViewerScreen .memory-list {
        height: 1fr;
        min-height: 8;
        background: $background;
        scrollbar-gutter: stable;
        padding: 0 1;
    }

    MemoryViewerScreen .memory-help {
        height: 1;
        color: $text-muted;
        text-style: italic;
        margin-top: 1;
        text-align: center;
    }
    """

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
            lines.append(
                f"[bold #5DADE2]{escape(t('memory.viewer.label.scope'))}[/bold #5DADE2]: "
                f"{escape(view.scope)}"
            )
            lines.append(
                f"[bold #5DADE2]{escape(t('memory.viewer.label.path'))}[/bold #5DADE2]: "
                f"{escape(view.path)}"
            )
            if not view.exists:
                lines.append(
                    f"[bold #F5B041]{escape(t('memory.viewer.label.status'))}[/bold #F5B041]: "
                    f"{escape(t('memory.viewer.status.missing'))}"
                )
                self._visible_items = []
            elif not view.valid:
                err = escape(view.error or "unknown error")
                lines.append(
                    f"[bold #EC7063]{escape(t('memory.viewer.label.status'))}[/bold #EC7063]: "
                    f"{escape(t('memory.viewer.status.invalid').format(error=err))}"
                )
                self._visible_items = []
            else:
                lines.append(
                    f"[bold #58D68D]{escape(t('memory.viewer.label.status'))}[/bold #58D68D]: "
                    f"{escape(t('memory.viewer.status.ok'))}"
                )
                sorted_items = _apply_sort(view.items, self._sort_mode)
                visible: list[MemoryItemView] = [
                    item for item in sorted_items
                    if item.status == "active" or self._show_archived
                ]
                self._visible_items = visible
                if self._selected_index >= len(visible):
                    self._selected_index = max(0, len(visible) - 1)

                if not visible:
                    lines.append(f"  - {t('memory.viewer.no_visible_items')}")
                else:
                    for idx, item in enumerate(visible):
                        is_selected = idx == self._selected_index
                        cursor = "▶ " if is_selected else "  "
                        sel_open = "[reverse]" if is_selected else ""
                        sel_close = "[/reverse]" if is_selected else ""
                        lines.append(
                            f"{cursor}{sel_open}"
                            f"[bold #AF7AC5]{escape(t('memory.viewer.label.status'))}[/bold #AF7AC5]="
                            f"{escape(item.status)}  "
                            f"[bold #AF7AC5]{escape(t('memory.viewer.label.id'))}[/bold #AF7AC5]="
                            f"{escape(item.item_id)}  "
                            f"[bold #AF7AC5]{escape(t('memory.viewer.label.section'))}[/bold #AF7AC5]="
                            f"{escape(item.section)}  "
                            f"[bold #AF7AC5]{escape(t('memory.viewer.label.tier'))}[/bold #AF7AC5]="
                            f"{escape(item.tier)}  "
                            f"[bold #AF7AC5]{escape(t('memory.viewer.label.score'))}[/bold #AF7AC5]="
                            f"{item.score}{sel_close}"
                        )
                        lines.append(
                            f"    [bold #AF7AC5]{escape(t('memory.viewer.label.content'))}[/bold #AF7AC5]="
                            f"{escape(item.content)}"
                        )
                        if item.score_reason:
                            lines.append(
                                f"    [bold #AF7AC5]{escape(t('memory.viewer.label.score_reason'))}[/bold #AF7AC5]="
                                f"{escape(item.score_reason)}"
                            )
                        lines.append(
                            f"    [bold #AF7AC5]{escape(t('memory.viewer.label.last_scored_at'))}[/bold #AF7AC5]="
                            f"{escape(item.last_scored_at or '-')}"
                        )

        content = "\n".join(lines).strip() if lines else t("memory.viewer.no_stores_configured")
        children = list(container.children)
        if children and isinstance(children[0], Static):
            children[0].update(_markup_text([content]))

        help_widget = self.query_one(".memory-help", Static)
        if self._status_message:
            help_widget.update(self._status_message)
        elif self._pending_delete_id:
            help_widget.update(
                t("memory.viewer.delete.confirm").format(item_id=self._pending_delete_id)
            )
        else:
            help_widget.update(t("memory.viewer.help"))

    def action_refresh(self) -> None:
        self._render_snapshot()

    def action_toggle_archived(self) -> None:
        self._show_archived = not self._show_archived
        self._render_snapshot()

    def action_cycle_sort(self) -> None:
        idx = _SORT_MODES.index(self._sort_mode) if self._sort_mode in _SORT_MODES else 0
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
            self._selected_index = min(len(self._visible_items) - 1, self._selected_index + 1)
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
                # Adjust selection so it doesn't go out of bounds after delete.
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


__all__ = [
    "MemoryViewerScreen",
    "MemoryScopeView",
    "load_memory_snapshot",
]
