"""Mutation helpers for structured memory stores."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from invincat_cli.memory.store_core import (
    _ALLOWED_SCOPE,
    _ITEM_ID_PATTERNS,
    _ITEM_ID_PREFIX,
    DEFAULT_SCORE,
    MAX_ARCHIVED_ITEMS_PER_SCOPE,
    _align_score_to_tier,
    _derive_tier_from_score,
    _new_store,
    _normalize_confidence,
    _normalize_hash,
    _normalize_reason,
    _normalize_score,
    _normalize_tier,
    _raw_reason,
    _reason_implies_invalid_fact,
)


def _next_memory_id(store: dict[str, Any], scope: str) -> str:
    pattern = _ITEM_ID_PATTERNS[scope]
    max_index = 0
    for item in store.get("items", []):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str):
            continue
        match = pattern.match(item_id)
        if not match:
            continue
        max_index = max(max_index, int(match.group(1)))
    return f"{_ITEM_ID_PREFIX[scope]}{max_index + 1:06d}"

def _find_item(store: dict[str, Any] | None, item_id: str) -> dict[str, Any] | None:
    if store is None:
        return None
    for item in store.get("items", []):
        if isinstance(item, dict) and item.get("id") == item_id:
            return item
    return None

def _build_invalid_fact_cleanup_operations(
    user_store: dict[str, Any] | None,
    project_store: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build deterministic deletes for active memories already marked invalid.

    This scans the full store independently of the model snapshot, so previously
    mis-scored invalid facts do not linger just because the model returned noop.
    """
    operations: list[dict[str, Any]] = []
    for scope, store in (("user", user_store), ("project", project_store)):
        if store is None:
            continue
        for item in store.get("items", []):
            if not isinstance(item, dict) or item.get("status") != "active":
                continue
            reason = _normalize_reason(_raw_reason(item))
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id.strip():
                continue
            if _reason_implies_invalid_fact(reason):
                operations.append(
                    {
                        "op": "delete",
                        "scope": scope,
                        "id": item_id.strip(),
                        "reason": reason or "Existing memory is no longer valid.",
                        "_cleanup": True,
                    }
                )
    return operations

def _build_archived_overflow_operations(
    user_store: dict[str, Any] | None,
    project_store: dict[str, Any] | None,
    *,
    max_archived: int = MAX_ARCHIVED_ITEMS_PER_SCOPE,
) -> list[dict[str, Any]]:
    """Physically delete oldest archived items when the archived cap is exceeded.

    Active items are never touched here — this only manages archived capacity so
    the store doesn't grow unboundedly from proactive archival over time.
    """
    operations: list[dict[str, Any]] = []
    for scope, store in (("user", user_store), ("project", project_store)):
        if store is None:
            continue
        archived = [
            item
            for item in store.get("items", [])
            if isinstance(item, dict) and item.get("status") == "archived"
        ]
        if len(archived) <= max_archived:
            continue
        overflow = len(archived) - max_archived
        oldest = sorted(
            archived,
            key=lambda item: (
                str(item.get("archived_at") or item.get("updated_at", "")),
                str(item.get("id", "")),
            ),
        )[:overflow]
        for item in oldest:
            item_id = item.get("id")
            if isinstance(item_id, str) and item_id.strip():
                operations.append(
                    {
                        "op": "delete",
                        "scope": scope,
                        "id": item_id.strip(),
                        "reason": "Archived item removed: archived capacity exceeded.",
                        "_cleanup": True,
                    }
                )
    return operations

def _apply_operations(
    user_store: dict[str, Any] | None,
    project_store: dict[str, Any] | None,
    operations: list[dict[str, Any]],
    *,
    thread_id: str,
    source_anchor: str,
    now_iso: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    user_store = deepcopy(user_store) if user_store is not None else None
    project_store = deepcopy(project_store) if project_store is not None else None
    changed_scopes: set[str] = set()

    # Conflict guard: same id touched more than once in one batch.
    id_touch_count: dict[str, int] = {}
    for op in operations:
        item_id = op.get("id")
        if isinstance(item_id, str) and item_id:
            id_touch_count[item_id] = id_touch_count.get(item_id, 0) + 1
    conflicted_ids = {item_id for item_id, count in id_touch_count.items() if count > 1}
    invalid_fact_delete_ids = {
        str(op.get("id"))
        for op in operations
        if op.get("op") == "delete"
        and isinstance(op.get("id"), str)
        and _reason_implies_invalid_fact(str(op.get("reason") or ""))
    }

    def _get_or_create_store(scope: str) -> dict[str, Any]:
        nonlocal user_store, project_store
        if scope == "user":
            if user_store is None:
                user_store = _new_store("user")
            return user_store
        if project_store is None:
            project_store = _new_store("project")
        return project_store

    for op in operations:
        op_name = op.get("op")
        if op_name == "noop":
            continue
        scope = op.get("scope")
        if not isinstance(scope, str) or scope not in _ALLOWED_SCOPE:
            continue

        store = _get_or_create_store(scope)
        if op_name == "create":
            section = str(op["section"])
            content = str(op["content"])
            duplicate = any(
                item.get("status") == "active"
                and item.get("content", "").strip() == content
                for item in store.get("items", [])
                if isinstance(item, dict)
            )
            if duplicate:
                continue
            item_id = _next_memory_id(store, scope)
            score = _normalize_score(op.get("score"), default=DEFAULT_SCORE)
            tier = _normalize_tier(
                op.get("tier"), default=_derive_tier_from_score(score)
            )
            score = _align_score_to_tier(score, tier)
            store["items"].append(
                {
                    "id": item_id,
                    "scope": scope,
                    "section": section,
                    "content": content,
                    "status": "active",
                    "created_at": now_iso,
                    "updated_at": now_iso,
                    "archived_at": None,
                    "source_thread_id": thread_id,
                    "source_anchor": source_anchor,
                    "confidence": _normalize_confidence(
                        op.get("confidence"), default="high"
                    ),
                    "tier": tier,
                    "score": score,
                    "reason": _normalize_reason(op.get("reason")),
                    "last_scored_at": now_iso,
                    "norm_hash": _normalize_hash(section, content),
                }
            )
            changed_scopes.add(scope)
            continue

        item_id = op.get("id")
        if not isinstance(item_id, str):
            continue
        if item_id in conflicted_ids and item_id not in invalid_fact_delete_ids:
            continue
        if item_id in invalid_fact_delete_ids and op_name != "delete":
            continue
        item = _find_item(store, item_id)
        if item is None:
            continue
        if op_name == "update":
            if "content" in op:
                content = str(op.get("content", "")).strip()
                if not content:
                    continue
                item["content"] = content
                item["norm_hash"] = _normalize_hash(
                    str(item.get("section", "")), content
                )
            item["updated_at"] = now_iso
            item["source_thread_id"] = thread_id
            item["source_anchor"] = source_anchor
            if "confidence" in op:
                item["confidence"] = _normalize_confidence(
                    op.get("confidence"),
                    default=str(item.get("confidence", "medium")),
                )
            has_score = "score" in op
            has_tier = "tier" in op
            if has_score and has_tier:
                # Both provided: tier is authoritative; score is clamped into its band.
                tier = _normalize_tier(
                    op.get("tier"),
                    default=_normalize_tier(
                        item.get("tier"),
                        default=_derive_tier_from_score(
                            _normalize_score(item.get("score"))
                        ),
                    ),
                )
                score = _normalize_score(
                    op.get("score"), default=_normalize_score(item.get("score"))
                )
                item["tier"] = tier
                item["score"] = _align_score_to_tier(score, tier)
            elif has_score:
                score = _normalize_score(
                    op.get("score"), default=_normalize_score(item.get("score"))
                )
                item["score"] = score
                item["tier"] = _derive_tier_from_score(score)
            elif has_tier:
                default_tier = _normalize_tier(
                    item.get("tier"),
                    default=_derive_tier_from_score(
                        _normalize_score(item.get("score"))
                    ),
                )
                item["tier"] = _normalize_tier(op.get("tier"), default=default_tier)
                item["score"] = _align_score_to_tier(
                    _normalize_score(item.get("score")),
                    str(item["tier"]),
                )
            if "reason" in op:
                item["reason"] = _normalize_reason(op.get("reason"))
            if has_score or has_tier:
                item["last_scored_at"] = now_iso
            if item.get("status") == "archived":
                item["status"] = "active"
                item["archived_at"] = None
            changed_scopes.add(scope)
        elif op_name == "rescore":
            score = _normalize_score(
                op.get("score"), default=_normalize_score(item.get("score"))
            )
            item["score"] = score
            item["tier"] = _derive_tier_from_score(score)
            item["reason"] = _normalize_reason(op.get("reason"))
            item["last_scored_at"] = now_iso
            changed_scopes.add(scope)
        elif op_name == "retier":
            default_tier = _normalize_tier(
                item.get("tier"),
                default=_derive_tier_from_score(_normalize_score(item.get("score"))),
            )
            item["tier"] = _normalize_tier(op.get("tier"), default=default_tier)
            item["score"] = _align_score_to_tier(
                _normalize_score(item.get("score")),
                str(item["tier"]),
            )
            item["reason"] = _normalize_reason(op.get("reason"))
            item["last_scored_at"] = now_iso
            changed_scopes.add(scope)
        elif op_name == "archive":
            if item.get("status") == "archived":
                continue
            item["status"] = "archived"
            item["updated_at"] = now_iso
            item["archived_at"] = now_iso
            item["source_thread_id"] = thread_id
            item["source_anchor"] = source_anchor
            if "reason" in op:
                item["reason"] = _normalize_reason(op.get("reason"))
            changed_scopes.add(scope)
        elif op_name == "delete":
            items = store.get("items", [])
            if not isinstance(items, list):
                continue
            store["items"] = [
                existing
                for existing in items
                if not (isinstance(existing, dict) and existing.get("id") == item_id)
            ]
            changed_scopes.add(scope)

    return user_store, project_store, sorted(changed_scopes)
