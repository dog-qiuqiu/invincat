from __future__ import annotations

import json
from pathlib import Path

from invincat_cli.command_registry import IMMEDIATE_UI, SLASH_COMMANDS
from invincat_cli.widgets.memory_viewer import MemoryViewerScreen, load_memory_snapshot


def _write_store(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_memory_command_registered_for_immediate_ui() -> None:
    assert "/memory" in IMMEDIATE_UI
    names = {entry[0] for entry in SLASH_COMMANDS}
    assert "/memory" in names


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
                    "score_reason": "Stable preference",
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


def test_load_memory_snapshot_marks_invalid_schema(tmp_path: Path) -> None:
    project_store = tmp_path / "memory_project.json"
    _write_store(project_store, {"version": 1, "scope": "project", "items": {}})

    snapshot = load_memory_snapshot({"project": str(project_store)})
    project = snapshot["project"]
    assert project.exists is True
    assert project.valid is False
    assert project.error == "invalid schema: items is not a list"


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


def test_load_memory_snapshot_old_schema_defaults_tier_and_score(tmp_path: Path) -> None:
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
