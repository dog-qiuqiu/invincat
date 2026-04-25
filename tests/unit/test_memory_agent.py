"""Unit tests for structured memory-agent store operations."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


from invincat_cli.memory_agent import (
    _SYSTEM_PROMPT,
    DEFAULT_SCORE,
    DEFAULT_TIER,
    COLD_THRESHOLD,
    HOT_THRESHOLD,
    MAX_SNAPSHOT_ITEMS_PER_SCOPE,
    MAX_ITEM_CONTENT_CHARS,
    MemoryAgentMiddleware,
    _build_invalid_fact_cleanup_operations,
    _build_memory_snapshot,
    _derive_tier_from_score,
    _apply_operations,
    _atomic_write_text,
    _backup_corrupt_store,
    _is_trivial_turn,
    _is_explicit_memory_request,
    _new_store,
    _normalize_score,
    _normalize_and_validate_operations,
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
        "score_reason": "",
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
            "score_reason": "Explicit stable preference.",
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
    assert new_project["items"][0]["content"] == "Backend API fields should be snake_case."
    assert new_project["items"][0]["tier"] == "hot"
    assert new_project["items"][0]["score"] == 88


def test_archive_existing_item() -> None:
    project_store = _new_store("project")
    for idx in range(5):
        project_store["items"].append(_item(f"mem_p_{idx+1:06d}", content=f"Rule {idx}"))
    _, new_project, changed = _apply_operations(
        None,
        project_store,
        [{"op": "archive", "scope": "project", "id": "mem_p_000001", "reason": "superseded"}],
        thread_id="t1",
        source_anchor="a1",
        now_iso="2026-04-22T10:10:00Z",
    )
    assert new_project is not None
    assert changed == ["project"]
    assert new_project["items"][0]["status"] == "archived"
    assert new_project["items"][0]["archived_at"] == "2026-04-22T10:10:00Z"


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
        memory_paths=[],
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


def test_removal_ratio_guard_blocks_excessive_deletes() -> None:
    # Ratio guard fires for delete ops without a contradiction reason.
    project_store = _new_store("project")
    for idx in range(5):
        project_store["items"].append(_item(f"mem_p_{idx+1:06d}", content=f"Rule {idx}"))
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
    assert changed == []
    assert all(item["status"] == "active" for item in new_project["items"])


def test_removal_ratio_guard_does_not_block_archive() -> None:
    # Archive is reversible — it is intentionally exempt from the ratio guard so
    # proactive confidence-retirement is never blocked.
    project_store = _new_store("project")
    for idx in range(5):
        project_store["items"].append(_item(f"mem_p_{idx+1:06d}", content=f"Rule {idx}"))
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


def test_removal_ratio_guard_allows_contradiction_deletes() -> None:
    # Contradiction-based deletes must NOT be blocked by the ratio guard.
    # The model follows "delete old + create new" for contradicted facts;
    # blocking the delete while allowing the create would leave both the
    # contradicted and the replacement memory in the store simultaneously.
    project_store = _new_store("project")
    for idx in range(5):
        project_store["items"].append(_item(f"mem_p_{idx+1:06d}", content=f"Rule {idx}"))
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
                "score_reason": "User confirmed migration from Poetry to uv.",
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


def test_atomic_write_and_whitelist_authorization(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    store = tmp_path / "memory_project.json"
    outsider = tmp_path / "other.txt"
    middleware = MemoryAgentMiddleware(
        memory_paths=[str(agents)],
        memory_store_paths={"project": str(store)},
    )
    _atomic_write_text(store, "{}")
    assert store.read_text(encoding="utf-8") == "{}"
    assert middleware._is_authorized_path(store)
    assert not middleware._is_authorized_path(agents)
    assert not middleware._is_authorized_path(outsider)


def test_operation_validation_contract() -> None:
    payload = {
        "operations": [
            {"op": "create", "scope": "project", "section": "S", "content": "C"},
            {"op": "update", "scope": "project", "id": "mem_p_000001", "content": ""},
            {"op": "archive", "scope": "project", "id": "mem_p_000001"},
            {"op": "delete", "scope": "project", "id": "mem_p_000002", "reason": "wrong"},
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
                    "score_reason": "The memory is no longer accurate.",
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
                "score_reason": "Stale",
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
                    "score_reason": "Existing memory is contradicted by current facts.",
                }
            ]
        },
        rescoring_candidate_ids_by_scope={"project": {"mem_p_000001"}},
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
                "score_reason": "History only",
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
                    "score_reason": "这条记忆与当前事实不符，已被替代。",
                }
            ]
        },
        rescoring_candidate_ids_by_scope={"project": {"mem_p_000001"}},
    )

    assert ops == [
        {
            "op": "delete",
            "scope": "project",
            "id": "mem_p_000001",
            "reason": "这条记忆与当前事实不符，已被替代。",
        }
    ]


def test_invalid_fact_cleanup_scans_full_store_beyond_snapshot_cap() -> None:
    project_store = _new_store("project")
    for idx in range(MAX_SNAPSHOT_ITEMS_PER_SCOPE + 20):
        project_store["items"].append(
            _item(
                f"mem_p_{idx + 1:06d}",
                content=f"Valid rule {idx}",
            )
        )
    hidden_invalid_id = f"mem_p_{MAX_SNAPSHOT_ITEMS_PER_SCOPE + 21:06d}"
    invalid = _item(hidden_invalid_id, content="Old incorrect fact")
    invalid["tier"] = "cold"
    invalid["score"] = 5
    invalid["score_reason"] = "Existing memory is contradicted by current facts."
    project_store["items"].append(invalid)

    snapshot = _build_memory_snapshot(
        None,
        project_store,
        conversation="unrelated turn",
    )
    assert all(
        item["id"] != hidden_invalid_id
        for item in snapshot["project"]["items"]
    )

    cleanup = _build_invalid_fact_cleanup_operations(None, project_store)
    assert cleanup == [
        {
            "op": "delete",
            "scope": "project",
            "id": hidden_invalid_id,
            "reason": "Existing memory is contradicted by current facts.",
            "_cleanup": True,
        }
    ]


def test_invalid_fact_cleanup_deletes_warm_item_when_reason_is_clear() -> None:
    project_store = _new_store("project")
    invalid = _item("mem_p_000001", content="Old incorrect fact")
    invalid["tier"] = "warm"
    invalid["score"] = 45
    invalid["score_reason"] = "该记忆与当前事实不一致，内容不准确。"
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


def test_old_schema_items_are_backfilled_with_default_tier_score(tmp_path: Path) -> None:
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
    ops = _normalize_and_validate_operations(
        payload,
        rescoring_candidate_ids_by_scope={"project": {"mem_p_000001"}},
    )
    assert ops[0]["score"] == 100
    assert ops[1]["score"] == 0
    assert _derive_tier_from_score(_normalize_score(88)) == "hot"


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


def test_memory_snapshot_caps_items_per_scope() -> None:
    project_store = _new_store("project")
    for idx in range(MAX_SNAPSHOT_ITEMS_PER_SCOPE + 20):
        project_store["items"].append(
            _item(
                f"mem_p_{idx + 1:06d}",
                content=f"Rule {idx}",
            )
        )
    snapshot = _build_memory_snapshot(
        None,
        project_store,
        conversation="use uv and keep tests green",
    )
    project_snapshot = snapshot["project"]
    assert isinstance(project_snapshot, dict)
    assert len(project_snapshot["items"]) == MAX_SNAPSHOT_ITEMS_PER_SCOPE


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
    def __init__(self, msg_type: str, content: str, *, tool_calls: list[Any] | None = None) -> None:
        self.type = msg_type
        self.content = content
        self.tool_calls = tool_calls if tool_calls is not None else []


class _Runtime:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def stream_writer(self, payload: dict[str, Any]) -> None:
        self.events.append(payload)


class _NoopMemoryModel:
    def bind(self, **_kwargs: Any) -> "_NoopMemoryModel":
        return self

    async def ainvoke(self, *_args: Any, **_kwargs: Any) -> Any:
        return type("Response", (), {"content": '{"operations": [{"op": "noop"}]}'})()


class _MalformedMemoryModel:
    def bind(self, **_kwargs: Any) -> "_MalformedMemoryModel":
        return self

    async def ainvoke(self, *_args: Any, **_kwargs: Any) -> Any:
        return type("Response", (), {"content": "not json"})()


class _FailingMemoryModel:
    def bind(self, **_kwargs: Any) -> "_FailingMemoryModel":
        return self

    async def ainvoke(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("model unavailable")


def test_aafter_agent_emits_status_and_advances_cursor(monkeypatch: Any) -> None:
    middleware = MemoryAgentMiddleware(memory_paths=["/tmp/AGENTS.md"])
    middleware._captured_model = object()
    runtime = _Runtime()

    async def _fake_safe_extract_and_write(*args: Any, **kwargs: Any) -> list[str]:
        return []

    monkeypatch.setattr(middleware, "_safe_extract_and_write", _fake_safe_extract_and_write)
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


def test_aafter_agent_does_not_advance_cursor_on_extract_failure(monkeypatch: Any) -> None:
    middleware = MemoryAgentMiddleware(memory_paths=["/tmp/AGENTS.md"])
    middleware._captured_model = object()
    runtime = _Runtime()

    async def _fake_safe_extract_and_write(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(middleware, "_safe_extract_and_write", _fake_safe_extract_and_write)
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


def test_aafter_agent_runs_cleanup_even_for_trivial_turn(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    store = tmp_path / "memory_project.json"
    project_store = _new_store("project")
    invalid = _item("mem_p_000001", content="Old incorrect fact")
    invalid["score_reason"] = "该记忆与当前事实不一致，内容不准确。"
    project_store["items"].append(invalid)
    _write_memory_store(store, project_store)

    middleware = MemoryAgentMiddleware(
        memory_paths=[],
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
        memory_paths=[],
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


def test_extract_deletes_existing_invalid_fact_even_when_model_noops(tmp_path: Path) -> None:
    store = tmp_path / "memory_project.json"
    project_store = _new_store("project")
    invalid = _item("mem_p_000001", content="Old incorrect fact")
    invalid["tier"] = "cold"
    invalid["score"] = 8
    invalid["score_reason"] = "这条记忆与当前事实不符，已被替代。"
    project_store["items"].append(invalid)
    _write_memory_store(store, project_store)

    middleware = MemoryAgentMiddleware(
        memory_paths=[],
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
    invalid["score_reason"] = "该记忆不符合当前事实。"
    project_store["items"].append(invalid)
    _write_memory_store(store, project_store)

    middleware = MemoryAgentMiddleware(
        memory_paths=[],
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
    invalid["score_reason"] = "Existing memory is contradicted by current facts."
    project_store["items"].append(invalid)
    _write_memory_store(store, project_store)

    middleware = MemoryAgentMiddleware(
        memory_paths=[],
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


def test_format_messages_includes_project_tool_evidence() -> None:
    tool = _Msg(
        "tool",
        "pyproject.toml: [tool.ruff] line-length = 100; lint must run before commit.",
    )
    tool.name = "read_file"
    rendered = MemoryAgentMiddleware._format_messages(
        [
            _Msg("human", "Please remember project conventions."),
            _Msg("ai", "I checked the config.", tool_calls=[]),
            tool,
        ]
    )

    assert "tool_evidence:" in rendered
    assert "tool[read_file]:" in rendered
    assert "line-length = 100" in rendered


def test_format_messages_skips_non_informative_tool_output() -> None:
    tool = _Msg("tool", "Command exited with code 0.")
    tool.name = "execute"
    rendered = MemoryAgentMiddleware._format_messages(
        [
            _Msg("human", "ok"),
            _Msg("ai", "Done.", tool_calls=[]),
            tool,
        ]
    )

    assert "tool_evidence:" not in rendered


def test_format_messages_respects_tool_whitelist() -> None:
    tool = _Msg("tool", "Lint must run before commit and tests must pass.")
    tool.name = "web_search"
    rendered = MemoryAgentMiddleware._format_messages(
        [
            _Msg("human", "remember project rules"),
            _Msg("ai", "ok", tool_calls=[]),
            tool,
        ]
    )

    assert "tool_evidence:" not in rendered


def test_format_messages_redacts_sensitive_absolute_paths() -> None:
    tool = _Msg(
        "tool",
        "Found rule in /Users/alice/work/repo/pyproject.toml: lint must run before commit.",
    )
    tool.name = "read_file"
    rendered = MemoryAgentMiddleware._format_messages(
        [
            _Msg("human", "remember project conventions"),
            _Msg("ai", "ok", tool_calls=[]),
            tool,
        ]
    )

    assert "<ABS_PATH>" in rendered
    assert "/Users/alice/work/repo/pyproject.toml" not in rendered


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
        memory_paths=[],
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


def test_load_or_recover_store_recovers_unreadable_store_with_backup(tmp_path: Path) -> None:
    store = tmp_path / "memory_project.json"
    store.write_text("{bad-json", encoding="utf-8")

    middleware = MemoryAgentMiddleware(
        memory_paths=[],
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


def test_system_prompt_contains_conservative_policy_contract() -> None:
    lowered = _SYSTEM_PROMPT.lower()
    assert "conservative memory curator" in lowered
    assert "if ambiguous, prefer project or noop" in lowered
    assert "do not store" in lowered
    assert "first non-whitespace character must be \"{\"" in lowered


def test_system_prompt_forbids_metadata_only_fact_corrections() -> None:
    lowered = _SYSTEM_PROMPT.lower()
    assert "rescore/retier only change priority metadata" in lowered
    assert "do not use them to record a changed fact" in lowered
    assert "use update with" in lowered
    assert "corrected content" in lowered
    assert "delete the old item and create the replacement" in lowered
    assert '"op": "delete"' in lowered


def test_recover_corrupt_store_without_legacy_fallback(tmp_path: Path) -> None:
    store = tmp_path / "memory_project.json"
    store.write_text("{bad-json", encoding="utf-8")

    middleware = MemoryAgentMiddleware(
        memory_paths=[],
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
