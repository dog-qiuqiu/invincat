"""Thread management using LangGraph's built-in checkpoint persistence."""

from __future__ import annotations

import asyncio
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, NotRequired, TypedDict

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import aiosqlite
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from invincat_cli.io.output import OutputFormat

logger = logging.getLogger(__name__)

_aiosqlite_patched = False
_jsonplus_serializer: JsonPlusSerializer | None = None
_message_count_cache: dict[str, tuple[str | None, int]] = {}
_MAX_MESSAGE_COUNT_CACHE = 4096
_initial_prompt_cache: dict[str, tuple[str | None, str | None]] = {}
_MAX_INITIAL_PROMPT_CACHE = 4096
_recent_threads_cache: dict[tuple[str | None, int], list[ThreadInfo]] = {}
_MAX_RECENT_THREADS_CACHE_KEYS = 16


def _patch_aiosqlite() -> None:
    """Patch aiosqlite.Connection with `is_alive()` if missing.

    Required by langgraph-checkpoint>=2.1.0.
    See: https://github.com/langchain-ai/langgraph/issues/6583
    """
    global _aiosqlite_patched  # noqa: PLW0603  # Module-level flag requires global statement
    if _aiosqlite_patched:
        return

    import aiosqlite as _aiosqlite

    if not hasattr(_aiosqlite.Connection, "is_alive"):

        def _is_alive(self: _aiosqlite.Connection) -> bool:
            """Check if the connection is still alive.

            Returns:
                True if connection is alive, False otherwise.
            """
            return bool(self._running and self._connection is not None)

        # Dynamically adding a method to aiosqlite.Connection at runtime.
        # Type checkers can't understand this monkey-patch, so we suppress the
        # "attr-defined" error that would otherwise be raised.
        _aiosqlite.Connection.is_alive = _is_alive  # type: ignore[attr-defined]

    _aiosqlite_patched = True


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Import aiosqlite, apply the compatibility patch, and connect.

    Centralizes the deferred import + patch + connect sequence used by every
    database function in this module.

    Yields:
        An open aiosqlite connection to the sessions database.
    """
    import aiosqlite as _aiosqlite

    _patch_aiosqlite()

    async with _aiosqlite.connect(str(get_db_path()), timeout=30.0) as conn:
        yield conn


class ThreadInfo(TypedDict):
    """Thread metadata returned by `list_threads`."""

    thread_id: str
    """Unique identifier for the thread."""

    agent_name: str | None
    """Name of the agent that owns the thread."""

    updated_at: str | None
    """ISO timestamp of the last update."""

    created_at: NotRequired[str | None]
    """ISO timestamp of thread creation (earliest checkpoint)."""

    git_branch: NotRequired[str | None]
    """Git branch active when the thread was created."""

    initial_prompt: NotRequired[str | None]
    """First human message in the thread."""

    message_count: NotRequired[int]
    """Number of messages in the thread."""

    latest_checkpoint_id: NotRequired[str | None]
    """Most recent checkpoint ID for cache invalidation."""

    cwd: NotRequired[str | None]
    """Working directory where the thread was last used."""


class _CheckpointSummary(NamedTuple):
    """Structured data extracted from a thread's latest checkpoint."""

    message_count: int
    """Number of messages in the latest checkpoint."""

    initial_prompt: str | None
    """First human prompt recovered from the latest checkpoint."""


from invincat_cli.sessions.format import (  # noqa: E402, F401
    format_path,
    format_relative_timestamp,
    format_timestamp,
)

_db_path: Path | None = None


def get_db_path() -> Path:
    """Get path to global database.

    The result is cached after the first successful call to avoid repeated
    filesystem operations.

    On the first call, if the new database (`~/.invincat/sessions.db`) does
    not yet exist but the legacy path (`~/.deepagents/sessions.db`) does, the
    old file is copied to the new location so that existing conversation history
    is transparently preserved after the config-directory rename (``44d46a7``).

    Returns:
        Path to the SQLite database file.
    """
    global _db_path  # noqa: PLW0603  # Module-level cache requires global statement
    if _db_path is not None:
        return _db_path
    db_dir = Path.home() / ".invincat"
    db_dir.mkdir(parents=True, exist_ok=True)
    new_db = db_dir / "sessions.db"

    # One-time migration: copy the legacy DB when the new path is absent.
    # Uses copy2 (not rename/move) so the old file remains as a backup.
    if not new_db.exists():
        old_db = Path.home() / ".deepagents" / "sessions.db"
        if old_db.exists():
            try:
                shutil.copy2(old_db, new_db)
                logger.info(
                    "Migrated sessions database from %s to %s",
                    old_db,
                    new_db,
                )
            except OSError:
                logger.warning(
                    "Could not migrate sessions database from %s to %s; "
                    "conversation history from the previous installation may "
                    "not be visible in /threads",
                    old_db,
                    new_db,
                    exc_info=True,
                )

    _db_path = new_db
    return _db_path


def generate_thread_id() -> str:
    """Generate a new thread ID as a full UUID7 string.

    Returns:
        UUID7 string (time-ordered for natural sort by creation time).
    """
    from uuid_utils import uuid7

    return str(uuid7())


async def _table_exists(conn: aiosqlite.Connection, table: str) -> bool:
    """Check if a table exists in the database.

    Returns:
        True if table exists, False otherwise.
    """
    query = "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?"
    async with conn.execute(query, (table,)) as cursor:
        return await cursor.fetchone() is not None


from invincat_cli.sessions.cache import (  # noqa: E402, F401
    _cache_initial_prompt,
    _cache_message_count,
    _cache_recent_threads,
    _copy_threads,
    _thread_freshness,
    apply_cached_thread_initial_prompts,
    apply_cached_thread_message_counts,
    get_cached_threads,
)
from invincat_cli.sessions.queries import (  # noqa: E402, F401
    list_threads,
    populate_thread_checkpoint_details,
    populate_thread_message_counts,
    prewarm_thread_message_counts,
)


async def _populate_message_counts(
    conn: aiosqlite.Connection,
    threads: list[ThreadInfo],
) -> None:
    """Fill `message_count` on thread rows with cache-aware lookup."""
    await _populate_checkpoint_fields(
        conn,
        threads,
        include_message_count=True,
        include_initial_prompt=False,
    )


async def _get_jsonplus_serializer() -> JsonPlusSerializer:
    """Return a cached JsonPlus serializer, loading it off the UI loop."""
    global _jsonplus_serializer  # noqa: PLW0603  # Module-level cache requires global statement
    if _jsonplus_serializer is not None:
        return _jsonplus_serializer

    loop = asyncio.get_running_loop()
    _jsonplus_serializer = await loop.run_in_executor(None, _create_jsonplus_serializer)
    return _jsonplus_serializer


def _create_jsonplus_serializer() -> JsonPlusSerializer:
    """Import and create a JsonPlus serializer.

    Returns:
        A ready `JsonPlusSerializer` instance.
    """
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return JsonPlusSerializer()


from invincat_cli.sessions.checkpoints import (  # noqa: E402, F401
    _SQLITE_MAX_VARIABLE_NUMBER,
    _checkpoint_messages,
    _coerce_prompt_text,
    _count_messages_from_checkpoint,
    _extract_initial_prompt,
    _initial_prompt_from_messages,
    _load_latest_checkpoint_summaries_batch,
    _load_latest_checkpoint_summary,
    _populate_checkpoint_fields,
    _summarize_checkpoint,
    populate_thread_initial_prompts,
)


async def get_most_recent(agent_name: str | None = None) -> str | None:
    """Get most recent thread_id, optionally filtered by agent.

    Returns:
        Most recent thread_id or None if no threads exist.
    """
    async with _connect() as conn:
        if not await _table_exists(conn, "checkpoints"):
            return None

        if agent_name:
            query = """
                SELECT thread_id FROM checkpoints
                WHERE json_extract(metadata, '$.agent_name') = ?
                ORDER BY checkpoint_id DESC
                LIMIT 1
            """
            params: tuple = (agent_name,)
        else:
            query = (
                "SELECT thread_id FROM checkpoints ORDER BY checkpoint_id DESC LIMIT 1"
            )
            params = ()

        async with conn.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def get_thread_agent(thread_id: str) -> str | None:
    """Get agent_name for a thread.

    Returns:
        Agent name associated with the thread, or None if not found.
    """
    async with _connect() as conn:
        if not await _table_exists(conn, "checkpoints"):
            return None

        query = """
            SELECT json_extract(metadata, '$.agent_name')
            FROM checkpoints
            WHERE thread_id = ?
            LIMIT 1
        """
        async with conn.execute(query, (thread_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def thread_exists(thread_id: str) -> bool:
    """Check if a thread exists in checkpoints.

    Returns:
        True if thread exists, False otherwise.
    """
    async with _connect() as conn:
        if not await _table_exists(conn, "checkpoints"):
            return False

        query = "SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1"
        async with conn.execute(query, (thread_id,)) as cursor:
            row = await cursor.fetchone()
            return row is not None


async def find_similar_threads(thread_id: str, limit: int = 3) -> list[str]:
    """Find threads whose IDs start with the given prefix.

    Args:
        thread_id: Prefix to match against thread IDs.
        limit: Maximum number of matching threads to return.

    Returns:
        List of thread IDs that begin with the given prefix.
    """
    async with _connect() as conn:
        if not await _table_exists(conn, "checkpoints"):
            return []

        query = """
            SELECT DISTINCT thread_id
            FROM checkpoints
            WHERE thread_id LIKE ?
            ORDER BY thread_id
            LIMIT ?
        """
        prefix = thread_id + "%"
        async with conn.execute(query, (prefix, limit)) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def delete_thread(thread_id: str) -> bool:
    """Delete thread checkpoints.

    Returns:
        True if thread was deleted, False if not found.
    """
    async with _connect() as conn:
        if not await _table_exists(conn, "checkpoints"):
            return False

        cursor = await conn.execute(
            "DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,)
        )
        deleted = cursor.rowcount > 0
        if await _table_exists(conn, "writes"):
            await conn.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
        await conn.commit()
        if deleted:
            _message_count_cache.pop(thread_id, None)
            _initial_prompt_cache.pop(thread_id, None)
            for key, rows in list(_recent_threads_cache.items()):
                filtered = [row for row in rows if row["thread_id"] != thread_id]
                _recent_threads_cache[key] = filtered
        return deleted


@asynccontextmanager
async def get_checkpointer() -> AsyncIterator[AsyncSqliteSaver]:
    """Get AsyncSqliteSaver for the global database.

    Yields:
        AsyncSqliteSaver instance for checkpoint persistence.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    _patch_aiosqlite()

    async with AsyncSqliteSaver.from_conn_string(str(get_db_path())) as checkpointer:
        yield checkpointer


_DEFAULT_THREAD_LIMIT = 20


def get_thread_limit() -> int:
    """Read the thread listing limit from `DA_CLI_RECENT_THREADS`.

    Falls back to `_DEFAULT_THREAD_LIMIT` when the variable is unset or contains
    a non-integer value. The result is clamped to a minimum of 1.

    Returns:
        Number of threads to display.
    """
    import os

    raw = os.environ.get("DA_CLI_RECENT_THREADS")
    if raw is None:
        return _DEFAULT_THREAD_LIMIT
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning(
            "Invalid DA_CLI_RECENT_THREADS value %r, using default %d",
            raw,
            _DEFAULT_THREAD_LIMIT,
        )
        return _DEFAULT_THREAD_LIMIT


async def list_threads_command(
    agent_name: str | None = None,
    limit: int | None = None,
    sort_by: str | None = None,
    branch: str | None = None,
    verbose: bool = False,
    relative: bool | None = None,
    *,
    output_format: OutputFormat = "text",
) -> None:
    """CLI handler for `deepagents threads list`."""
    from invincat_cli.sessions.commands import list_threads_command as _impl

    await _impl(
        agent_name=agent_name,
        limit=limit,
        sort_by=sort_by,
        branch=branch,
        verbose=verbose,
        relative=relative,
        output_format=output_format,
    )


async def delete_thread_command(
    thread_id: str,
    *,
    dry_run: bool = False,
    output_format: OutputFormat = "text",
) -> None:
    """CLI handler for `deepagents threads delete`."""
    from invincat_cli.sessions.commands import delete_thread_command as _impl

    await _impl(thread_id, dry_run=dry_run, output_format=output_format)
