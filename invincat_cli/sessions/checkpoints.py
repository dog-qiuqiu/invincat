"""Checkpoint summary helpers for session thread metadata."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from invincat_cli import sessions as _sessions

if TYPE_CHECKING:
    import aiosqlite
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    from invincat_cli.sessions import ThreadInfo, _CheckpointSummary


async def _count_messages_from_checkpoint(
    conn: aiosqlite.Connection,
    thread_id: str,
    serde: JsonPlusSerializer,
) -> int:
    """Count messages from the most recent checkpoint blob.

    With `durability='exit'`, messages are stored in the checkpoint blob, not in
    the writes table. This function deserializes the checkpoint and counts the
    messages in channel_values.

    Args:
        conn: Database connection.
        thread_id: The thread ID to count messages for.
        serde: Serializer for decoding checkpoint data.

    Returns:
        Number of messages in the checkpoint, or 0 if not found.
    """
    return (await _sessions._load_latest_checkpoint_summary(conn, thread_id, serde)).message_count


async def _extract_initial_prompt(
    conn: aiosqlite.Connection,
    thread_id: str,
    serde: JsonPlusSerializer,
) -> str | None:
    """Extract the first human message from the latest checkpoint.

    Args:
        conn: Database connection.
        thread_id: The thread ID to extract from.
        serde: Serializer for decoding checkpoint data.

    Returns:
        First human message content, or None if not found.
    """
    summary = await _sessions._load_latest_checkpoint_summary(conn, thread_id, serde)
    return summary.initial_prompt


async def populate_thread_initial_prompts(threads: list[ThreadInfo]) -> None:
    """Populate `initial_prompt` for thread rows in the background.

    Args:
        threads: Thread rows to enrich in place.
    """
    if not threads:
        return

    async with _sessions._connect() as conn:
        await _sessions._populate_checkpoint_fields(
            conn,
            threads,
            include_message_count=False,
            include_initial_prompt=True,
        )


async def _populate_checkpoint_fields(
    conn: aiosqlite.Connection,
    threads: list[ThreadInfo],
    *,
    include_message_count: bool,
    include_initial_prompt: bool,
) -> None:
    """Populate checkpoint-derived thread fields with a batched latest-row pass."""
    serde = await _sessions._get_jsonplus_serializer()

    # Phase 1: apply cache hits, collect threads that need DB fetch.
    uncached: list[ThreadInfo] = []
    for thread in threads:
        thread_id = thread["thread_id"]
        freshness = _sessions._thread_freshness(thread)
        needs_count = False
        needs_prompt = False

        if include_message_count:
            cached = _sessions._message_count_cache.get(thread_id)
            if cached is not None and cached[0] == freshness:
                thread["message_count"] = cached[1]
            else:
                needs_count = True

        if include_initial_prompt and "initial_prompt" not in thread:
            cached_prompt = _sessions._initial_prompt_cache.get(thread_id)
            if cached_prompt is not None and cached_prompt[0] == freshness:
                thread["initial_prompt"] = cached_prompt[1]
            else:
                needs_prompt = True

        if needs_count or needs_prompt:
            uncached.append(thread)

    if not uncached:
        return

    # Phase 2: batch-fetch all uncached threads.
    uncached_ids = [t["thread_id"] for t in uncached]
    batch_results = await _sessions._load_latest_checkpoint_summaries_batch(
        conn, uncached_ids, serde
    )

    # Phase 3: apply results and update caches.
    for thread in uncached:
        thread_id = thread["thread_id"]
        freshness = _sessions._thread_freshness(thread)
        summary = batch_results.get(thread_id, _sessions._CheckpointSummary(0, None))

        if include_message_count and "message_count" not in thread:
            thread["message_count"] = summary.message_count
            _sessions._cache_message_count(thread_id, freshness, summary.message_count)
        if include_initial_prompt and "initial_prompt" not in thread:
            thread["initial_prompt"] = summary.initial_prompt
            _sessions._cache_initial_prompt(thread_id, freshness, summary.initial_prompt)


_SQLITE_MAX_VARIABLE_NUMBER = 500
"""Max `?` placeholders per SQL query.

SQLite limits how many `?` parameters a single query can have (default 999,
lower on some builds). If a user accumulates hundreds of threads and the
`/threads` modal fetches them all at once, the `IN (?, ?, ...)` clause could
exceed that limit. We chunk to this size to stay safe.
"""


async def _load_latest_checkpoint_summaries_batch(
    conn: aiosqlite.Connection,
    thread_ids: list[str],
    serde: JsonPlusSerializer,
) -> dict[str, _CheckpointSummary]:
    """Batch-load the latest checkpoint summary for multiple threads.

    Uses a window function to fetch the latest checkpoint per thread, issuing
    one query per chunk for SQLite variable-limit safety.

    Args:
        conn: Database connection.
        thread_ids: Thread IDs to look up.
        serde: Serializer for decoding checkpoint blobs.

    Returns:
        Dict mapping thread IDs to their checkpoint summaries.
    """
    if not thread_ids:
        return {}

    results: dict[str, _CheckpointSummary] = {}

    for start in range(0, len(thread_ids), _sessions._SQLITE_MAX_VARIABLE_NUMBER):
        chunk = thread_ids[start : start + _sessions._SQLITE_MAX_VARIABLE_NUMBER]
        placeholders = ",".join("?" * len(chunk))
        query = f"""
            SELECT thread_id, type, checkpoint FROM (
                SELECT thread_id, type, checkpoint,
                       ROW_NUMBER() OVER (
                           PARTITION BY thread_id ORDER BY checkpoint_id DESC
                       ) AS rn
                FROM checkpoints
                WHERE thread_id IN ({placeholders})
            ) WHERE rn = 1
        """  # noqa: S608  # placeholders built from len(chunk); user values use ? params
        async with conn.execute(query, chunk) as cursor:
            rows = await cursor.fetchall()

        loop = asyncio.get_running_loop()
        for row in rows:
            tid, type_str, checkpoint_blob = row
            if not type_str or not checkpoint_blob:
                results[tid] = _sessions._CheckpointSummary(message_count=0, initial_prompt=None)
                continue
            try:
                data = await loop.run_in_executor(
                    None, serde.loads_typed, (type_str, checkpoint_blob)
                )
                results[tid] = _sessions._summarize_checkpoint(data)
            except Exception:
                _sessions.logger.warning(
                    "Failed to deserialize checkpoint for thread %s; "
                    "message count and initial prompt may be incomplete",
                    tid,
                    exc_info=True,
                )
                results[tid] = _sessions._CheckpointSummary(message_count=0, initial_prompt=None)

    return results


async def _load_latest_checkpoint_summary(
    conn: aiosqlite.Connection,
    thread_id: str,
    serde: JsonPlusSerializer,
) -> _CheckpointSummary:
    """Load checkpoint-derived summary data from the latest checkpoint row.

    Returns:
        Message-count and prompt data extracted from the latest checkpoint row.
    """
    query = """
        SELECT type, checkpoint
        FROM checkpoints
        WHERE thread_id = ?
        ORDER BY checkpoint_id DESC
        LIMIT 1
    """
    async with conn.execute(query, (thread_id,)) as cursor:
        row = await cursor.fetchone()
        if not row or not row[0] or not row[1]:
            return _sessions._CheckpointSummary(message_count=0, initial_prompt=None)

        type_str, checkpoint_blob = row
        try:
            data = serde.loads_typed((type_str, checkpoint_blob))
        except (ValueError, TypeError, KeyError, AttributeError):
            _sessions.logger.warning(
                "Failed to deserialize checkpoint for thread %s; "
                "message count and initial prompt may be incomplete",
                thread_id,
                exc_info=True,
            )
            return _sessions._CheckpointSummary(message_count=0, initial_prompt=None)

    return _sessions._summarize_checkpoint(data)


def _summarize_checkpoint(data: object) -> _CheckpointSummary:
    """Extract message count and initial human prompt from checkpoint data.

    Returns:
        Structured summary for the decoded checkpoint payload.
    """
    messages = _sessions._checkpoint_messages(data)
    return _sessions._CheckpointSummary(
        message_count=len(messages),
        initial_prompt=_sessions._initial_prompt_from_messages(messages),
    )


def _checkpoint_messages(data: object) -> list[object]:
    """Return checkpoint messages when the decoded payload has the expected shape."""
    if not isinstance(data, dict):
        return []

    payload = cast("dict[str, object]", data)
    channel_values = payload.get("channel_values")
    if not isinstance(channel_values, dict):
        return []

    channel_values_dict = cast("dict[str, object]", channel_values)
    messages = channel_values_dict.get("messages")
    if not isinstance(messages, list):
        return []

    return cast("list[object]", messages)


def _initial_prompt_from_messages(messages: list[object]) -> str | None:
    """Return the first human message content from a checkpoint message list."""
    for msg in messages:
        if getattr(msg, "type", None) == "human":
            return _sessions._coerce_prompt_text(getattr(msg, "content", None))
    return None


def _coerce_prompt_text(content: object) -> str | None:
    """Normalize checkpoint message content into displayable text.

    Returns:
        Displayable prompt text, or `None` when the content is empty.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                part_dict = cast("dict[str, object]", part)
                text = part_dict.get("text")
                parts.append(text if isinstance(text, str) else "")
            else:
                parts.append(str(part))
        joined = " ".join(parts).strip()
        return joined or None
    if content is None:
        return None
    return str(content)
