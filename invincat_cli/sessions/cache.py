"""In-memory caches for session thread metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING

from invincat_cli import sessions as _sessions

if TYPE_CHECKING:
    from invincat_cli.sessions import ThreadInfo


def get_cached_threads(
    agent_name: str | None = None,
    limit: int | None = None,
    *,
    require_message_counts: bool = False,
) -> list[ThreadInfo] | None:
    """Get cached recent threads, if available.

    Args:
        agent_name: Optional agent-name filter key.
        limit: Maximum rows requested. Uses `_sessions.get_thread_limit()` when `None`.
        require_message_counts: If True, only return cached threads when all
            rows have message counts populated. Useful for avoiding "..."
            placeholders in the thread selector.

    Returns:
        Copy of cached rows when available, otherwise `None`.
    """

    def _copy_with_cached_counts(rows: list[ThreadInfo]) -> list[ThreadInfo]:
        copied_rows = _sessions._copy_threads(rows)
        _sessions.apply_cached_thread_message_counts(copied_rows)
        _sessions.apply_cached_thread_initial_prompts(copied_rows)
        return copied_rows

    thread_limit = limit if limit is not None else _sessions.get_thread_limit()
    if thread_limit < 1:
        return None

    exact = _sessions._recent_threads_cache.get((agent_name, thread_limit))
    if exact is not None:
        result = _copy_with_cached_counts(exact)
        if require_message_counts and result:
            if not all("message_count" in t for t in result):
                return None
        return result

    best_key: tuple[str | None, int] | None = None
    for key in _sessions._recent_threads_cache:
        cache_agent, cache_limit = key
        if cache_agent != agent_name or cache_limit < thread_limit:
            continue
        if best_key is None or cache_limit < best_key[1]:
            best_key = key

    if best_key is None:
        return None

    result = _copy_with_cached_counts(_sessions._recent_threads_cache[best_key][:thread_limit])
    if require_message_counts and result:
        if not all("message_count" in t for t in result):
            return None
    return result


def apply_cached_thread_message_counts(threads: list[ThreadInfo]) -> int:
    """Apply cached message counts onto thread rows when freshness matches.

    Args:
        threads: Thread rows to mutate in place.

    Returns:
        Number of rows that were populated from cache.
    """
    populated = 0
    for thread in threads:
        if "message_count" in thread:
            continue
        thread_id = thread["thread_id"]
        freshness = _sessions._thread_freshness(thread)
        cached = _sessions._message_count_cache.get(thread_id)
        if cached is None or cached[0] != freshness:
            continue
        thread["message_count"] = cached[1]
        populated += 1
    return populated


def apply_cached_thread_initial_prompts(threads: list[ThreadInfo]) -> int:
    """Apply cached initial prompts onto thread rows when freshness matches.

    Args:
        threads: Thread rows to mutate in place.

    Returns:
        Number of rows that were populated from cache.
    """
    populated = 0
    for thread in threads:
        if "initial_prompt" in thread:
            continue
        thread_id = thread["thread_id"]
        freshness = _sessions._thread_freshness(thread)
        cached = _sessions._initial_prompt_cache.get(thread_id)
        if cached is None or cached[0] != freshness:
            continue
        thread["initial_prompt"] = cached[1]
        populated += 1
    return populated

def _cache_message_count(thread_id: str, freshness: str | None, count: int) -> None:
    """Cache a thread's message count with a freshness token."""
    if len(_sessions._message_count_cache) >= _sessions._MAX_MESSAGE_COUNT_CACHE and (
        thread_id not in _sessions._message_count_cache
    ):
        oldest = next(iter(_sessions._message_count_cache))
        _sessions._message_count_cache.pop(oldest, None)
    _sessions._message_count_cache[thread_id] = (freshness, count)


def _cache_initial_prompt(
    thread_id: str,
    freshness: str | None,
    initial_prompt: str | None,
) -> None:
    """Cache a thread's initial prompt with a freshness token."""
    if len(_sessions._initial_prompt_cache) >= _sessions._MAX_INITIAL_PROMPT_CACHE and (
        thread_id not in _sessions._initial_prompt_cache
    ):
        oldest = next(iter(_sessions._initial_prompt_cache))
        _sessions._initial_prompt_cache.pop(oldest, None)
    _sessions._initial_prompt_cache[thread_id] = (freshness, initial_prompt)


def _thread_freshness(thread: ThreadInfo) -> str | None:
    """Return a cache freshness token for a thread row."""
    return thread.get("latest_checkpoint_id") or thread.get("updated_at")


def _cache_recent_threads(
    agent_name: str | None,
    limit: int,
    threads: list[ThreadInfo],
) -> None:
    """Store a copy of recent thread rows for fast selector startup."""
    key = (agent_name, max(1, limit))
    if len(_sessions._recent_threads_cache) >= _sessions._MAX_RECENT_THREADS_CACHE_KEYS and (
        key not in _sessions._recent_threads_cache
    ):
        _sessions._recent_threads_cache.clear()
    _sessions._recent_threads_cache[key] = _sessions._copy_threads(threads)


def _copy_threads(threads: list[ThreadInfo]) -> list[ThreadInfo]:
    """Return shallow-copied thread rows."""
    return [_sessions.ThreadInfo(**thread) for thread in threads]
