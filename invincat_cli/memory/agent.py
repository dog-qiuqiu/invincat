"""Dedicated memory agent middleware with structured memory stores.

Runs an independent model call with a focused system prompt after every
non-trivial conversation turn to extract durable memory operations. By
default the agent runs once per turn (no wall-clock or file cooldown)
so memory stays in sync with the latest signal; the turn-interval,
wall-clock, and file cooldown throttles can still be re-enabled via
the INVINCAT_MEMORY_MIN_TURN_INTERVAL / INVINCAT_MEMORY_MIN_SECONDS_BETWEEN_RUNS
/ INVINCAT_MEMORY_FILE_COOLDOWN_SECONDS environment variables.

The extraction runs in ``aafter_agent`` so it does not block the user from
receiving the main response.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
)
from langgraph.config import get_config

from invincat_cli.core.debug import configure_debug_logging
from invincat_cli.memory import store_ops as _memory_store_ops
from invincat_cli.memory.prompts import (
    _FINAL_INSTRUCTION_TEMPLATE as _FINAL_INSTRUCTION_TEMPLATE,
)
from invincat_cli.memory.prompts import _SYSTEM_PROMPT as _SYSTEM_PROMPT

logger = logging.getLogger(__name__)
configure_debug_logging(logger)

MAX_ITEM_CONTENT_CHARS = _memory_store_ops.MAX_ITEM_CONTENT_CHARS
MAX_SECTION_NAME_CHARS = _memory_store_ops.MAX_SECTION_NAME_CHARS
MAX_REASON_CHARS = _memory_store_ops.MAX_REASON_CHARS
_MAX_OUTPUT_TOKENS = _memory_store_ops._MAX_OUTPUT_TOKENS
DEFAULT_TIER = _memory_store_ops.DEFAULT_TIER
DEFAULT_SCORE = _memory_store_ops.DEFAULT_SCORE
HOT_THRESHOLD = _memory_store_ops.HOT_THRESHOLD
COLD_THRESHOLD = _memory_store_ops.COLD_THRESHOLD
MAX_HOT_ITEMS_PER_SCOPE = _memory_store_ops.MAX_HOT_ITEMS_PER_SCOPE
MAX_WARM_ITEMS_PER_SCOPE = _memory_store_ops.MAX_WARM_ITEMS_PER_SCOPE
MAX_ARCHIVED_ITEMS_PER_SCOPE = _memory_store_ops.MAX_ARCHIVED_ITEMS_PER_SCOPE
_MEMORY_SIGNAL_RE = _memory_store_ops._MEMORY_SIGNAL_RE
_env_int = _memory_store_ops._env_int
_env_float = _memory_store_ops._env_float
_is_trivial_turn = _memory_store_ops._is_trivial_turn
_last_human_text = _memory_store_ops._last_human_text
_is_explicit_memory_request = _memory_store_ops._is_explicit_memory_request
_detect_target_language = _memory_store_ops._detect_target_language
_is_task_complete = _memory_store_ops._is_task_complete
_ITEM_ID_PATTERNS = _memory_store_ops._ITEM_ID_PATTERNS
_ITEM_ID_PREFIX = _memory_store_ops._ITEM_ID_PREFIX
_ALLOWED_SCOPE = _memory_store_ops._ALLOWED_SCOPE
_ALLOWED_STATUS = _memory_store_ops._ALLOWED_STATUS
_ALLOWED_CONFIDENCE = _memory_store_ops._ALLOWED_CONFIDENCE
_ALLOWED_TIER = _memory_store_ops._ALLOWED_TIER
_ALLOWED_OPS = _memory_store_ops._ALLOWED_OPS
_INVALID_FACT_REASON_RE = _memory_store_ops._INVALID_FACT_REASON_RE
_iso_now = _memory_store_ops._iso_now
_new_store = _memory_store_ops._new_store
_normalize_scope = _memory_store_ops._normalize_scope
_normalize_status = _memory_store_ops._normalize_status
_normalize_confidence = _memory_store_ops._normalize_confidence
_normalize_tier = _memory_store_ops._normalize_tier
_normalize_score = _memory_store_ops._normalize_score
_derive_tier_from_score = _memory_store_ops._derive_tier_from_score
_normalize_reason = _memory_store_ops._normalize_reason
_raw_reason = _memory_store_ops._raw_reason
_reason_implies_invalid_fact = _memory_store_ops._reason_implies_invalid_fact
_align_score_to_tier = _memory_store_ops._align_score_to_tier
_normalize_text = _memory_store_ops._normalize_text
_format_call_messages_for_log = _memory_store_ops._format_call_messages_for_log
_message_content_to_text = _memory_store_ops._message_content_to_text
_format_messages_for_memory_transcript = _memory_store_ops._format_messages_for_memory_transcript
_normalize_hash = _memory_store_ops._normalize_hash
_read_memory_store = _memory_store_ops._read_memory_store
_write_memory_store = _memory_store_ops._write_memory_store
_next_memory_id = _memory_store_ops._next_memory_id
_build_memory_snapshot = _memory_store_ops._build_memory_snapshot
_normalize_and_validate_operations = _memory_store_ops._normalize_and_validate_operations
_find_item = _memory_store_ops._find_item
_build_invalid_fact_cleanup_operations = _memory_store_ops._build_invalid_fact_cleanup_operations
_build_archived_overflow_operations = _memory_store_ops._build_archived_overflow_operations
_apply_operations = _memory_store_ops._apply_operations
_atomic_write_text = _memory_store_ops._atomic_write_text
_backup_corrupt_store = _memory_store_ops._backup_corrupt_store


class MemoryAgentState(AgentState):
    """Private state fields for MemoryAgentMiddleware."""

    _auto_memory_updated_paths: Annotated[NotRequired[list[str]], PrivateStateAttr]


class MemoryAgentMiddleware(AgentMiddleware):
    """Dedicated memory agent that runs after every non-trivial conversation turn."""

    state_schema = MemoryAgentState

    def __init__(
        self,
        *,
        memory_store_paths: dict[str, str] | None = None,
        context_messages: int | None = None,
        min_turn_interval: int | None = None,
        min_seconds_between_runs: float | None = None,
        file_cooldown_seconds: float | None = None,
    ) -> None:
        if context_messages is None:
            context_messages = _env_int(
                "INVINCAT_MEMORY_CONTEXT_MESSAGES",
                default=0,
                minimum=0,
            )
        if min_turn_interval is None:
            min_turn_interval = _env_int(
                "INVINCAT_MEMORY_MIN_TURN_INTERVAL",
                default=1,
                minimum=1,
            )
        if min_seconds_between_runs is None:
            min_seconds_between_runs = _env_float(
                "INVINCAT_MEMORY_MIN_SECONDS_BETWEEN_RUNS",
                default=0.0,
                minimum=0.0,
            )
        if file_cooldown_seconds is None:
            file_cooldown_seconds = _env_float(
                "INVINCAT_MEMORY_FILE_COOLDOWN_SECONDS",
                default=0.0,
                minimum=0.0,
            )

        resolved_store_paths = (
            {
                scope: str(Path(p).expanduser().resolve())
                for scope, p in memory_store_paths.items()
                if scope in _ALLOWED_SCOPE and isinstance(p, str) and p.strip()
            }
            if memory_store_paths
            else {}
        )
        self._memory_store_paths: dict[str, str] = resolved_store_paths
        self._context_messages = max(0, context_messages)
        self._min_turn_interval = max(1, min_turn_interval)
        self._min_seconds_between_runs = max(0.0, min_seconds_between_runs)
        self._file_cooldown_seconds = max(0.0, file_cooldown_seconds)

        self._allowed_paths: frozenset[str] = frozenset(
            self._memory_store_paths.values()
        )

        self._captured_model: Any = None
        self._memory_model_cache_key: tuple[str, str] | None = None
        self._memory_model_cache_obj: Any = None
        self._turn_index = 0
        self._last_run_turn = 0
        self._last_run_at = 0.0
        self._cursor_by_thread: dict[str, int] = {}
        self._anchor_by_thread: dict[str, str] = {}

    def _is_authorized_path(self, path: Path) -> bool:
        return str(path.expanduser().resolve()) in self._allowed_paths

    def _memory_files_recently_updated(self) -> bool:
        if self._file_cooldown_seconds <= 0:
            return False
        now = time.time()
        for path in self._memory_store_paths.values():
            p = Path(path).expanduser()
            try:
                if p.exists():
                    age = now - p.stat().st_mtime
                    if age < self._file_cooldown_seconds:
                        return True
            except OSError:
                continue
        return False

    @staticmethod
    def _last_human_text(messages: list[Any]) -> str:
        return _last_human_text(messages)

    @staticmethod
    def _message_anchor(message: Any) -> str:
        msg_type = getattr(message, "type", "")
        content = getattr(message, "content", "")
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            )
        text = str(content)
        tool_calls = getattr(message, "tool_calls", None)
        return f"{msg_type}|{len(text)}|{text[:160]}|{bool(tool_calls)}"

    @staticmethod
    def _resolve_thread_id() -> str:
        try:
            cfg = get_config()
            configurable = cfg.get("configurable", {})
            thread_id = configurable.get("thread_id")
            if isinstance(thread_id, str) and thread_id.strip():
                return thread_id
        except Exception:
            logger.debug("Memory agent: failed to resolve thread_id", exc_info=True)
        return "__default_thread__"

    def _slice_incremental_messages(
        self, thread_id: str, messages: list[Any]
    ) -> list[Any]:
        if not messages:
            return []

        cursor = self._cursor_by_thread.get(thread_id, 0)
        if cursor <= 0:
            return list(messages)
        if cursor > len(messages):
            logger.debug(
                "Memory agent: cursor reset for %s (cursor=%d > len=%d)",
                thread_id,
                cursor,
                len(messages),
            )
            return list(messages)

        anchor = self._anchor_by_thread.get(thread_id)
        if anchor and cursor - 1 >= 0:
            current_anchor = self._message_anchor(messages[cursor - 1])
            if current_anchor != anchor:
                logger.debug(
                    "Memory agent: cursor reset for %s (history changed before cursor)",
                    thread_id,
                )
                return list(messages)
        return list(messages[cursor:])

    def _advance_cursor(self, thread_id: str, messages: list[Any]) -> None:
        self._cursor_by_thread[thread_id] = len(messages)
        if messages:
            self._anchor_by_thread[thread_id] = self._message_anchor(messages[-1])
        else:
            self._anchor_by_thread.pop(thread_id, None)

    def _should_run_for_turn(self, messages: list[Any]) -> bool:
        from invincat_cli.memory.agent_runtime import should_run_for_turn

        return should_run_for_turn(self, messages)

    def _load_or_recover_store(
        self, scope: str, thread_id: str, source_anchor: str
    ) -> dict[str, Any] | None:
        from invincat_cli.memory.agent_store import load_or_recover_store

        return load_or_recover_store(self, scope, thread_id, source_anchor)

    async def _apply_and_write_memory_operations(
        self,
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
        from invincat_cli.memory.agent_store import apply_and_write_memory_operations

        return await apply_and_write_memory_operations(
            self,
            user_store,
            project_store,
            user_before,
            project_before,
            operations,
            thread_id=thread_id,
            source_anchor=source_anchor,
            now_iso=now_iso,
        )

    async def _cleanup_invalid_fact_stores(
        self,
        *,
        thread_id: str,
        source_anchor: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
        from invincat_cli.memory.agent_store import cleanup_invalid_fact_stores

        return await cleanup_invalid_fact_stores(
            self,
            thread_id=thread_id,
            source_anchor=source_anchor,
        )

    async def _extract_and_write(
        self,
        model: Any,
        messages: list[Any],
        *,
        thread_id: str,
        source_anchor: str,
        preloaded_stores: tuple[dict[str, Any] | None, dict[str, Any] | None]
        | None = None,
    ) -> list[str] | None:
        from invincat_cli.memory.agent_extraction import extract_and_write

        return await extract_and_write(
            self,
            model,
            messages,
            thread_id=thread_id,
            source_anchor=source_anchor,
            preloaded_stores=preloaded_stores,
        )

    @staticmethod
    def _emit_memory_status(runtime: Any, status: str) -> None:
        try:
            writer = getattr(runtime, "stream_writer", None)
            if callable(writer):
                writer({"event": "memory_agent", "status": status})
        except Exception:
            logger.debug(
                "Memory agent: failed to emit status=%s", status, exc_info=True
            )

    def _resolve_memory_model(self, runtime: Any, fallback_model: Any) -> Any:
        from invincat_cli.memory.agent_runtime import resolve_memory_model

        return resolve_memory_model(self, runtime, fallback_model)

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        self._captured_model = request.model
        return handler(request)

    async def awrap_model_call(
        self, request: ModelRequest, handler: Any
    ) -> ModelResponse:
        self._captured_model = request.model
        return await handler(request)

    async def aafter_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        try:
            logger.debug("Memory agent: aafter_agent called")
            primary_model = self._captured_model
            if primary_model is None:
                return None
            model = self._resolve_memory_model(runtime, primary_model)
            if state.get("__interrupt__"):
                logger.debug("Memory agent: skipping extraction — pending interrupts")
                return None

            messages = state.get("messages", [])
            if not messages:
                return None
            if not _is_task_complete(messages):
                logger.debug("Memory agent: skipping extraction — task not complete")
                return None

            thread_id = self._resolve_thread_id()
            cleanup_source_anchor = self._message_anchor(messages[-1])
            (
                cleaned_user,
                cleaned_project,
                cleanup_written,
            ) = await self._cleanup_invalid_fact_stores(
                thread_id=thread_id,
                source_anchor=cleanup_source_anchor,
            )
            cleanup_written = list(dict.fromkeys(cleanup_written))

            if _is_trivial_turn(messages):
                logger.debug("Memory agent: skipping trivial turn")
                if cleanup_written:
                    self._advance_cursor(thread_id, messages)
                    return {
                        "memory_contents": None,
                        "_auto_memory_updated_paths": cleanup_written,
                    }
                return None
            if not self._should_run_for_turn(messages):
                if cleanup_written:
                    return {
                        "memory_contents": None,
                        "_auto_memory_updated_paths": cleanup_written,
                    }
                return None

            incremental = self._slice_incremental_messages(thread_id, messages)
            if not incremental:
                if cleanup_written:
                    return {
                        "memory_contents": None,
                        "_auto_memory_updated_paths": cleanup_written,
                    }
                return None

            if self._context_messages <= 0:
                recent = incremental
            else:
                recent = incremental[-self._context_messages :]
                human_indices = [
                    i
                    for i, m in enumerate(messages)
                    if getattr(m, "type", "") == "human"
                ]
                if human_indices:
                    last_human_idx = human_indices[-1]
                    window_start = len(messages) - self._context_messages
                    if last_human_idx < window_start:
                        recent = [messages[last_human_idx]] + list(recent)

            source_anchor = self._message_anchor(recent[-1]) if recent else ""
            self._emit_memory_status(runtime, "running")
            try:
                written = await self._safe_extract_and_write(
                    model,
                    recent,
                    thread_id=thread_id,
                    source_anchor=source_anchor,
                    preloaded_stores=(cleaned_user, cleaned_project),
                )
            finally:
                self._emit_memory_status(runtime, "done")
            if written is None:
                logger.debug("Memory agent: extraction failed, cursor is not advanced")
                if cleanup_written:
                    return {
                        "memory_contents": None,
                        "_auto_memory_updated_paths": cleanup_written,
                    }
                return None
            self._advance_cursor(thread_id, messages)
            combined_written = list(dict.fromkeys([*cleanup_written, *written]))
            if combined_written:
                return {
                    "memory_contents": None,
                    "_auto_memory_updated_paths": combined_written,
                }
            return None
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Memory agent: aafter_agent failed unexpectedly")
            return None

    async def _safe_extract_and_write(
        self,
        model: Any,
        messages: list[Any],
        *,
        thread_id: str,
        source_anchor: str,
        preloaded_stores: tuple[dict[str, Any] | None, dict[str, Any] | None]
        | None = None,
    ) -> list[str] | None:
        try:
            return await self._extract_and_write(
                model,
                messages,
                thread_id=thread_id,
                source_anchor=source_anchor,
                preloaded_stores=preloaded_stores,
            )
        except asyncio.CancelledError:
            logger.debug(
                "Memory agent: extraction cancelled — re-scheduling task cancellation"
            )
            current = asyncio.current_task()
            if current is not None:
                current.cancel()
            return None
