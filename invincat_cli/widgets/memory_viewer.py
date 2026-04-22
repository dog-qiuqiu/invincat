"""Read-only memory management viewer modal."""

from __future__ import annotations

import json
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

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.timer import Timer

_MAX_CONTENT_PREVIEW_CHARS = 140
_REFRESH_INTERVAL_SECONDS = 1.5
_ALLOWED_STATUS = frozenset({"active", "archived"})


@dataclass(frozen=True, slots=True)
class MemoryItemView:
    """Single memory item rendered in the viewer."""

    scope: str
    item_id: str
    section: str
    status: str
    content: str
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
        items.append(
            MemoryItemView(
                scope=scope,
                item_id=item_id,
                section=section,
                status=status,
                content=content,
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


class MemoryViewerScreen(ModalScreen[None]):
    """Modal memory viewer with periodic refresh."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("r", "refresh", "Refresh", show=False, priority=True),
        Binding("a", "toggle_archived", "Toggle archived", show=False, priority=True),
        Binding("1", "show_user_scope", "User scope", show=False, priority=True),
        Binding("2", "show_project_scope", "Project scope", show=False, priority=True),
        Binding("tab", "next_scope", "Next scope", show=False, priority=True),
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
        self._refresh_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="memory-title", classes="memory-title")
            yield Static("", id="memory-summary", classes="memory-summary")
            with VerticalScroll(id="memory-list", classes="memory-list"):
                yield Static("Loading memory...")
            yield Static(
                "1 user · 2 project · tab switch · r refresh · a toggle archived · esc close",
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

        title.update(f"Memory Manager · Scope: {self._current_scope}")

        lines: list[str] = []
        view = snapshots.get(self._current_scope)
        if view is None:
            summary.update(
                "Scopes: "
                f"{valid_scopes}/{total_scopes} valid · "
                f"Current scope unavailable: {self._current_scope}"
            )
            lines.append("No memory store configured for current scope.")
        else:
            latest = _iso_to_local(view.latest_updated_at) or "-"
            summary.update(
                "Scopes: "
                f"{valid_scopes}/{total_scopes} valid · "
                f"path: {view.path} · "
                f"total={view.total} active={view.active} archived={view.archived} "
                f"latest={latest}"
            )
            lines.append(
                "[bold #5DADE2]scope[/bold #5DADE2]: "
                f"{escape(view.scope)}"
            )
            lines.append(
                "[bold #5DADE2]path[/bold #5DADE2]: "
                f"{escape(view.path)}"
            )
            if not view.exists:
                lines.append("[bold #F5B041]status[/bold #F5B041]: missing")
            elif not view.valid:
                err = escape(view.error or "unknown error")
                lines.append(f"[bold #EC7063]status[/bold #EC7063]: invalid ({err})")
            else:
                lines.append("[bold #58D68D]status[/bold #58D68D]: ok")
                rendered_items = 0
                sorted_items = sorted(
                    view.items,
                    key=lambda item: (item.section.casefold(), item.item_id),
                )
                for item in sorted_items:
                    if item.status == "archived" and not self._show_archived:
                        continue
                    rendered_items += 1
                    lines.append(
                        "  [bold #AF7AC5]status[/bold #AF7AC5]="
                        f"{escape(item.status)}  "
                        "[bold #AF7AC5]id[/bold #AF7AC5]="
                        f"{escape(item.item_id)}  "
                        "[bold #AF7AC5]section[/bold #AF7AC5]="
                        f"{escape(item.section)}"
                    )
                    lines.append(
                        "    [bold #AF7AC5]content[/bold #AF7AC5]="
                        f"{escape(item.content)}"
                    )
                if rendered_items == 0:
                    lines.append("  - (no visible items)")

        content = "\n".join(lines).strip() if lines else "No memory stores configured."
        children = list(container.children)
        if children and isinstance(children[0], Static):
            children[0].update(_markup_text([content]))

    def action_refresh(self) -> None:
        self._render_snapshot()

    def action_toggle_archived(self) -> None:
        self._show_archived = not self._show_archived
        self._render_snapshot()

    def action_show_user_scope(self) -> None:
        self._current_scope = "user"
        self._render_snapshot()

    def action_show_project_scope(self) -> None:
        self._current_scope = "project"
        self._render_snapshot()

    def action_next_scope(self) -> None:
        ordered_scopes = [
            scope for scope in ("user", "project") if scope in self._memory_store_paths
        ]
        if not ordered_scopes:
            return
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
