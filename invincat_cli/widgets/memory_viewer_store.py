"""Memory store loading and mutation helpers for the viewer."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from invincat_cli.widgets.memory_viewer_models import MemoryItemView, MemoryScopeView

MAX_CONTENT_PREVIEW_CHARS = 140
ALLOWED_STATUS = frozenset({"active", "archived"})
ALLOWED_TIER = frozenset({"hot", "warm", "cold"})


def iso_to_local(iso_value: str | None) -> str | None:
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


def trim(text: Any, max_chars: int) -> str:
    if not isinstance(text, str):
        return ""
    normalized = " ".join(text.strip().split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def normalize_tier(value: Any) -> str:
    if isinstance(value, str):
        tier = value.strip().lower()
        if tier in ALLOWED_TIER:
            return tier
    return "warm"


def normalize_score(value: Any) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        score = 50
    return max(0, min(100, score))


def load_scope_snapshot(scope: str, path: str) -> MemoryScopeView:
    store_path = Path(path)
    if not store_path.exists():
        return _empty_scope(scope, store_path, exists=False, error="store not found")

    try:
        raw = store_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _empty_scope(scope, store_path, exists=True, error="store unreadable")

    items_raw = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items_raw, list):
        return _empty_scope(
            scope,
            store_path,
            exists=True,
            error="invalid schema: items is not a list",
        )

    items = [
        item
        for raw_item in items_raw
        if isinstance(raw_item, dict)
        if (item := _item_from_raw(scope, raw_item)) is not None
    ]
    active = sum(1 for item in items if item.status == "active")
    archived = sum(1 for item in items if item.status == "archived")
    latest_updated_at = next(
        iter(sorted((item.updated_at for item in items if item.updated_at), reverse=True)),
        None,
    )
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
            snapshots[scope] = load_scope_snapshot(scope, raw_path)
    return snapshots


def delete_memory_item(store_path: str, item_id: str) -> None:
    """Remove item with ``item_id`` from the store at ``store_path`` atomically."""
    path = Path(store_path)
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    items = payload.get("items")
    if not isinstance(items, list):
        msg = "invalid store schema: items is not a list"
        raise ValueError(msg)
    payload["items"] = [
        item
        for item in items
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


def _empty_scope(
    scope: str,
    store_path: Path,
    *,
    exists: bool,
    error: str,
) -> MemoryScopeView:
    return MemoryScopeView(
        scope=scope,
        path=str(store_path),
        exists=exists,
        valid=False,
        error=error,
        total=0,
        active=0,
        archived=0,
        latest_updated_at=None,
        items=[],
    )


def _item_from_raw(scope: str, raw_item: dict[str, Any]) -> MemoryItemView | None:
    item_id = trim(raw_item.get("id"), max_chars=64)
    section = trim(raw_item.get("section"), max_chars=80)
    content = trim(raw_item.get("content"), max_chars=MAX_CONTENT_PREVIEW_CHARS)
    status = trim(raw_item.get("status"), max_chars=16).lower()
    if not item_id or not section or not content or status not in ALLOWED_STATUS:
        return None

    updated_at = trim(raw_item.get("updated_at"), max_chars=64)
    reason_source = (
        raw_item.get("reason")
        if raw_item.get("reason") is not None
        else raw_item.get("score_reason")
    )
    last_scored_at = trim(raw_item.get("last_scored_at"), max_chars=64) or updated_at
    return MemoryItemView(
        scope=scope,
        item_id=item_id,
        section=section,
        status=status,
        content=content,
        tier=normalize_tier(raw_item.get("tier")),
        score=normalize_score(raw_item.get("score")),
        reason=trim(reason_source, max_chars=160),
        last_scored_at=last_scored_at,
        updated_at=updated_at,
    )
