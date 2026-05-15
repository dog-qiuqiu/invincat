"""Store loading, cleanup, and write helpers for the memory middleware."""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from invincat_cli.memory import store_ops as _ops

logger = logging.getLogger(__name__)


def load_or_recover_store(
    middleware: Any,
    scope: str,
    thread_id: str,
    source_anchor: str,
) -> dict[str, Any] | None:
    """Load one store, recovering unreadable content when possible."""
    del thread_id, source_anchor
    store_path_raw = middleware._memory_store_paths.get(scope)
    if not store_path_raw:
        return None
    store_path = Path(store_path_raw).expanduser().resolve()
    if store_path.exists():
        store = _ops._read_memory_store(store_path, scope)
        if not store.get("__read_error__"):
            return store
        logger.warning(
            "Memory agent: attempting auto-recovery for unreadable %s store", scope
        )
        backup = _ops._backup_corrupt_store(store_path)
        if backup is not None:
            logger.warning(
                "Memory agent: backed up unreadable store to %s before recovery",
                backup,
            )
    store = _ops._new_store(scope)
    if middleware._is_authorized_path(store_path):
        _ops._write_memory_store(store_path, store)
    return store


async def apply_and_write_memory_operations(
    middleware: Any,
    user_store: dict[str, Any] | None,
    project_store: dict[str, Any] | None,
    user_before: dict[str, Any] | None,
    project_before: dict[str, Any] | None,
    operations: list[dict[str, Any]],
    *,
    thread_id: str,
    source_anchor: str,
    now_iso: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    """Apply operations and write changed memory stores."""
    del user_before, project_before
    if not operations:
        return user_store, project_store, []

    new_user, new_project, changed_scopes = _ops._apply_operations(
        user_store,
        project_store,
        operations,
        thread_id=thread_id,
        source_anchor=source_anchor,
        now_iso=now_iso,
    )
    if not changed_scopes:
        return new_user, new_project, []

    written_store_paths: list[str] = []
    for scope in changed_scopes:
        store = new_user if scope == "user" else new_project
        if store is None:
            continue

        store_path_raw = middleware._memory_store_paths.get(scope)
        if not store_path_raw:
            continue
        store_path = Path(store_path_raw).expanduser().resolve()
        if not middleware._is_authorized_path(store_path):
            logger.warning("Memory agent: rejected unauthorized write for %s scope", scope)
            continue

        await asyncio.to_thread(_ops._write_memory_store, store_path, store)
        written_store_paths.append(str(store_path))

    return new_user, new_project, written_store_paths


async def cleanup_invalid_fact_stores(
    middleware: Any,
    *,
    thread_id: str,
    source_anchor: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    """Run deterministic cleanup passes and return the post-cleanup stores."""
    user_store = await asyncio.to_thread(
        middleware._load_or_recover_store, "user", thread_id, source_anchor
    )
    project_store = await asyncio.to_thread(
        middleware._load_or_recover_store, "project", thread_id, source_anchor
    )
    unreadable_scopes: list[str] = []
    if isinstance(user_store, dict) and user_store.get("__read_error__"):
        unreadable_scopes.append("user")
    if isinstance(project_store, dict) and project_store.get("__read_error__"):
        unreadable_scopes.append("project")
    if unreadable_scopes:
        logger.warning(
            "Memory agent: skip cleanup because store is unreadable (scopes=%s)",
            ",".join(unreadable_scopes),
        )
        return user_store, project_store, []

    all_cleanup = _ops._build_invalid_fact_cleanup_operations(
        user_store,
        project_store,
    ) + _ops._build_archived_overflow_operations(
        user_store,
        project_store,
    )
    if not all_cleanup:
        return user_store, project_store, []

    return await middleware._apply_and_write_memory_operations(
        user_store,
        project_store,
        deepcopy(user_store),
        deepcopy(project_store),
        all_cleanup,
        thread_id=thread_id,
        source_anchor=source_anchor,
        now_iso=_ops._iso_now(),
    )
