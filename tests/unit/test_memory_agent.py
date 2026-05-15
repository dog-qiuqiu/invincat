"""Unit tests for structured memory-agent store operations."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from invincat_cli.memory_agent import (
    _SYSTEM_PROMPT,
    COLD_THRESHOLD,
    DEFAULT_SCORE,
    DEFAULT_TIER,
    HOT_THRESHOLD,
    MAX_ITEM_CONTENT_CHARS,
    MemoryAgentMiddleware,
    _align_score_to_tier,
    _apply_operations,
    _atomic_write_text,
    _backup_corrupt_store,
    _build_archived_overflow_operations,
    _build_invalid_fact_cleanup_operations,
    _build_memory_snapshot,
    _derive_tier_from_score,
    _detect_target_language,
    _env_float,
    _env_int,
    _find_item,
    _format_call_messages_for_log,
    _format_messages_for_memory_transcript,
    _is_explicit_memory_request,
    _is_task_complete,
    _is_trivial_turn,
    _last_human_text,
    _message_content_to_text,
    _new_store,
    _next_memory_id,
    _normalize_and_validate_operations,
    _normalize_confidence,
    _normalize_scope,
    _normalize_score,
    _normalize_status,
    _normalize_text,
    _normalize_tier,
    _read_memory_store,
    _write_memory_store,
)


def _item(
    item_id: str,
    *,
    scope: str = "project",
    section: str = "Project Rules",
    content: str = "Use snake_case.",
    status: str = "active",
) -> dict[str, str | None]:
    return {
        "id": item_id,
        "scope": scope,
        "section": section,
        "content": content,
        "status": status,
        "created_at": "2026-04-22T10:00:00Z",
        "updated_at": "2026-04-22T10:00:00Z",
        "archived_at": None,
        "source_thread_id": "__default_thread__",
        "source_anchor": "human|1|x|False",
        "confidence": "high",
        "tier": "warm",
        "score": 50,
        "reason": "",
        "last_scored_at": "2026-04-22T10:00:00Z",
        "norm_hash": f"{section.casefold()}::{content.casefold()}",
    }


def test_empty_store_create() -> None:
    user_store = _new_store("user")
    ops = [
        {
            "op": "create",
            "scope": "user",
            "section": "User Preferences",
            "content": "Prefer concise answers in Chinese.",
            "confidence": "high",
            "tier": "hot",
            "score": 90,
            "reason": "Explicit stable preference.",
        }
    ]
    new_user, _, changed = _apply_operations(
        user_store,
        None,
        ops,
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:00:00Z",
    )
    assert new_user is not None
    assert changed == ["user"]
    assert len(new_user["items"]) == 1
    assert new_user["items"][0]["id"] == "mem_u_000001"
    assert new_user["items"][0]["tier"] == "hot"
    assert new_user["items"][0]["score"] == 90


def test_update_existing_item() -> None:
    project_store = _new_store("project")
    project_store["items"].append(_item("mem_p_000001"))
    _, new_project, changed = _apply_operations(
        None,
        project_store,
        [
            {
                "op": "update",
                "scope": "project",
                "id": "mem_p_000001",
                "content": "Backend API fields should be snake_case.",
                "confidence": "high",
                "score": 88,
            }
        ],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:10:00Z",
    )
    assert new_project is not None
    assert changed == ["project"]
    assert (
        new_project["items"][0]["content"] == "Backend API fields should be snake_case."
    )
    assert new_project["items"][0]["tier"] == "hot"
    assert new_project["items"][0]["score"] == 88


def test_update_existing_item_with_score_and_tier_aligns_score() -> None:
    project_store = _new_store("project")
    project_store["items"].append(_item("mem_p_000001"))

    _, new_project, changed = _apply_operations(
        None,
        project_store,
        [
            {
                "op": "update",
                "scope": "project",
                "id": "mem_p_000001",
                "score": 95,
                "tier": "warm",
                "reason": "Tier remains authoritative.",
            }
        ],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:10:00Z",
    )

    assert new_project is not None
    assert changed == ["project"]
    item = new_project["items"][0]
    assert item["tier"] == "warm"
    assert COLD_THRESHOLD <= int(item["score"]) < HOT_THRESHOLD
    assert item["reason"] == "Tier remains authoritative."
    assert item["last_scored_at"] == "2026-04-22T10:10:00Z"


def test_archive_existing_item() -> None:
    project_store = _new_store("project")
    for idx in range(5):
        project_store["items"].append(
            _item(f"mem_p_{idx + 1:06d}", content=f"Rule {idx}")
        )
    _, new_project, changed = _apply_operations(
        None,
        project_store,
        [
            {
                "op": "archive",
                "scope": "project",
                "id": "mem_p_000001",
                "reason": "superseded",
            }
        ],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:10:00Z",
    )
    assert new_project is not None
    assert changed == ["project"]
    assert new_project["items"][0]["status"] == "archived"
    assert new_project["items"][0]["archived_at"] == "2026-04-22T10:10:00Z"
    assert new_project["items"][0]["reason"] == "superseded"


def test_delete_existing_item() -> None:
    project_store = _new_store("project")
    project_store["items"].append(_item("mem_p_000001", content="Old incorrect fact"))
    project_store["items"].append(_item("mem_p_000002", content="Current fact"))
    _, new_project, changed = _apply_operations(
        None,
        project_store,
        [{"op": "delete", "scope": "project", "id": "mem_p_000001", "reason": "wrong"}],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:10:00Z",
    )
    assert new_project is not None
    assert changed == ["project"]
    assert [item["id"] for item in new_project["items"]] == ["mem_p_000002"]


def test_update_nonexistent_id_rejected() -> None:
    project_store = _new_store("project")
    _, new_project, changed = _apply_operations(
        None,
        project_store,
        [{"op": "update", "scope": "project", "id": "mem_p_999999", "content": "x"}],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:10:00Z",
    )
    assert new_project is not None
    assert changed == []
    assert new_project["items"] == []


def test_duplicate_create_deduped_to_noop() -> None:
    user_store = _new_store("user")
    user_store["items"].append(
        _item(
            "mem_u_000001",
            scope="user",
            section="User Preferences",
            content="Prefer concise answers in Chinese.",
        )
    )
    new_user, _, changed = _apply_operations(
        user_store,
        None,
        [
            {
                "op": "create",
                "scope": "user",
                "section": "User Preferences",
                "content": "Prefer concise answers in Chinese.",
                "confidence": "high",
            }
        ],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:10:00Z",
    )
    assert new_user is not None
    assert changed == []
    assert len(new_user["items"]) == 1


def test_load_or_recover_store_initializes_empty_when_missing(tmp_path: Path) -> None:
    store = tmp_path / "memory_project.json"
    middleware = MemoryAgentMiddleware(
        memory_store_paths={"project": str(store)},
    )
    loaded = middleware._load_or_recover_store("project", "t1", "a1")
    assert loaded is not None
    assert loaded["items"] == []
    assert store.exists()


def test_conflicting_operations_on_same_id_rejected() -> None:
    project_store = _new_store("project")
    project_store["items"].append(_item("mem_p_000001"))
    _, new_project, changed = _apply_operations(
        None,
        project_store,
        [
            {"op": "update", "scope": "project", "id": "mem_p_000001", "content": "A"},
            {"op": "delete", "scope": "project", "id": "mem_p_000001", "reason": "x"},
        ],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:10:00Z",
    )
    assert new_project is not None
    assert changed == []
    assert new_project["items"][0]["status"] == "active"
    assert new_project["items"][0]["content"] == "Use snake_case."


def test_multiple_delete_ops_are_applied_without_ratio_guard() -> None:
    project_store = _new_store("project")
    for idx in range(5):
        project_store["items"].append(
            _item(f"mem_p_{idx + 1:06d}", content=f"Rule {idx}")
        )
    _, new_project, changed = _apply_operations(
        None,
        project_store,
        [
            {"op": "delete", "scope": "project", "id": "mem_p_000001", "reason": ""},
            {"op": "delete", "scope": "project", "id": "mem_p_000002", "reason": ""},
        ],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:10:00Z",
    )
    assert new_project is not None
    assert changed == ["project"]
    remaining_ids = {item["id"] for item in new_project["items"]}
    assert "mem_p_000001" not in remaining_ids
    assert "mem_p_000002" not in remaining_ids


def test_multiple_archive_ops_are_applied() -> None:
    project_store = _new_store("project")
    for idx in range(5):
        project_store["items"].append(
            _item(f"mem_p_{idx + 1:06d}", content=f"Rule {idx}")
        )
    _, new_project, changed = _apply_operations(
        None,
        project_store,
        [
            {"op": "archive", "scope": "project", "id": "mem_p_000001", "reason": ""},
            {"op": "archive", "scope": "project", "id": "mem_p_000002", "reason": ""},
        ],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:10:00Z",
    )
    assert new_project is not None
    assert changed == ["project"]
    statuses = {item["id"]: item["status"] for item in new_project["items"]}
    assert statuses["mem_p_000001"] == "archived"
    assert statuses["mem_p_000002"] == "archived"
    assert statuses["mem_p_000003"] == "active"


def test_multiple_contradiction_deletes_are_applied() -> None:
    project_store = _new_store("project")
    for idx in range(5):
        project_store["items"].append(
            _item(f"mem_p_{idx + 1:06d}", content=f"Rule {idx}")
        )
    _, new_project, changed = _apply_operations(
        None,
        project_store,
        [
            {
                "op": "delete",
                "scope": "project",
                "id": "mem_p_000001",
                "reason": "User stated this is no longer valid.",
            },
            {
                "op": "delete",
                "scope": "project",
                "id": "mem_p_000002",
                "reason": "Contradicts current facts per user.",
            },
        ],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:10:00Z",
    )
    assert new_project is not None
    assert changed == ["project"]
    remaining_ids = {item["id"] for item in new_project["items"]}
    assert "mem_p_000001" not in remaining_ids
    assert "mem_p_000002" not in remaining_ids
    assert len(new_project["items"]) == 3


def test_contradiction_delete_plus_create_no_duplicate() -> None:
    # Regression: when the model emits delete(old) + create(new) for a
    # contradicted fact, the old item must be removed and only the new one
    # should survive — not both.
    project_store = _new_store("project")
    project_store["items"].append(
        _item("mem_p_000001", content="Uses Poetry for dependency management.")
    )
    _, new_project, changed = _apply_operations(
        None,
        project_store,
        [
            {
                "op": "delete",
                "scope": "project",
                "id": "mem_p_000001",
                "reason": "User stated the project migrated from Poetry to uv.",
            },
            {
                "op": "create",
                "scope": "project",
                "section": "Tooling",
                "content": "Uses `uv` for dependency management.",
                "confidence": "high",
                "tier": "hot",
                "score": 80,
                "reason": "User confirmed migration from Poetry to uv.",
            },
        ],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:10:00Z",
    )
    assert new_project is not None
    assert changed == ["project"]
    assert len(new_project["items"]) == 1
    assert new_project["items"][0]["content"] == "Uses `uv` for dependency management."


def test_fixed_issue_delete_ops_are_applied() -> None:
    project_store = _new_store("project")
    for idx in range(5):
        project_store["items"].append(
            _item(
                f"mem_p_{idx + 1:06d}",
                section="Known Issues",
                content=f"Known bug {idx}",
            )
        )

    _, new_project, changed = _apply_operations(
        None,
        project_store,
        [
            {
                "op": "delete",
                "scope": "project",
                "id": "mem_p_000001",
                "reason": "Bug fixed this turn.",
            },
            {
                "op": "delete",
                "scope": "project",
                "id": "mem_p_000002",
                "reason": "该问题已修复。",
            },
        ],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:10:00Z",
    )

    assert new_project is not None
    assert changed == ["project"]
    remaining_ids = {item["id"] for item in new_project["items"]}
    assert "mem_p_000001" not in remaining_ids
    assert "mem_p_000002" not in remaining_ids


def test_invalid_fact_delete_wins_over_same_id_metadata_conflict() -> None:
    project_store = _new_store("project")
    project_store["items"].append(
        _item(
            "mem_p_000001",
            section="Known Issues",
            content="Login form can submit duplicate requests.",
        )
    )

    _, new_project, changed = _apply_operations(
        None,
        project_store,
        [
            {
                "op": "rescore",
                "scope": "project",
                "id": "mem_p_000001",
                "score": 10,
                "reason": "Issue fixed this turn.",
            },
            {
                "op": "delete",
                "scope": "project",
                "id": "mem_p_000001",
                "reason": "Issue fixed this turn.",
            },
        ],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:10:00Z",
    )

    assert new_project is not None
    assert changed == ["project"]
    assert new_project["items"] == []


def test_atomic_write_and_whitelist_authorization(tmp_path: Path) -> None:
    store = tmp_path / "memory_project.json"
    outsider = tmp_path / "other.txt"
    middleware = MemoryAgentMiddleware(
        memory_store_paths={"project": str(store)},
    )
    _atomic_write_text(store, "{}")
    assert store.read_text(encoding="utf-8") == "{}"
    assert middleware._is_authorized_path(store)
    assert not middleware._is_authorized_path(outsider)


def test_operation_validation_contract() -> None:
    payload = {
        "operations": [
            {"op": "create", "scope": "project", "section": "S", "content": "C"},
            {"op": "update", "scope": "project", "id": "mem_p_000001", "content": ""},
            {"op": "archive", "scope": "project", "id": "mem_p_000001"},
            {
                "op": "delete",
                "scope": "project",
                "id": "mem_p_000002",
                "reason": "wrong",
            },
            {"op": "noop"},
        ]
    }
    ops = _normalize_and_validate_operations(payload)
    assert [op["op"] for op in ops] == ["create", "archive", "delete", "noop"]
    assert ops[0]["content"] == "C"


def test_update_metadata_only_invalid_fact_deletes_item() -> None:
    ops = _normalize_and_validate_operations(
        {
            "operations": [
                {
                    "op": "update",
                    "scope": "project",
                    "id": "mem_p_000001",
                    "score": 20,
                    "reason": "The memory is no longer accurate.",
                }
            ]
        }
    )

    assert ops == [
        {
            "op": "delete",
            "scope": "project",
            "id": "mem_p_000001",
            "reason": "The memory is no longer accurate.",
        }
    ]


def test_rescore_does_not_modify_content_or_updated_at() -> None:
    project_store = _new_store("project")
    project_store["items"].append(_item("mem_p_000001", content="Keep this text"))
    original_updated_at = project_store["items"][0]["updated_at"]
    _, new_project, changed = _apply_operations(
        None,
        project_store,
        [
            {
                "op": "rescore",
                "scope": "project",
                "id": "mem_p_000001",
                "score": 12,
                "reason": "Stale",
            }
        ],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T11:00:00Z",
    )
    assert new_project is not None
    assert changed == ["project"]
    item = new_project["items"][0]
    assert item["content"] == "Keep this text"
    assert item["updated_at"] == original_updated_at
    assert item["score"] == 12
    assert item["tier"] == "cold"


def test_rescore_with_invalid_fact_reason_deletes_item() -> None:
    ops = _normalize_and_validate_operations(
        {
            "operations": [
                {
                    "op": "rescore",
                    "scope": "project",
                    "id": "mem_p_000001",
                    "score": 10,
                    "reason": "Existing memory is contradicted by current facts.",
                }
            ]
        }
    )

    assert ops == [
        {
            "op": "delete",
            "scope": "project",
            "id": "mem_p_000001",
            "reason": "Existing memory is contradicted by current facts.",
        }
    ]

    project_store = _new_store("project")
    project_store["items"].append(_item("mem_p_000001", content="Old incorrect fact"))
    _, new_project, changed = _apply_operations(
        None,
        project_store,
        ops,
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:10:00Z",
    )

    assert new_project is not None
    assert changed == ["project"]
    assert new_project["items"] == []


def test_retier_does_not_modify_content_or_updated_at() -> None:
    project_store = _new_store("project")
    project_store["items"].append(_item("mem_p_000001", content="Keep this text"))
    original_updated_at = project_store["items"][0]["updated_at"]
    _, new_project, changed = _apply_operations(
        None,
        project_store,
        [
            {
                "op": "retier",
                "scope": "project",
                "id": "mem_p_000001",
                "tier": "cold",
                "reason": "History only",
            }
        ],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T11:00:00Z",
    )
    assert new_project is not None
    assert changed == ["project"]
    item = new_project["items"][0]
    assert item["content"] == "Keep this text"
    assert item["updated_at"] == original_updated_at
    assert item["tier"] == "cold"
    assert item["score"] < COLD_THRESHOLD


def test_retier_with_invalid_fact_reason_deletes_item() -> None:
    ops = _normalize_and_validate_operations(
        {
            "operations": [
                {
                    "op": "retier",
                    "scope": "project",
                    "id": "mem_p_000001",
                    "tier": "cold",
                    "reason": "这条记忆与当前事实不符，已被替代。",
                }
            ]
        }
    )

    assert ops == [
        {
            "op": "delete",
            "scope": "project",
            "id": "mem_p_000001",
            "reason": "这条记忆与当前事实不符，已被替代。",
        }
    ]


def test_invalid_fact_cleanup_scans_full_store() -> None:
    project_store = _new_store("project")
    for idx in range(100):
        project_store["items"].append(
            _item(
                f"mem_p_{idx + 1:06d}",
                content=f"Valid rule {idx}",
            )
        )
    invalid_id = "mem_p_000101"
    invalid = _item(invalid_id, content="Old incorrect fact")
    invalid["tier"] = "cold"
    invalid["score"] = 5
    invalid["reason"] = "Existing memory is contradicted by current facts."
    project_store["items"].append(invalid)

    snapshot = _build_memory_snapshot(None, project_store)
    assert any(item["id"] == invalid_id for item in snapshot["project"]["items"])

    cleanup = _build_invalid_fact_cleanup_operations(None, project_store)
    assert cleanup == [
        {
            "op": "delete",
            "scope": "project",
            "id": invalid_id,
            "reason": "Existing memory is contradicted by current facts.",
            "_cleanup": True,
        }
    ]


def test_invalid_fact_cleanup_deletes_warm_item_when_reason_is_clear() -> None:
    project_store = _new_store("project")
    invalid = _item("mem_p_000001", content="Old incorrect fact")
    invalid["tier"] = "warm"
    invalid["score"] = 45
    invalid["reason"] = "该记忆与当前事实不一致，内容不准确。"
    project_store["items"].append(invalid)

    cleanup = _build_invalid_fact_cleanup_operations(None, project_store)

    assert cleanup == [
        {
            "op": "delete",
            "scope": "project",
            "id": "mem_p_000001",
            "reason": "该记忆与当前事实不一致，内容不准确。",
            "_cleanup": True,
        }
    ]


def test_old_schema_items_are_backfilled_with_default_tier_score(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "memory_project.json"
    _atomic_write_text(
        store_path,
        (
            '{"version":1,"scope":"project","items":[{"id":"mem_p_000001","scope":"project",'
            '"section":"Rules","content":"Use uv.","status":"active","created_at":"2026-04-22T10:00:00Z",'
            '"updated_at":"2026-04-22T10:00:00Z"}]}\n'
        ),
    )
    loaded = _read_memory_store(store_path, "project")
    item = loaded["items"][0]
    assert item["tier"] == DEFAULT_TIER
    assert item["score"] == DEFAULT_SCORE
    assert item["last_scored_at"] == "2026-04-22T10:00:00Z"


def test_legacy_score_reason_is_read_as_reason(tmp_path: Path) -> None:
    store_path = tmp_path / "memory_project.json"
    _atomic_write_text(
        store_path,
        (
            '{"version":1,"scope":"project","items":[{"id":"mem_p_000001","scope":"project",'
            '"section":"Rules","content":"Use uv.","status":"active","created_at":"2026-04-22T10:00:00Z",'
            '"updated_at":"2026-04-22T10:00:00Z","score_reason":"Legacy rationale."}]}\n'
        ),
    )
    loaded = _read_memory_store(store_path, "project")
    assert loaded["items"][0]["reason"] == "Legacy rationale."
    assert "score_reason" not in loaded["items"][0]


def test_operation_validation_rejects_invalid_tier() -> None:
    payload = {
        "operations": [
            {
                "op": "create",
                "scope": "project",
                "section": "S",
                "content": "C",
                "tier": "invalid",
            }
        ]
    }
    assert _normalize_and_validate_operations(payload) == []


def test_score_clamp_and_tier_derivation() -> None:
    payload = {
        "operations": [
            {
                "op": "create",
                "scope": "project",
                "section": "Rules",
                "content": "Use uv",
                "score": 188,
            },
            {
                "op": "rescore",
                "scope": "project",
                "id": "mem_p_000001",
                "score": -7,
            },
        ]
    }
    ops = _normalize_and_validate_operations(payload)
    assert ops[0]["score"] == 100
    assert ops[1]["score"] == 0
    assert _derive_tier_from_score(_normalize_score(88)) == "hot"


def test_env_parsing_language_and_turn_helpers(monkeypatch: Any) -> None:
    monkeypatch.setenv("MEM_INT", "bad")
    monkeypatch.setenv("MEM_FLOAT", "-5")
    assert _env_int("MISSING_INT", 7, minimum=3) == 7
    assert _env_int("MEM_INT", 7, minimum=3) == 7
    assert _env_float("MISSING_FLOAT", 1.5, minimum=0.5) == 1.5
    assert _env_float("MEM_FLOAT", 1.5, minimum=0.5) == 0.5

    assert _detect_target_language("") == "the language of the last human message"
    assert _detect_target_language("12345") == "the language of the last human message"
    assert _is_trivial_turn([]) is True
    assert _last_human_text([_Msg("ai", "no human")]) == ""
    assert (
        _last_human_text(
            [
                _Msg(
                    "human",
                    [
                        {"text": "remember"},
                        {"kind": "image"},
                        "plain",
                    ],
                )
            ]
        )
        == "remember plain"
    )

    assert _is_task_complete([]) is False
    assert _is_task_complete([_Msg("tool", "done")]) is False
    assert _is_task_complete([_Msg("human", "hi")]) is False
    assert _is_task_complete([_Msg("ai", "calling", tool_calls=[{"id": "1"}])]) is False
    assert _is_task_complete([_Msg("ai", "final", tool_calls=[])]) is True


def test_message_formatting_helpers_include_metadata() -> None:
    tool_msg = _Msg("tool", [{"text": "line one"}, {"json": True}, "tail"])
    tool_msg.name = "read_file"
    tool_msg.tool_call_id = "call-1"
    ai_msg = _Msg("ai", "", tool_calls=[{"name": "write_file", "args": {"path": "a"}}])

    assert _message_content_to_text(tool_msg.content).splitlines() == [
        "line one",
        '{"json": true}',
        "tail",
    ]
    log = _format_call_messages_for_log([tool_msg, ai_msg])
    assert "name=read_file" in log
    assert "tool_call_id=call-1" in log
    assert "tool_calls" in log

    transcript = _format_messages_for_memory_transcript([tool_msg, ai_msg])
    assert "conversation_transcript" in transcript
    assert "[1] role=tool name=read_file tool_call_id=call-1" in transcript
    assert "assistant_tool_calls_json" in transcript


def test_normalization_helpers_cover_invalid_inputs(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("MEM_FLOAT_BAD", "not-a-float")

    assert _env_float("MEM_FLOAT_BAD", 1.5, minimum=0.5) == 1.5
    assert _normalize_scope(123) is None
    assert _normalize_status(" ARCHIVED ") == "archived"
    assert _normalize_status("bad") == "active"
    assert _normalize_confidence("HIGH") == "high"
    assert _normalize_confidence("bad", default="low") == "low"
    assert _normalize_tier("COLD") == "cold"
    assert _normalize_tier("bad", default="hot") == "hot"
    assert _normalize_text(123, max_chars=5) == ""
    assert _align_score_to_tier(95, "warm") == HOT_THRESHOLD - 1
    assert _align_score_to_tier(5, "warm") == COLD_THRESHOLD


def test_read_memory_store_handles_invalid_schema_and_filters_dirty_items(
    tmp_path: Path,
) -> None:
    missing = _read_memory_store(tmp_path / "missing.json", "project")
    assert missing == _new_store("project")

    store_path = tmp_path / "memory_project.json"
    store_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    invalid_schema = _read_memory_store(store_path, "project")
    assert invalid_schema["__read_error__"] is True

    store_path.write_text(json.dumps({"scope": "bad", "items": []}), encoding="utf-8")
    invalid_scope = _read_memory_store(store_path, "project")
    assert invalid_scope["__read_error__"] is True

    store_path.write_text(
        json.dumps({"scope": "project", "items": "bad"}),
        encoding="utf-8",
    )
    invalid_items = _read_memory_store(store_path, "project")
    assert invalid_items["__read_error__"] is True

    dirty_items = [
        "bad",
        {"scope": "user", "id": "mem_u_000001", "section": "S", "content": "C"},
        {"scope": "project", "id": 123, "section": "S", "content": "C"},
        {"scope": "project", "id": "wrong", "section": "S", "content": "C"},
        {"scope": "project", "id": "mem_p_000001", "section": "", "content": "C"},
        {
            "scope": "project",
            "id": "mem_p_000002",
            "section": " Rules ",
            "content": "  Use pytest.  ",
            "status": "archived",
        },
    ]
    store_path.write_text(
        json.dumps({"scope": "project", "items": dirty_items}),
        encoding="utf-8",
    )

    filtered = _read_memory_store(store_path, "project")

    assert [item["id"] for item in filtered["items"]] == ["mem_p_000002"]
    assert filtered["items"][0]["section"] == "Rules"
    assert filtered["items"][0]["content"] == "Use pytest."
    assert filtered["items"][0]["status"] == "archived"


def test_next_memory_id_and_snapshot_ignore_dirty_items() -> None:
    store = _new_store("project")
    item = _item("mem_p_000004")
    item["score"] = "bad"  # type: ignore[assignment]
    store["items"] = [
        "bad",
        {"id": 123},
        {"id": "other"},
        item,
    ]

    assert _next_memory_id(store, "project") == "mem_p_000005"

    snapshot = _build_memory_snapshot(None, store)
    assert snapshot["user"] == {"items": []}
    assert [item["id"] for item in snapshot["project"]["items"]] == [
        "mem_p_000004",
        123,
        "other",
    ]
    assert snapshot["project"]["items"][0]["score"] == DEFAULT_SCORE


def test_create_aligns_score_with_explicit_tier() -> None:
    new_user, _, changed = _apply_operations(
        _new_store("user"),
        None,
        [
            {
                "op": "create",
                "scope": "user",
                "section": "Prefs",
                "content": "Prefer concise output.",
                "tier": "hot",
                "score": 10,
            }
        ],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:00:00Z",
    )
    assert new_user is not None
    assert changed == ["user"]
    item = new_user["items"][0]
    assert item["tier"] == "hot"
    assert item["score"] >= HOT_THRESHOLD


def test_validation_rejects_bad_shapes_and_normalizes_update_fields() -> None:
    assert _normalize_and_validate_operations(None) == []
    assert _normalize_and_validate_operations({"operations": "bad"}) == []

    payload = {
        "operations": [
            "bad",
            {},
            {"op": "unknown"},
            {"op": "create", "scope": "bad", "section": "S", "content": "C"},
            {
                "op": "create",
                "scope": "project",
                "id": "mem_p_000001",
                "section": "S",
                "content": "C",
            },
            {"op": "update", "scope": "project", "id": ""},
            {"op": "update", "scope": "project", "id": "mem_p_000001"},
            {
                "op": "update",
                "scope": "project",
                "id": "mem_p_000001",
                "tier": "bad",
            },
            {"op": "rescore", "scope": "project", "id": "mem_p_000001"},
            {"op": "retier", "scope": "project", "id": "mem_p_000001"},
            {
                "op": "retier",
                "scope": "project",
                "id": "mem_p_000001",
                "tier": "bad",
            },
            {"op": "archive", "scope": "project", "id": ""},
            {
                "op": "update",
                "scope": "project",
                "id": "mem_p_000001",
                "content": " Updated fact ",
                "confidence": "LOW",
                "tier": "hot",
                "score": 20,
                "score_reason": " confirmed ",
            },
        ]
    }

    ops = _normalize_and_validate_operations(payload)

    assert ops == [
        {
            "op": "update",
            "scope": "project",
            "id": "mem_p_000001",
            "content": "Updated fact",
            "confidence": "low",
            "tier": "hot",
            "score": 20,
            "reason": "confirmed",
        }
    ]


def test_validation_edge_branches_for_create_rescore_retier_and_delete() -> None:
    payload = {
        "operations": [
            {"op": "create", "scope": "project", "section": "", "content": "C"},
            {"op": "create", "scope": "project", "section": "S", "content": ""},
            {"op": "rescore", "scope": "project", "id": 123, "score": 10},
            {"op": "retier", "scope": "project", "id": 123, "tier": "warm"},
            {
                "op": "rescore",
                "scope": "project",
                "id": "mem_p_000001",
                "score": 60,
                "reason": "still useful",
            },
            {
                "op": "retier",
                "scope": "project",
                "id": "mem_p_000002",
                "tier": "warm",
                "score_reason": "promoted",
            },
            {
                "op": "archive",
                "scope": "project",
                "id": "mem_p_000003",
                "reason": "",
            },
        ]
    }

    ops = _normalize_and_validate_operations(payload)

    assert ops == [
        {
            "op": "rescore",
            "scope": "project",
            "id": "mem_p_000001",
            "score": 60,
            "reason": "still useful",
        },
        {
            "op": "retier",
            "scope": "project",
            "id": "mem_p_000002",
            "tier": "warm",
            "reason": "promoted",
        },
        {
            "op": "archive",
            "scope": "project",
            "id": "mem_p_000003",
            "reason": None,
        },
    ]
    assert _find_item(None, "mem_p_000001") is None


def test_create_can_initialize_missing_user_store_and_next_id_skips_bad_items() -> None:
    user_store = _new_store("user")
    user_store["items"].extend(
        [
            "bad",
            {"id": 1},
            _item("mem_u_000003", scope="user", content="Existing preference."),
        ]
    )

    new_user, _, changed = _apply_operations(
        user_store,
        None,
        [
            {
                "op": "create",
                "scope": "user",
                "section": "Prefs",
                "content": "Prefer short summaries.",
            },
            {
                "op": "create",
                "scope": "project",
                "section": "Rules",
                "content": "Use pytest.",
            },
        ],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:00:00Z",
    )

    assert new_user is not None
    assert changed == ["project", "user"]
    assert new_user["items"][-1]["id"] == "mem_u_000004"


def test_apply_operations_edge_branches_for_updates_and_archives() -> None:
    project_store = _new_store("project")
    base = _item("mem_p_000001")
    base["score"] = 50  # type: ignore[index]
    base["tier"] = "warm"  # type: ignore[index]
    blank_target = _item("mem_p_000002", content="Blank target")
    archived = _item("mem_p_000003", content="Archived stale", status="archived")
    archived["archived_at"] = "2026-04-20T10:00:00Z"
    already_archived = _item(
        "mem_p_000004",
        content="Already archived",
        status="archived",
    )
    already_archived["archived_at"] = "2026-04-20T11:00:00Z"
    project_store["items"].extend([base, blank_target, archived, already_archived])

    new_user, new_project, changed = _apply_operations(
        None,
        project_store,
        [
            {
                "op": "create",
                "scope": "user",
                "section": "Prefs",
                "content": "Use concise answers.",
            },
            {"op": "update", "scope": "bad", "id": "mem_p_000001", "content": "x"},
            {"op": "update", "scope": "project", "id": 123, "content": "x"},
            {"op": "update", "scope": "project", "id": "mem_p_000002", "content": " "},
            {
                "op": "update",
                "scope": "project",
                "id": "mem_p_000001",
                "score": 95,
                "tier": "warm",
                "reason": "tier remains authoritative",
            },
            {
                "op": "update",
                "scope": "project",
                "id": "mem_p_000003",
                "content": "Reactivated memory",
                "tier": "hot",
            },
            {"op": "archive", "scope": "project", "id": "mem_p_000004"},
        ],
        thread_id="thread-edge",
        source_anchor="anchor-edge",
        now_iso="2026-04-22T10:00:00Z",
    )

    assert new_user is not None
    assert new_user["items"][0]["id"] == "mem_u_000001"
    assert changed == ["project", "user"]
    assert new_project is not None
    updated = _find_item(new_project, "mem_p_000001")
    assert updated is not None
    assert updated["tier"] == "warm"
    assert COLD_THRESHOLD <= int(updated["score"]) < HOT_THRESHOLD
    reactivated = _find_item(new_project, "mem_p_000003")
    assert reactivated is not None
    assert reactivated["status"] == "active"
    assert reactivated["archived_at"] is None
    unchanged = _find_item(new_project, "mem_p_000002")
    assert unchanged is not None
    assert unchanged["content"] == "Blank target"

    malformed_store = {"version": 1, "scope": "project", "items": "bad"}
    _, malformed_after, malformed_changed = _apply_operations(
        None,
        malformed_store,
        [{"op": "delete", "scope": "project", "id": "mem_p_000001"}],
        thread_id="thread-edge",
        source_anchor="anchor-edge",
        now_iso="2026-04-22T10:00:00Z",
    )
    assert malformed_after == malformed_store
    assert malformed_changed == []


def test_archived_overflow_cleanup_deletes_oldest_archived_items() -> None:
    project_store = _new_store("project")
    for idx in range(4):
        item = _item(f"mem_p_{idx + 1:06d}", content=f"Archived {idx}")
        item["status"] = "archived"
        item["archived_at"] = f"2026-04-22T10:0{idx}:00Z"
        project_store["items"].append(item)
    project_store["items"].append(_item("mem_p_000005", content="Active stays"))

    ops = _build_archived_overflow_operations(None, project_store, max_archived=2)

    assert [op["id"] for op in ops] == ["mem_p_000001", "mem_p_000002"]
    assert all(op["_cleanup"] is True for op in ops)


def test_memory_snapshot_includes_all_items_per_scope() -> None:
    project_store = _new_store("project")
    for idx in range(120):
        project_store["items"].append(
            _item(
                f"mem_p_{idx + 1:06d}",
                content=f"Rule {idx}",
            )
        )
    snapshot = _build_memory_snapshot(None, project_store)
    project_snapshot = snapshot["project"]
    assert isinstance(project_snapshot, dict)
    assert len(project_snapshot["items"]) == 120


def test_store_read_write_roundtrip(tmp_path: Path) -> None:
    store_path = tmp_path / "memory_user.json"
    store = _new_store("user")
    store["items"].append(
        _item(
            "mem_u_000001",
            scope="user",
            section="User Preferences",
            content="X" * (MAX_ITEM_CONTENT_CHARS + 50),
        )
    )
    _write_memory_store(store_path, store)
    loaded = _read_memory_store(store_path, "user")
    assert loaded["scope"] == "user"
    assert len(loaded["items"]) == 1
    assert len(loaded["items"][0]["content"]) <= MAX_ITEM_CONTENT_CHARS


class _Msg:
    def __init__(
        self, msg_type: str, content: str, *, tool_calls: list[Any] | None = None
    ) -> None:
        self.type = msg_type
        self.content = content
        self.tool_calls = tool_calls if tool_calls is not None else []


class _Runtime:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def stream_writer(self, payload: dict[str, Any]) -> None:
        self.events.append(payload)


class _NoopMemoryModel:
    def bind(self, **_kwargs: Any) -> _NoopMemoryModel:
        return self

    async def ainvoke(self, *_args: Any, **_kwargs: Any) -> Any:
        return type("Response", (), {"content": '{"operations": [{"op": "noop"}]}'})()


class _CapturingMemoryModel:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    def bind(self, **_kwargs: Any) -> _CapturingMemoryModel:
        return self

    async def ainvoke(self, messages: list[Any], **_kwargs: Any) -> Any:
        self.messages = list(messages)
        return type("Response", (), {"content": '{"operations": [{"op": "noop"}]}'})()


class _MalformedMemoryModel:
    def bind(self, **_kwargs: Any) -> _MalformedMemoryModel:
        return self

    async def ainvoke(self, *_args: Any, **_kwargs: Any) -> Any:
        return type("Response", (), {"content": "not json"})()


class _FailingMemoryModel:
    def bind(self, **_kwargs: Any) -> _FailingMemoryModel:
        return self

    async def ainvoke(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("model unavailable")


def test_resolve_memory_model_uses_default_deepseek_thinking(monkeypatch: Any) -> None:
    middleware = MemoryAgentMiddleware()
    runtime = type(
        "Runtime",
        (),
        {"context": {"memory_model": "openai:deepseek-chat"}},
    )()
    fallback = object()
    created = object()
    calls: list[dict[str, Any]] = []

    def _fake_create_model(*args: Any, **kwargs: Any) -> Any:
        calls.append({"args": args, "kwargs": kwargs})
        return type("ModelResult", (), {"model": created})()

    import invincat_cli.config as config_mod

    monkeypatch.setattr(config_mod, "create_model", _fake_create_model)

    assert middleware._resolve_memory_model(runtime, fallback) is created
    assert calls == [
        {
            "args": ("openai:deepseek-chat",),
            "kwargs": {"extra_kwargs": {}},
        }
    ]


def test_aafter_agent_emits_status_and_advances_cursor(monkeypatch: Any) -> None:
    middleware = MemoryAgentMiddleware()
    middleware._captured_model = object()
    runtime = _Runtime()

    async def _fake_safe_extract_and_write(*args: Any, **kwargs: Any) -> list[str]:
        return []

    monkeypatch.setattr(
        middleware, "_safe_extract_and_write", _fake_safe_extract_and_write
    )
    monkeypatch.setattr(middleware, "_resolve_thread_id", lambda: "thread-1")

    messages = [
        _Msg("human", "Please remember: I prefer concise answers in Chinese."),
        _Msg("ai", "Acknowledged.", tool_calls=[]),
    ]
    state = {"messages": messages}
    result = asyncio.run(middleware.aafter_agent(state, runtime))

    assert result is None
    assert runtime.events == [
        {"event": "memory_agent", "status": "running"},
        {"event": "memory_agent", "status": "done"},
    ]
    assert middleware._cursor_by_thread.get("thread-1") == len(messages)


def test_aafter_agent_does_not_advance_cursor_on_extract_failure(
    monkeypatch: Any,
) -> None:
    middleware = MemoryAgentMiddleware()
    middleware._captured_model = object()
    runtime = _Runtime()

    async def _fake_safe_extract_and_write(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        middleware, "_safe_extract_and_write", _fake_safe_extract_and_write
    )
    monkeypatch.setattr(middleware, "_resolve_thread_id", lambda: "thread-2")

    state = {
        "messages": [
            _Msg("human", "Please remember this preference for future turns."),
            _Msg("ai", "Done.", tool_calls=[]),
        ]
    }
    result = asyncio.run(middleware.aafter_agent(state, runtime))

    assert result is None
    assert "thread-2" not in middleware._cursor_by_thread


def test_middleware_cursor_cooldown_and_thread_helpers(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    store = tmp_path / "memory_project.json"
    store.write_text("{}", encoding="utf-8")
    middleware = MemoryAgentMiddleware(
        memory_store_paths={"project": str(store)},
        min_turn_interval=3,
        min_seconds_between_runs=100.0,
        file_cooldown_seconds=100.0,
    )

    assert middleware._memory_files_recently_updated() is True
    assert "hello world" in middleware._message_anchor(
        _Msg("human", [{"text": "hello"}, "world"])  # type: ignore[arg-type]
    )

    import invincat_cli.memory_agent as memory_mod

    monkeypatch.setattr(
        memory_mod,
        "get_config",
        lambda: (_ for _ in ()).throw(RuntimeError("no config")),
    )
    assert middleware._resolve_thread_id() == "__default_thread__"

    messages = [_Msg("human", "one"), _Msg("ai", "two")]
    assert middleware._slice_incremental_messages("t", []) == []
    middleware._cursor_by_thread["t"] = 99
    assert middleware._slice_incremental_messages("t", messages) == messages
    middleware._cursor_by_thread["t"] = 1
    middleware._anchor_by_thread["t"] = "changed"
    assert middleware._slice_incremental_messages("t", messages) == messages
    middleware._anchor_by_thread["t"] = middleware._message_anchor(messages[0])
    assert middleware._slice_incremental_messages("t", messages) == [messages[1]]
    middleware._advance_cursor("empty", [])
    assert "empty" not in middleware._anchor_by_thread

    middleware._last_run_at = time.monotonic()
    assert (
        middleware._should_run_for_turn([_Msg("human", "ordinary"), _Msg("ai", "ok")])
        is False
    )
    middleware._min_seconds_between_runs = 0.0
    assert (
        middleware._should_run_for_turn([_Msg("human", "ordinary"), _Msg("ai", "ok")])
        is False
    )
    assert (
        middleware._should_run_for_turn(
            [_Msg("human", "Please remember this"), _Msg("ai", "ok")]
        )
        is True
    )


def test_aafter_agent_skip_and_cleanup_only_paths(monkeypatch: Any) -> None:
    middleware = MemoryAgentMiddleware()
    runtime = _Runtime()

    assert asyncio.run(middleware.aafter_agent({"messages": []}, runtime)) is None

    middleware._captured_model = object()
    assert (
        asyncio.run(
            middleware.aafter_agent(
                {"__interrupt__": [object()], "messages": [_Msg("human", "x")]},
                runtime,
            )
        )
        is None
    )
    assert (
        asyncio.run(
            middleware.aafter_agent({"messages": [_Msg("human", "x")]}, runtime)
        )
        is None
    )

    async def cleanup(
        *, thread_id: str, source_anchor: str
    ) -> tuple[None, None, list[str]]:
        return None, None, ["memory.json"]

    monkeypatch.setattr(middleware, "_cleanup_invalid_fact_stores", cleanup)
    monkeypatch.setattr(middleware, "_resolve_thread_id", lambda: "thread-cleanup-only")
    monkeypatch.setattr(middleware, "_should_run_for_turn", lambda _messages: False)
    messages = [
        _Msg("human", "Please remember later"),
        _Msg("ai", "Done.", tool_calls=[]),
    ]
    result = asyncio.run(middleware.aafter_agent({"messages": messages}, runtime))
    assert result == {
        "memory_contents": None,
        "_auto_memory_updated_paths": ["memory.json"],
    }

    middleware._cursor_by_thread["thread-cleanup-only"] = len(messages)
    middleware._anchor_by_thread["thread-cleanup-only"] = middleware._message_anchor(
        messages[-1]
    )
    monkeypatch.setattr(middleware, "_should_run_for_turn", lambda _messages: True)
    result = asyncio.run(middleware.aafter_agent({"messages": messages}, runtime))
    assert result == {
        "memory_contents": None,
        "_auto_memory_updated_paths": ["memory.json"],
    }


def test_aafter_agent_context_window_keeps_last_human(monkeypatch: Any) -> None:
    middleware = MemoryAgentMiddleware(context_messages=1)
    middleware._captured_model = object()
    captured: list[list[Any]] = []

    async def cleanup(
        *, thread_id: str, source_anchor: str
    ) -> tuple[None, None, list[str]]:
        return None, None, []

    async def safe_extract(
        _model: Any,
        messages: list[Any],
        **_kwargs: Any,
    ) -> list[str]:
        captured.append(messages)
        return []

    monkeypatch.setattr(middleware, "_cleanup_invalid_fact_stores", cleanup)
    monkeypatch.setattr(middleware, "_safe_extract_and_write", safe_extract)
    monkeypatch.setattr(middleware, "_resolve_thread_id", lambda: "thread-window")

    messages = [
        _Msg("human", "Please remember this preference"),
        _Msg("ai", "I will keep it.", tool_calls=[]),
        _Msg("ai", "Done.", tool_calls=[]),
    ]
    result = asyncio.run(middleware.aafter_agent({"messages": messages}, _Runtime()))

    assert result is None
    assert captured == [[messages[0], messages[-1]]]


def test_aafter_agent_runs_cleanup_even_for_trivial_turn(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    store = tmp_path / "memory_project.json"
    project_store = _new_store("project")
    invalid = _item("mem_p_000001", content="Old incorrect fact")
    invalid["reason"] = "该记忆与当前事实不一致，内容不准确。"
    project_store["items"].append(invalid)
    _write_memory_store(store, project_store)

    middleware = MemoryAgentMiddleware(
        memory_store_paths={"project": str(store)},
    )
    middleware._captured_model = object()
    monkeypatch.setattr(middleware, "_resolve_thread_id", lambda: "thread-trivial")

    messages = [_Msg("human", "收到"), _Msg("ai", "ok", tool_calls=[])]
    result = asyncio.run(middleware.aafter_agent({"messages": messages}, _Runtime()))

    assert result == {
        "memory_contents": None,
        "_auto_memory_updated_paths": [str(store.resolve())],
    }
    assert middleware._cursor_by_thread.get("thread-trivial") == len(messages)
    assert _read_memory_store(store, "project")["items"] == []


def test_unreadable_store_is_auto_recovered_before_extract(tmp_path: Path) -> None:
    store = tmp_path / "memory_project.json"
    store.write_text("{not-json", encoding="utf-8")

    middleware = MemoryAgentMiddleware(
        memory_store_paths={"project": str(store)},
    )

    before = store.read_text(encoding="utf-8")
    result = asyncio.run(
        middleware._extract_and_write(
            model=object(),
            messages=[_Msg("human", "remember this"), _Msg("ai", "ok", tool_calls=[])],
            thread_id="thread-3",
            source_anchor="a1",
        )
    )
    after = store.read_text(encoding="utf-8")

    assert result is None
    assert after != before
    reloaded = _read_memory_store(store, "project")
    assert reloaded.get("__read_error__") is None
    backups = list(tmp_path.glob("memory_project.json.corrupt.*.bak"))
    assert backups


def test_extract_deletes_existing_invalid_fact_even_when_model_noops(
    tmp_path: Path,
) -> None:
    store = tmp_path / "memory_project.json"
    project_store = _new_store("project")
    invalid = _item("mem_p_000001", content="Old incorrect fact")
    invalid["tier"] = "cold"
    invalid["score"] = 8
    invalid["reason"] = "这条记忆与当前事实不符，已被替代。"
    project_store["items"].append(invalid)
    _write_memory_store(store, project_store)

    middleware = MemoryAgentMiddleware(
        memory_store_paths={"project": str(store)},
    )
    written = asyncio.run(
        middleware._extract_and_write(
            model=_NoopMemoryModel(),
            messages=[
                _Msg("human", "记忆整理一下"),
                _Msg("ai", "ok", tool_calls=[]),
            ],
            thread_id="thread-cleanup",
            source_anchor="a1",
        )
    )

    assert written == [str(store.resolve())]
    reloaded = _read_memory_store(store, "project")
    assert reloaded["items"] == []


def test_extract_cleanup_is_written_before_model_failure(tmp_path: Path) -> None:
    store = tmp_path / "memory_project.json"
    project_store = _new_store("project")
    invalid = _item("mem_p_000001", content="Old incorrect fact")
    invalid["tier"] = "warm"
    invalid["score"] = 45
    invalid["reason"] = "该记忆不符合当前事实。"
    project_store["items"].append(invalid)
    _write_memory_store(store, project_store)

    middleware = MemoryAgentMiddleware(
        memory_store_paths={"project": str(store)},
    )
    written = asyncio.run(
        middleware._extract_and_write(
            model=_FailingMemoryModel(),
            messages=[
                _Msg("human", "memory cleanup"),
                _Msg("ai", "ok", tool_calls=[]),
            ],
            thread_id="thread-cleanup",
            source_anchor="a1",
        )
    )

    assert written == [str(store.resolve())]
    reloaded = _read_memory_store(store, "project")
    assert reloaded["items"] == []


def test_extract_cleanup_runs_when_model_returns_malformed_json(tmp_path: Path) -> None:
    store = tmp_path / "memory_project.json"
    project_store = _new_store("project")
    invalid = _item("mem_p_000001", content="Old incorrect fact")
    invalid["tier"] = "cold"
    invalid["score"] = 8
    invalid["reason"] = "Existing memory is contradicted by current facts."
    project_store["items"].append(invalid)
    _write_memory_store(store, project_store)

    middleware = MemoryAgentMiddleware(
        memory_store_paths={"project": str(store)},
    )
    written = asyncio.run(
        middleware._extract_and_write(
            model=_MalformedMemoryModel(),
            messages=[
                _Msg("human", "memory cleanup"),
                _Msg("ai", "ok", tool_calls=[]),
            ],
            thread_id="thread-cleanup",
            source_anchor="a1",
        )
    )

    assert written == [str(store.resolve())]
    reloaded = _read_memory_store(store, "project")
    assert reloaded["items"] == []


def test_short_memory_signal_is_not_trivial() -> None:
    msgs = [_Msg("human", "记住用中文回答"), _Msg("ai", "好的", tool_calls=[])]
    assert _is_trivial_turn(msgs) is False


def test_invalid_schema_store_sets_read_error(tmp_path: Path) -> None:
    store_path = tmp_path / "memory_project.json"
    store_path.write_text('{"scope":"project","items":"bad"}', encoding="utf-8")
    store = _read_memory_store(store_path, "project")
    assert store["scope"] == "project"
    assert store.get("__read_error__") is True


def test_invalid_utf8_store_sets_read_error(tmp_path: Path) -> None:
    store_path = tmp_path / "memory_project.json"
    store_path.write_bytes(b"\xff\xfe\x00")
    store = _read_memory_store(store_path, "project")
    assert store["scope"] == "project"
    assert store.get("__read_error__") is True


def test_schema_invalid_store_is_auto_recovered_before_extract(tmp_path: Path) -> None:
    store = tmp_path / "memory_project.json"
    store.write_text('{"scope":"project","items":"bad"}', encoding="utf-8")

    middleware = MemoryAgentMiddleware(
        memory_store_paths={"project": str(store)},
    )
    before = store.read_text(encoding="utf-8")
    result = asyncio.run(
        middleware._extract_and_write(
            model=object(),
            messages=[_Msg("human", "remember this"), _Msg("ai", "ok", tool_calls=[])],
            thread_id="thread-4",
            source_anchor="a1",
        )
    )
    after = store.read_text(encoding="utf-8")

    assert result is None
    assert after != before
    reloaded = _read_memory_store(store, "project")
    assert reloaded.get("__read_error__") is None
    backups = list(tmp_path.glob("memory_project.json.corrupt.*.bak"))
    assert backups


def test_load_or_recover_store_recovers_unreadable_store_with_backup(
    tmp_path: Path,
) -> None:
    store = tmp_path / "memory_project.json"
    store.write_text("{bad-json", encoding="utf-8")

    middleware = MemoryAgentMiddleware(
        memory_store_paths={"project": str(store)},
    )

    recovered = middleware._load_or_recover_store("project", "t1", "a1")
    assert recovered is not None
    assert recovered.get("__read_error__") is None
    assert recovered["items"] == []
    # Store was rewritten to a healthy JSON payload.
    reloaded = _read_memory_store(store, "project")
    assert reloaded.get("__read_error__") is None
    assert reloaded["items"] == []
    backups = list(tmp_path.glob("memory_project.json.corrupt.*.bak"))
    assert backups


def test_short_ack_is_trivial() -> None:
    msgs = [_Msg("human", "收到"), _Msg("ai", "ok", tool_calls=[])]
    assert _is_trivial_turn(msgs) is True


def test_explicit_memory_request_detection() -> None:
    assert _is_explicit_memory_request("Please remember this preference.") is True
    assert _is_explicit_memory_request("请记住这条规则") is True
    assert _is_explicit_memory_request("thanks") is False


def test_target_language_detection() -> None:
    assert _detect_target_language("请记住以后用中文总结这个项目") == "Chinese"
    assert _detect_target_language("Please remember this preference") == "English"


def test_extract_includes_target_language_instruction_for_chinese_turn(
    tmp_path: Path,
) -> None:
    store = tmp_path / "memory_project.json"
    model = _CapturingMemoryModel()
    middleware = MemoryAgentMiddleware(
        memory_store_paths={"project": str(store)},
    )

    written = asyncio.run(
        middleware._extract_and_write(
            model=model,
            messages=[
                _Msg("human", "请记住：这个项目提交前必须运行 pytest。"),
                _Msg("ai", "好的。", tool_calls=[]),
            ],
            thread_id="thread-lang",
            source_anchor="a1",
        )
    )

    assert written == []
    assert model.messages
    final_instruction = model.messages[-1].content
    assert "target_language: Chinese" in final_instruction
    assert "must use target_language" in final_instruction


def test_extract_passes_plain_transcript_instead_of_native_tool_call_messages(
    tmp_path: Path,
) -> None:
    store = tmp_path / "memory_project.json"
    model = _CapturingMemoryModel()
    middleware = MemoryAgentMiddleware(
        memory_store_paths={"project": str(store)},
    )
    tool_call = {
        "name": "read_file",
        "args": {"file_path": "/tmp/example.py", "offset": 10, "limit": 5},
        "id": "call_1",
    }

    written = asyncio.run(
        middleware._extract_and_write(
            model=model,
            messages=[
                _Msg("human", "Please remember: inspect tool calls as context."),
                _Msg("ai", "I will inspect the file.", tool_calls=[tool_call]),
                _Msg("tool", "file contents"),
            ],
            thread_id="thread-transcript",
            source_anchor="a1",
        )
    )

    assert written == []
    assert len(model.messages) == 3
    assert all(not isinstance(message, _Msg) for message in model.messages)
    transcript = model.messages[1].content
    assert "conversation_transcript" in transcript
    assert "assistant_tool_calls_json" in transcript
    assert '"read_file"' in transcript


def test_system_prompt_contains_conservative_policy_contract() -> None:
    lowered = _SYSTEM_PROMPT.lower()
    assert "memory curator" in lowered
    assert "prefer project" in lowered
    assert "do not store" in lowered
    assert "decision order" in lowered
    assert "prefer existing-item ops before create" in lowered
    assert "never create semantic duplicates" in lowered
    assert "at most one op per item id" in lowered
    assert "do not treat confirmation as noise" in lowered


def test_system_prompt_forbids_metadata_only_fact_corrections() -> None:
    lowered = " ".join(_SYSTEM_PROMPT.lower().split())
    assert "both change only priority metadata" in lowered
    assert "do not use either to record a changed fact" in lowered
    assert "use update with" in lowered
    assert "corrected content" in lowered
    assert "delete the old item and create the replacement" in lowered
    assert '"op":"delete"' in lowered


def test_system_prompt_encourages_rescore_for_confirmed_existing_items() -> None:
    lowered = " ".join(_SYSTEM_PROMPT.lower().split())
    assert "directly confirmed by this turn" in lowered
    assert "prefer rescore over noop" in lowered
    assert "fresh confirming evidence" in lowered
    assert "project item confirmed without content changes" in lowered


def test_recover_corrupt_store_without_legacy_fallback(tmp_path: Path) -> None:
    store = tmp_path / "memory_project.json"
    store.write_text("{bad-json", encoding="utf-8")

    middleware = MemoryAgentMiddleware(
        memory_store_paths={"project": str(store)},
    )
    recovered = middleware._load_or_recover_store("project", "t1", "a1")
    assert recovered is not None
    assert recovered.get("__read_error__") is None
    assert recovered["items"] == []

    reloaded = _read_memory_store(store, "project")
    assert reloaded.get("__read_error__") is None
    assert reloaded["items"] == []


def test_backup_corrupt_store_handles_invalid_utf8(tmp_path: Path) -> None:
    store = tmp_path / "memory_project.json"
    store.write_bytes(b"\xff\xfe\x00")
    backup = _backup_corrupt_store(store)
    assert backup is not None
    assert backup.exists()
