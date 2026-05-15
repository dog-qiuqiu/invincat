from __future__ import annotations

import json
from pathlib import Path

import pytest

from invincat_cli.app import DeepAgentsApp
from invincat_cli.commands.registry import IMMEDIATE_UI, SLASH_COMMANDS
from invincat_cli.config import settings
from invincat_cli.widgets import memory_viewer as memory_viewer_mod
from invincat_cli.widgets.memory_viewer import (
    MemoryItemView,
    MemoryViewerScreen,
    _apply_sort,
    _delete_memory_item,
    _format_item_status,
    _iso_to_local,
    _normalize_score,
    _normalize_tier,
    _trim,
    load_memory_snapshot,
)


def _write_store(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_memory_command_registered_for_immediate_ui() -> None:
    assert "/memory" in IMMEDIATE_UI
    names = {entry[0] for entry in SLASH_COMMANDS}
    assert "/memory" in names


def test_memory_viewer_project_store_falls_back_to_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "project_root", None)
    app = DeepAgentsApp(
        agent=None,
        assistant_id="agent",
        backend=None,
        cwd=tmp_path,
    )

    paths = app._resolve_memory_store_paths()

    assert paths["project"] == str(
        (tmp_path / ".invincat" / "memory_project.json").resolve()
    )


def test_load_memory_snapshot_reads_valid_store(tmp_path: Path) -> None:
    user_store = tmp_path / "memory_user.json"
    _write_store(
        user_store,
        {
            "version": 1,
            "scope": "user",
            "items": [
                {
                    "id": "mem_u_000001",
                    "section": "User Preferences",
                    "content": "Prefer concise Chinese answers.",
                    "status": "active",
                    "tier": "hot",
                    "score": 91,
                    "reason": "Stable preference",
                    "last_scored_at": "2026-04-22T10:05:00Z",
                    "updated_at": "2026-04-22T10:00:00Z",
                },
                {
                    "id": "mem_u_000002",
                    "section": "User Preferences",
                    "content": "Use short code comments.",
                    "status": "archived",
                    "updated_at": "2026-04-22T11:00:00Z",
                },
            ],
        },
    )

    snapshot = load_memory_snapshot({"user": str(user_store)})
    user = snapshot["user"]
    assert user.valid is True
    assert user.exists is True
    assert user.total == 2
    assert user.active == 1
    assert user.archived == 1
    assert user.latest_updated_at == "2026-04-22T11:00:00Z"
    assert user.items[0].tier in {"hot", "warm", "cold"}
    assert isinstance(user.items[0].score, int)
    assert user.items[0].reason == "Stable preference"


def test_load_memory_snapshot_reads_legacy_score_reason(tmp_path: Path) -> None:
    user_store = tmp_path / "memory_user.json"
    _write_store(
        user_store,
        {
            "version": 1,
            "scope": "user",
            "items": [
                {
                    "id": "mem_u_000001",
                    "section": "User Preferences",
                    "content": "Prefer concise Chinese answers.",
                    "status": "active",
                    "score_reason": "Legacy preference rationale",
                }
            ],
        },
    )

    snapshot = load_memory_snapshot({"user": str(user_store)})
    assert snapshot["user"].items[0].reason == "Legacy preference rationale"


def test_memory_viewer_status_colors() -> None:
    assert _format_item_status("active") == "[bold #58D68D]active[/bold #58D68D]"
    assert _format_item_status("archived") == "[bold #EC7063]archived[/bold #EC7063]"


def test_load_memory_snapshot_marks_invalid_schema(tmp_path: Path) -> None:
    project_store = tmp_path / "memory_project.json"
    _write_store(project_store, {"version": 1, "scope": "project", "items": {}})

    snapshot = load_memory_snapshot({"project": str(project_store)})
    project = snapshot["project"]
    assert project.exists is True
    assert project.valid is False
    assert project.error == "invalid schema: items is not a list"


def test_load_memory_snapshot_missing_unreadable_and_invalid_items(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.json"
    assert (
        load_memory_snapshot({"user": str(missing)})["user"].error == "store not found"
    )

    unreadable = tmp_path / "bad.json"
    unreadable.write_text("{bad", encoding="utf-8")
    assert (
        load_memory_snapshot({"project": str(unreadable)})["project"].error
        == "store unreadable"
    )

    store = tmp_path / "memory_user.json"
    _write_store(
        store,
        {
            "items": [
                "not a dict",
                {"id": "missing-content", "section": "s", "status": "active"},
                {
                    "id": "valid",
                    "section": "s",
                    "content": "kept",
                    "status": "active",
                },
            ]
        },
    )
    snapshot = load_memory_snapshot({"user": str(store)})["user"]
    assert [item.item_id for item in snapshot.items] == ["valid"]


def test_memory_viewer_next_scope_cycles_between_user_and_project() -> None:
    screen = MemoryViewerScreen(
        memory_store_paths={
            "user": "/tmp/memory_user.json",
            "project": "/tmp/memory_project.json",
        }
    )
    screen._current_scope = "user"
    screen._render_snapshot = lambda: None  # type: ignore[method-assign]

    screen.action_next_scope()
    assert screen._current_scope == "project"
    screen.action_next_scope()
    assert screen._current_scope == "user"


def test_load_memory_snapshot_old_schema_defaults_tier_and_score(
    tmp_path: Path,
) -> None:
    project_store = tmp_path / "memory_project.json"
    _write_store(
        project_store,
        {
            "version": 1,
            "scope": "project",
            "items": [
                {
                    "id": "mem_p_000001",
                    "section": "Rules",
                    "content": "Legacy",
                    "status": "active",
                    "updated_at": "2026-04-22T10:00:00Z",
                }
            ],
        },
    )
    snapshot = load_memory_snapshot({"project": str(project_store)})
    item = snapshot["project"].items[0]
    assert item.tier == "warm"
    assert item.score == 50


def test_memory_viewer_sorts_hot_before_warm_before_cold(tmp_path: Path) -> None:
    project_store = tmp_path / "memory_project.json"
    _write_store(
        project_store,
        {
            "version": 1,
            "scope": "project",
            "items": [
                {
                    "id": "mem_p_000003",
                    "section": "Rules",
                    "content": "Cold item",
                    "status": "active",
                    "tier": "cold",
                    "score": 10,
                    "updated_at": "2026-04-22T10:00:00Z",
                },
                {
                    "id": "mem_p_000002",
                    "section": "Rules",
                    "content": "Warm item",
                    "status": "active",
                    "tier": "warm",
                    "score": 50,
                    "updated_at": "2026-04-22T10:00:00Z",
                },
                {
                    "id": "mem_p_000001",
                    "section": "Rules",
                    "content": "Hot item",
                    "status": "active",
                    "tier": "hot",
                    "score": 90,
                    "updated_at": "2026-04-22T10:00:00Z",
                },
            ],
        },
    )

    snapshot = load_memory_snapshot({"project": str(project_store)})
    sorted_items = sorted(
        snapshot["project"].items,
        key=lambda item: (
            0 if item.status == "active" else 1,
            {"hot": 0, "warm": 1, "cold": 2}.get(item.tier, 1),
            -item.score,
            item.section.casefold(),
            item.item_id,
        ),
    )
    assert [item.content for item in sorted_items[:3]] == [
        "Hot item",
        "Warm item",
        "Cold item",
    ]


def _item(
    item_id: str,
    *,
    status: str = "active",
    tier: str = "warm",
    score: int = 50,
    last_scored_at: str = "2026-04-22T10:00:00Z",
    section: str = "Rules",
    reason: str = "reason",
) -> MemoryItemView:
    return MemoryItemView(
        scope="user",
        item_id=item_id,
        section=section,
        status=status,
        content=f"content {item_id}",
        tier=tier,
        score=score,
        reason=reason,
        last_scored_at=last_scored_at,
        updated_at=last_scored_at,
    )


def test_memory_viewer_helper_normalization_and_sorting() -> None:
    assert _iso_to_local(None) is None
    assert _iso_to_local("not-a-date") is None
    assert _iso_to_local("2026-04-22T10:00:00Z") is not None
    assert _iso_to_local("2026-04-22T10:00:00") is not None
    assert _trim(["bad"], 10) == ""
    assert _trim("  many\n words  ", 20) == "many words"
    assert _trim("abcdef", 5) == "ab..."
    assert _normalize_tier(" HOT ") == "hot"
    assert _normalize_tier("invalid") == "warm"
    assert _normalize_score("101") == 100
    assert _normalize_score("-1") == 0
    assert _normalize_score("bad") == 50

    items = [
        _item("archived", status="archived", score=100),
        _item("low", score=10, last_scored_at="2026-04-22T09:00:00Z"),
        _item("high", score=90, last_scored_at="2026-04-22T11:00:00Z"),
    ]
    assert [item.item_id for item in _apply_sort(items, "score_desc")] == [
        "high",
        "low",
        "archived",
    ]
    assert [item.item_id for item in _apply_sort(items, "score_asc")] == [
        "low",
        "high",
        "archived",
    ]
    assert [item.item_id for item in _apply_sort(items, "last_scored_desc")] == [
        "high",
        "low",
        "archived",
    ]
    assert [item.item_id for item in _apply_sort(items, "last_scored_asc")] == [
        "low",
        "high",
        "archived",
    ]


def test_delete_memory_item_removes_matching_dicts_atomically(tmp_path: Path) -> None:
    store = tmp_path / "memory.json"
    _write_store(
        store,
        {
            "items": [
                {"id": "keep", "content": "a"},
                {"id": "delete", "content": "b"},
                "not a dict",
            ]
        },
    )

    _delete_memory_item(str(store), "delete")

    payload = json.loads(store.read_text(encoding="utf-8"))
    assert payload["items"] == [{"id": "keep", "content": "a"}, "not a dict"]


def test_delete_memory_item_rejects_invalid_schema(tmp_path: Path) -> None:
    store = tmp_path / "memory.json"
    _write_store(store, {"items": {}})

    with pytest.raises(ValueError, match="items is not a list"):
        _delete_memory_item(str(store), "x")


def test_memory_viewer_mount_unmount_and_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = MemoryViewerScreen(memory_store_paths={"user": "/tmp/user.json"})
    renders = 0

    def render() -> None:
        nonlocal renders
        renders += 1

    timer = pytest.MonkeyPatch()
    stopped: list[bool] = []
    fake_timer = type("Timer", (), {"stop": lambda _self: stopped.append(True)})()
    monkeypatch.setattr(screen, "_render_snapshot", render)
    monkeypatch.setattr(screen, "set_interval", lambda *_args: fake_timer)

    screen.on_mount()
    assert renders == 1
    assert screen._refresh_timer is fake_timer

    screen.on_unmount()
    assert stopped == [True]
    assert screen._refresh_timer is None
    timer.undo()

    screen._visible_items = [_item("a"), _item("b")]
    screen._selected_index = 1
    screen._pending_delete_id = "b"
    screen._status_message = "old"

    screen.action_refresh()
    screen.action_toggle_archived()
    screen._sort_mode = "unknown"
    screen.action_cycle_sort()
    screen.action_move_up()
    screen.action_move_down()
    screen.action_show_project_scope()
    screen.action_show_user_scope()

    assert screen._show_archived is False
    assert screen._sort_mode == "score_asc"
    assert screen._selected_index == 1
    assert screen._pending_delete_id is None
    assert screen._status_message == ""
    assert screen._current_scope == "user"


def test_memory_viewer_delete_action_confirm_success_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = MemoryViewerScreen(memory_store_paths={"user": "/tmp/user.json"})
    renders = 0

    def render() -> None:
        nonlocal renders
        renders += 1

    monkeypatch.setattr(screen, "_render_snapshot", render)
    monkeypatch.setattr(
        "invincat_cli.widgets.memory_viewer.t",
        lambda key: {
            "memory.viewer.delete.no_selection": "nothing selected",
            "memory.viewer.delete.success": "deleted {item_id}",
            "memory.viewer.delete.error": "error {error}",
        }.get(key, key),
    )

    screen.action_delete_item()
    assert screen._status_message == "nothing selected"

    screen._visible_items = [_item("delete-me")]
    screen._selected_index = 0
    screen.action_delete_item()
    assert screen._pending_delete_id == "delete-me"

    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "invincat_cli.widgets.memory_viewer._delete_memory_item",
        lambda path, item_id: deleted.append((path, item_id)),
    )
    screen.action_delete_item()
    assert deleted == [("/tmp/user.json", "delete-me")]
    assert screen._status_message == "deleted delete-me"
    assert screen._pending_delete_id is None

    screen._visible_items = [_item("bad")]
    screen._pending_delete_id = "bad"
    monkeypatch.setattr(
        "invincat_cli.widgets.memory_viewer._delete_memory_item",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    screen.action_delete_item()
    assert screen._status_message == "error boom"
    assert renders >= 4


class _FakeStatic:
    def __init__(self, value: object = "", **_kwargs: object) -> None:
        self.value = value

    def update(self, value: object) -> None:
        self.value = value


class _FakeContainer:
    def __init__(self) -> None:
        self.children = [_FakeStatic()]


class _FakeContext:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> _FakeContext:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _patch_memory_viewer_i18n(monkeypatch: pytest.MonkeyPatch) -> None:
    templates = {
        "memory.viewer.sort.score_desc": "score desc",
        "memory.viewer.sort.score_asc": "score asc",
        "memory.viewer.title": "Memory {scope} {sort}",
        "memory.viewer.summary_unavailable": "summary {valid}/{total} {scope}",
        "memory.viewer.no_scope_configured": "no scope",
        "memory.viewer.summary": (
            "summary {valid}/{total} {path} {items_total} {active} {archived} {latest}"
        ),
        "memory.viewer.label.scope": "scope",
        "memory.viewer.label.path": "path",
        "memory.viewer.label.status": "status",
        "memory.viewer.label.id": "id",
        "memory.viewer.label.section": "section",
        "memory.viewer.label.tier": "tier",
        "memory.viewer.label.score": "score",
        "memory.viewer.label.content": "content",
        "memory.viewer.label.reason": "reason",
        "memory.viewer.label.last_scored_at": "last scored",
        "memory.viewer.status.missing": "missing",
        "memory.viewer.status.invalid": "invalid {error}",
        "memory.viewer.status.ok": "ok",
        "memory.viewer.no_visible_items": "none visible",
        "memory.viewer.no_stores_configured": "no stores",
        "memory.viewer.delete.confirm": "confirm {item_id}",
        "memory.viewer.help": "help",
        "memory.viewer.loading": "loading",
    }
    monkeypatch.setattr(memory_viewer_mod, "t", lambda key: templates.get(key, key))


def test_memory_viewer_compose_builds_title_list_and_help(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_memory_viewer_i18n(monkeypatch)
    monkeypatch.setattr(memory_viewer_mod, "Vertical", _FakeContext)
    monkeypatch.setattr(memory_viewer_mod, "VerticalScroll", _FakeContext)
    monkeypatch.setattr(memory_viewer_mod, "Static", _FakeStatic)
    screen = MemoryViewerScreen(memory_store_paths={})

    children = list(screen.compose())

    assert [child.value for child in children] == ["", "", "loading", "help"]


def _render_screen(
    monkeypatch: pytest.MonkeyPatch,
    snapshots: dict[str, object],
) -> tuple[MemoryViewerScreen, dict[str, object]]:
    _patch_memory_viewer_i18n(monkeypatch)
    monkeypatch.setattr(memory_viewer_mod, "Static", _FakeStatic)
    monkeypatch.setattr(
        memory_viewer_mod, "load_memory_snapshot", lambda _paths: snapshots
    )
    widgets: dict[str, object] = {
        "#memory-title": _FakeStatic(),
        "#memory-summary": _FakeStatic(),
        "#memory-list": _FakeContainer(),
        ".memory-help": _FakeStatic(),
    }
    screen = MemoryViewerScreen(memory_store_paths={"user": "/tmp/user.json"})
    monkeypatch.setattr(
        screen, "query_one", lambda selector, _cls=None: widgets[selector]
    )
    screen._render_snapshot()
    return screen, widgets


def test_memory_viewer_render_snapshot_no_configured_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen, widgets = _render_screen(monkeypatch, {})
    rendered = widgets["#memory-list"].children[0].value

    assert screen._current_scope == "user"
    assert widgets["#memory-title"].value == "Memory user score desc"
    assert widgets["#memory-summary"].value == "summary 0/0 user"
    assert rendered.plain == "no scope"
    assert widgets[".memory-help"].value == "help"


def test_memory_viewer_render_snapshot_switches_to_available_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = memory_viewer_mod.MemoryScopeView(
        scope="project",
        path="/tmp/project.json",
        exists=True,
        valid=True,
        error=None,
        total=0,
        active=0,
        archived=0,
        latest_updated_at=None,
        items=[],
    )

    screen, widgets = _render_screen(monkeypatch, {"project": project})

    assert screen._current_scope == "project"
    assert widgets["#memory-title"].value == "Memory project score desc"


def test_memory_viewer_render_snapshot_missing_invalid_and_empty_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = memory_viewer_mod.MemoryScopeView(
        scope="user",
        path="/tmp/user.json",
        exists=False,
        valid=False,
        error="store not found",
        total=0,
        active=0,
        archived=0,
        latest_updated_at=None,
        items=[],
    )
    screen, widgets = _render_screen(monkeypatch, {"user": missing})
    assert "missing" in widgets["#memory-list"].children[0].value.plain
    assert screen._visible_items == []

    invalid = memory_viewer_mod.MemoryScopeView(
        scope="user",
        path="/tmp/user.json",
        exists=True,
        valid=False,
        error="bad <schema>",
        total=0,
        active=0,
        archived=0,
        latest_updated_at=None,
        items=[],
    )
    screen, widgets = _render_screen(monkeypatch, {"user": invalid})
    assert "invalid bad <schema>" in widgets["#memory-list"].children[0].value.plain
    assert screen._visible_items == []

    valid_empty = memory_viewer_mod.MemoryScopeView(
        scope="user",
        path="/tmp/user.json",
        exists=True,
        valid=True,
        error=None,
        total=1,
        active=0,
        archived=1,
        latest_updated_at=None,
        items=[_item("archived", status="archived")],
    )
    screen, widgets = _render_screen(monkeypatch, {"user": valid_empty})
    screen._show_archived = False
    screen._render_snapshot()
    assert "none visible" in widgets["#memory-list"].children[0].value.plain


def test_memory_viewer_render_snapshot_valid_items_and_help_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = memory_viewer_mod.MemoryScopeView(
        scope="user",
        path="/tmp/user.json",
        exists=True,
        valid=True,
        error=None,
        total=2,
        active=1,
        archived=1,
        latest_updated_at="2026-04-22T10:00:00Z",
        items=[
            _item("active", status="active", reason="why"),
            _item("archived", status="archived"),
        ],
    )
    screen, widgets = _render_screen(monkeypatch, {"user": view})
    content = widgets["#memory-list"].children[0].value.plain

    assert "id=active" in content
    assert "reason=why" in content
    assert screen._visible_items[0].item_id == "active"

    screen._status_message = "custom status"
    screen._render_snapshot()
    assert widgets[".memory-help"].value == "custom status"

    screen._status_message = ""
    screen._pending_delete_id = "active"
    screen._render_snapshot()
    assert widgets[".memory-help"].value == "confirm active"


def test_memory_viewer_next_scope_empty_and_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = MemoryViewerScreen(memory_store_paths={})
    empty._current_scope = "missing"
    empty.action_next_scope()
    assert empty._current_scope == "missing"

    screen = MemoryViewerScreen(memory_store_paths={"project": "/tmp/project.json"})
    rendered: list[bool] = []
    dismissed: list[object] = []
    monkeypatch.setattr(screen, "_render_snapshot", lambda: rendered.append(True))
    monkeypatch.setattr(screen, "dismiss", lambda value: dismissed.append(value))

    screen.action_next_scope()
    screen.action_cancel()

    assert screen._current_scope == "project"
    assert rendered == [True]
    assert dismissed == [None]
