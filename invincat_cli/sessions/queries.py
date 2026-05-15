"""Thread list query and prewarm helpers for session storage."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from invincat_cli import sessions as _sessions

if TYPE_CHECKING:
    from invincat_cli.sessions import ThreadInfo


async def list_threads(
    agent_name: str | None = None,
    limit: int = 20,
    include_message_count: bool = False,
    sort_by: str = "updated",
    branch: str | None = None,
) -> list[ThreadInfo]:
    """List threads from checkpoints table.

    Args:
        agent_name: Optional filter by agent name.
        limit: Maximum number of threads to return.
        include_message_count: Whether to include message counts.
        sort_by: Sort field — `"updated"` or `"created"`.
        branch: Optional filter by git branch name.

    Returns:
        List of `ThreadInfo` dicts with `thread_id`, `agent_name`,
            `updated_at`, `created_at`, `latest_checkpoint_id`, `git_branch`,
            `cwd`, and optionally `message_count`.

    Raises:
        ValueError: If `sort_by` is not `"updated"` or `"created"`.
    """
    async with _sessions._connect() as conn:
        if not await _sessions._table_exists(conn, "checkpoints"):
            return []

        if sort_by not in {"updated", "created"}:
            msg = f"Invalid sort_by {sort_by!r}; expected 'updated' or 'created'"
            raise ValueError(msg)
        order_col = "created_at" if sort_by == "created" else "updated_at"

        where_clauses: list[str] = []
        params_list: list[str | int] = []

        if agent_name:
            where_clauses.append("json_extract(metadata, '$.agent_name') = ?")
            params_list.append(agent_name)
        if branch:
            where_clauses.append("json_extract(metadata, '$.git_branch') = ?")
            params_list.append(branch)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        query = f"""
            SELECT thread_id,
                   json_extract(metadata, '$.agent_name') as agent_name,
                   MAX(json_extract(metadata, '$.updated_at')) as updated_at,
                   MAX(checkpoint_id) as latest_checkpoint_id,
                   MIN(json_extract(metadata, '$.updated_at')) as created_at,
                   MAX(json_extract(metadata, '$.git_branch')) as git_branch,
                   MAX(json_extract(metadata, '$.cwd')) as cwd
            FROM checkpoints
            {where_sql}
            GROUP BY thread_id
            ORDER BY {order_col} DESC
            LIMIT ?
        """  # noqa: S608  # where_sql/order_col derived from controlled internal values; user values use ? placeholders
        params: tuple = (*params_list, limit)

        async with conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            threads: list[ThreadInfo] = [
                _sessions.ThreadInfo(
                    thread_id=r[0],
                    agent_name=r[1],
                    updated_at=r[2],
                    latest_checkpoint_id=r[3],
                    created_at=r[4],
                    git_branch=r[5],
                    cwd=r[6],
                )
                for r in rows
            ]

        # Fetch message counts if requested
        if include_message_count and threads:
            await _sessions._populate_message_counts(conn, threads)

        # Only cache unfiltered results so the thread selector modal
        # doesn't receive branch-filtered or differently-sorted data.
        if sort_by == "updated" and branch is None:
            _sessions._cache_recent_threads(agent_name, limit, threads)
        return threads


async def populate_thread_message_counts(threads: list[ThreadInfo]) -> list[ThreadInfo]:
    """Populate `message_count` for an existing thread list.

    This is used by the `/threads` modal to render rows quickly, then backfill
    counts in the background without issuing a second thread-list query.

    Args:
        threads: Thread rows to enrich in place.

    Returns:
        The same list object with `message_count` values populated.
    """
    if not threads:
        return threads

    async with _sessions._connect() as conn:
        await _sessions._populate_message_counts(conn, threads)
    return threads


async def populate_thread_checkpoint_details(
    threads: list[ThreadInfo],
    *,
    include_message_count: bool = True,
    include_initial_prompt: bool = True,
) -> list[ThreadInfo]:
    """Populate checkpoint-derived fields for an existing thread list.

    This is used by the `/threads` modal to enrich rows in one background pass,
    so the latest checkpoint is fetched and deserialized at most once per row.

    Args:
        threads: Thread rows to enrich in place.
        include_message_count: Whether to populate `message_count`.
        include_initial_prompt: Whether to populate `initial_prompt`.

    Returns:
        The same list object with missing checkpoint-derived fields populated.
    """
    if not threads or (not include_message_count and not include_initial_prompt):
        return threads

    async with _sessions._connect() as conn:
        await _sessions._populate_checkpoint_fields(
            conn,
            threads,
            include_message_count=include_message_count,
            include_initial_prompt=include_initial_prompt,
        )
    return threads


async def prewarm_thread_message_counts(limit: int | None = None) -> None:
    """Prewarm thread selector cache for faster `/threads` open.

    Fetches a bounded list of recent threads and populates checkpoint-derived
    fields for currently visible columns into the in-memory cache. Intended to
    run in a background worker during app startup.

    Args:
        limit: Maximum threads to prewarm. Uses `_sessions.get_thread_limit()` when `None`.
    """
    thread_limit = limit if limit is not None else _sessions.get_thread_limit()
    if thread_limit < 1:
        return

    try:
        from invincat_cli.model_config import load_thread_config

        cfg = load_thread_config()
        threads = await _sessions.list_threads(limit=thread_limit, include_message_count=False)
        if threads:
            await _sessions.populate_thread_checkpoint_details(
                threads,
                include_message_count=cfg.columns.get("messages", False),
                include_initial_prompt=cfg.columns.get("initial_prompt", False),
            )
        _sessions._cache_recent_threads(None, thread_limit, threads)
    except (OSError, sqlite3.Error):
        _sessions.logger.debug("Could not prewarm thread selector cache", exc_info=True)
    except Exception:
        _sessions.logger.warning(
            "Unexpected error while prewarming thread selector cache",
            exc_info=True,
        )
