"""Structured memory store normalization and mutation helpers."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from invincat_cli.memory.store_core import (
    _ALLOWED_CONFIDENCE as _ALLOWED_CONFIDENCE,
)
from invincat_cli.memory.store_core import (
    _ALLOWED_OPS as _ALLOWED_OPS,
)
from invincat_cli.memory.store_core import (
    _ALLOWED_SCOPE as _ALLOWED_SCOPE,
)
from invincat_cli.memory.store_core import (
    _ALLOWED_STATUS as _ALLOWED_STATUS,
)
from invincat_cli.memory.store_core import (
    _ALLOWED_TIER as _ALLOWED_TIER,
)
from invincat_cli.memory.store_core import (
    _INVALID_FACT_REASON_RE as _INVALID_FACT_REASON_RE,
)
from invincat_cli.memory.store_core import (
    _ITEM_ID_PATTERNS as _ITEM_ID_PATTERNS,
)
from invincat_cli.memory.store_core import (
    _ITEM_ID_PREFIX as _ITEM_ID_PREFIX,
)
from invincat_cli.memory.store_core import (
    _MAX_OUTPUT_TOKENS as _MAX_OUTPUT_TOKENS,
)
from invincat_cli.memory.store_core import (
    _MEMORY_SIGNAL_RE as _MEMORY_SIGNAL_RE,
)
from invincat_cli.memory.store_core import (
    COLD_THRESHOLD as COLD_THRESHOLD,
)
from invincat_cli.memory.store_core import (
    DEFAULT_SCORE as DEFAULT_SCORE,
)
from invincat_cli.memory.store_core import (
    DEFAULT_TIER as DEFAULT_TIER,
)
from invincat_cli.memory.store_core import (
    HOT_THRESHOLD as HOT_THRESHOLD,
)
from invincat_cli.memory.store_core import (
    MAX_ARCHIVED_ITEMS_PER_SCOPE as MAX_ARCHIVED_ITEMS_PER_SCOPE,
)
from invincat_cli.memory.store_core import (
    MAX_HOT_ITEMS_PER_SCOPE as MAX_HOT_ITEMS_PER_SCOPE,
)
from invincat_cli.memory.store_core import (
    MAX_ITEM_CONTENT_CHARS as MAX_ITEM_CONTENT_CHARS,
)
from invincat_cli.memory.store_core import (
    MAX_REASON_CHARS as MAX_REASON_CHARS,
)
from invincat_cli.memory.store_core import (
    MAX_SECTION_NAME_CHARS as MAX_SECTION_NAME_CHARS,
)
from invincat_cli.memory.store_core import (
    MAX_WARM_ITEMS_PER_SCOPE as MAX_WARM_ITEMS_PER_SCOPE,
)
from invincat_cli.memory.store_core import (
    _align_score_to_tier as _align_score_to_tier,
)
from invincat_cli.memory.store_core import (
    _derive_tier_from_score as _derive_tier_from_score,
)
from invincat_cli.memory.store_core import (
    _detect_target_language as _detect_target_language,
)
from invincat_cli.memory.store_core import (
    _env_float as _env_float,
)
from invincat_cli.memory.store_core import (
    _env_int as _env_int,
)
from invincat_cli.memory.store_core import (
    _format_call_messages_for_log as _format_call_messages_for_log,
)
from invincat_cli.memory.store_core import (
    _format_messages_for_memory_transcript as _format_messages_for_memory_transcript,
)
from invincat_cli.memory.store_core import (
    _is_explicit_memory_request as _is_explicit_memory_request,
)
from invincat_cli.memory.store_core import (
    _is_task_complete as _is_task_complete,
)
from invincat_cli.memory.store_core import (
    _is_trivial_turn as _is_trivial_turn,
)
from invincat_cli.memory.store_core import (
    _iso_now as _iso_now,
)
from invincat_cli.memory.store_core import (
    _last_human_text as _last_human_text,
)
from invincat_cli.memory.store_core import (
    _message_content_to_text as _message_content_to_text,
)
from invincat_cli.memory.store_core import (
    _new_store as _new_store,
)
from invincat_cli.memory.store_core import (
    _normalize_confidence as _normalize_confidence,
)
from invincat_cli.memory.store_core import (
    _normalize_hash as _normalize_hash,
)
from invincat_cli.memory.store_core import (
    _normalize_reason as _normalize_reason,
)
from invincat_cli.memory.store_core import (
    _normalize_scope as _normalize_scope,
)
from invincat_cli.memory.store_core import (
    _normalize_score as _normalize_score,
)
from invincat_cli.memory.store_core import (
    _normalize_status as _normalize_status,
)
from invincat_cli.memory.store_core import (
    _normalize_text as _normalize_text,
)
from invincat_cli.memory.store_core import (
    _normalize_tier as _normalize_tier,
)
from invincat_cli.memory.store_core import (
    _raw_reason as _raw_reason,
)
from invincat_cli.memory.store_core import (
    _reason_implies_invalid_fact as _reason_implies_invalid_fact,
)
from invincat_cli.memory.store_mutations import (
    _apply_operations as _apply_operations,
)
from invincat_cli.memory.store_mutations import (
    _build_archived_overflow_operations as _build_archived_overflow_operations,
)
from invincat_cli.memory.store_mutations import (
    _build_invalid_fact_cleanup_operations as _build_invalid_fact_cleanup_operations,
)
from invincat_cli.memory.store_mutations import (
    _find_item as _find_item,
)
from invincat_cli.memory.store_mutations import (
    _next_memory_id as _next_memory_id,
)
from invincat_cli.memory.store_validation import (
    _normalize_and_validate_operations as _normalize_and_validate_operations,
)

logger = logging.getLogger(__name__)


def _read_memory_store(path: Path, scope: str) -> dict[str, Any]:
    """Read memory store from JSON file or return a new validated store."""

    def _read_error_store(target_scope: str) -> dict[str, Any]:
        store = _new_store(target_scope)
        store["__read_error__"] = True
        return store

    if not path.exists():
        return _new_store(scope)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("Memory store unreadable at %s; marking as read-error", path)
        return _read_error_store(scope)

    if not isinstance(data, dict):
        logger.warning("Memory store schema invalid at %s; marking as read-error", path)
        return _read_error_store(scope)

    normalized_scope = _normalize_scope(data.get("scope")) or scope
    if (
        isinstance(data.get("scope"), str)
        and _normalize_scope(data.get("scope")) is None
    ):
        logger.warning("Memory store scope invalid at %s; marking as read-error", path)
        return _read_error_store(scope)
    store = {
        "version": 1,
        "scope": normalized_scope,
        "items": [],
    }

    items = data.get("items", [])
    if not isinstance(items, list):
        logger.warning("Memory store items invalid at %s; marking as read-error", path)
        return _read_error_store(normalized_scope)

    for raw in items:
        if not isinstance(raw, dict):
            continue
        item_scope = _normalize_scope(raw.get("scope")) or normalized_scope
        if item_scope != normalized_scope:
            continue
        item_id = raw.get("id")
        if not isinstance(item_id, str):
            continue
        if not _ITEM_ID_PATTERNS[normalized_scope].match(item_id):
            continue
        section = _normalize_text(
            raw.get("section", ""), max_chars=MAX_SECTION_NAME_CHARS
        )
        content = _normalize_text(
            raw.get("content", ""), max_chars=MAX_ITEM_CONTENT_CHARS
        )
        if not section or not content:
            continue
        status = _normalize_status(raw.get("status"))
        created_at = (
            raw.get("created_at")
            if isinstance(raw.get("created_at"), str)
            else _iso_now()
        )
        updated_at = (
            raw.get("updated_at")
            if isinstance(raw.get("updated_at"), str)
            else created_at
        )
        archived_at = (
            raw.get("archived_at") if isinstance(raw.get("archived_at"), str) else None
        )
        source_thread_id = (
            raw.get("source_thread_id")
            if isinstance(raw.get("source_thread_id"), str)
            else "__default_thread__"
        )
        source_anchor = (
            raw.get("source_anchor")
            if isinstance(raw.get("source_anchor"), str)
            else ""
        )
        confidence = _normalize_confidence(raw.get("confidence"), default="medium")
        score = _normalize_score(raw.get("score"), default=DEFAULT_SCORE)
        tier = _normalize_tier(raw.get("tier"), default=_derive_tier_from_score(score))
        reason = _normalize_reason(_raw_reason(raw))
        last_scored_at = (
            raw.get("last_scored_at")
            if isinstance(raw.get("last_scored_at"), str)
            else (
                updated_at if isinstance(updated_at, str) and updated_at else created_at
            )
        )

        store["items"].append(
            {
                "id": item_id,
                "scope": normalized_scope,
                "section": section,
                "content": content,
                "status": status,
                "created_at": created_at,
                "updated_at": updated_at,
                "archived_at": archived_at if status == "archived" else None,
                "source_thread_id": source_thread_id,
                "source_anchor": source_anchor,
                "confidence": confidence,
                "tier": tier,
                "score": score,
                "reason": reason,
                "last_scored_at": last_scored_at,
                "norm_hash": _normalize_hash(section, content),
            }
        )

    return store


def _write_memory_store(path: Path, store: dict[str, Any]) -> None:
    # Internal guard flags are runtime-only and must not be persisted.
    write_store = {k: v for k, v in store.items() if not str(k).startswith("__")}
    payload = json.dumps(write_store, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(path, payload)


def _build_memory_snapshot(
    user_store: dict[str, Any] | None,
    project_store: dict[str, Any] | None,
) -> dict[str, Any]:
    def _scope_snapshot(store: dict[str, Any] | None) -> dict[str, Any]:
        if store is None:
            return {"items": []}
        items: list[dict[str, Any]] = []
        for item in store.get("items", []):
            if not isinstance(item, dict):
                continue
            score = _normalize_score(item.get("score"), default=DEFAULT_SCORE)
            tier = _normalize_tier(
                item.get("tier"),
                default=_derive_tier_from_score(score),
            )
            items.append(
                {
                    "id": item.get("id"),
                    "section": item.get("section"),
                    "content": item.get("content"),
                    "status": item.get("status"),
                    "tier": tier,
                    "score": score,
                    "reason": _normalize_reason(_raw_reason(item)),
                    "created_at": item.get("created_at"),
                    "last_scored_at": item.get("last_scored_at"),
                }
            )
        items.sort(
            key=lambda x: (
                str(x.get("status", "")) != "active",
                {"hot": 0, "warm": 1, "cold": 2}.get(str(x.get("tier", "warm")), 1),
                -_normalize_score(x.get("score")),
                str(x.get("section", "")).casefold(),
                str(x.get("id", "")),
            )
        )
        return {"items": items}

    return {
        "user": _scope_snapshot(user_store),
        "project": _scope_snapshot(project_store),
    }










def _atomic_write_text(path: Path, content: str) -> None:
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


def _backup_corrupt_store(path: Path) -> Path | None:
    """Best-effort backup for unreadable store files before auto-recovery."""
    if not path.exists():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_name(f"{path.name}.corrupt.{stamp}.bak")
    try:
        raw = path.read_bytes()
        # Preserve recoverability even when the original store has invalid UTF-8.
        _atomic_write_text(backup_path, raw.decode("utf-8", errors="replace"))
        return backup_path
    except (OSError, UnicodeDecodeError):
        logger.warning(
            "Memory agent: failed to back up unreadable store %s", path, exc_info=True
        )
        return None
