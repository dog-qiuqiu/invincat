"""Dedicated memory agent middleware with structured memory stores.

Runs an independent model call with a focused system prompt after every
non-trivial conversation turn to extract durable memory operations.

The extraction runs in ``aafter_agent`` so it does not block the user from
receiving the main response.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import tempfile
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
)
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_config

logger = logging.getLogger(__name__)

MAX_OPERATIONS_PER_RUN = 8
MAX_ITEM_CONTENT_CHARS = 500
MAX_SECTION_NAME_CHARS = 80
_MAX_OUTPUT_TOKENS = 2000
_MAX_CONVERSATION_CHARS = 1500

_MEMORY_SIGNAL_RE = re.compile(
    r"\b("
    r"always|never|prefer|preference|style|convention|rule|guideline|"
    r"remember|remember this|best practice|pattern|decision|constraint"
    r")\b|"
    r"(记住|偏好|规范|约定|规则|风格|最佳实践|约束|决策)",
    re.IGNORECASE,
)

_TRIVIAL_RE = re.compile(
    r"^\s*("
    r"ok|okay|thanks|thank you|got it|sure|yes|no|confirmed|done|"
    r"continue|go ahead|proceed|sounds good|great|perfect|nice|"
    r"好的|谢谢|明白|知道了|好|嗯|是的|对|继续|好的好的|没问题|可以|"
    r"收到|了解|行|嗯嗯|好的收到"
    r")\s*[.!?。！？]?\s*$",
    re.IGNORECASE,
)

_ITEM_ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "user": re.compile(r"^mem_u_(\d{6})$"),
    "project": re.compile(r"^mem_p_(\d{6})$"),
}
_ITEM_ID_PREFIX: dict[str, str] = {"user": "mem_u_", "project": "mem_p_"}
_ALLOWED_SCOPE: frozenset[str] = frozenset({"user", "project"})
_ALLOWED_STATUS: frozenset[str] = frozenset({"active", "archived"})
_ALLOWED_CONFIDENCE: frozenset[str] = frozenset({"low", "medium", "high"})
_ALLOWED_OPS: frozenset[str] = frozenset({"create", "update", "archive", "noop"})

_SYSTEM_PROMPT = """\
You are a memory curator for an AI assistant.

You receive:
1) recent_conversation
2) memory_snapshot (structured memory items with stable IDs)

Return STRICT JSON only:
{
  "operations": [
    {"op": "create", "scope": "user|project", "section": "...", "content": "...", "confidence": "low|medium|high"},
    {"op": "update", "scope": "user|project", "id": "mem_u_000001", "content": "...", "confidence": "low|medium|high"},
    {"op": "archive", "scope": "user|project", "id": "mem_p_000031", "reason": "..."},
    {"op": "noop"}
  ]
}

## Scope

- user: applies to this person regardless of project (style, habits, tool preferences, communication)
- project: specific to this codebase (dependencies, architecture, conventions, key file locations)

When in doubt: if the fact would still be true in a different project, use user scope.

## Section names

Use ONLY these section names (pick the closest match):
- Coding Style      (formatting rules, naming conventions, type hints, comments)
- Tech Stack        (languages, frameworks, libraries, tools in active use)
- Project Context   (architecture patterns, key files, module responsibilities)
- Workflow          (git habits, testing approach, review process, deploy steps)
- Communication     (response verbosity, language preference, explanation depth)
- Constraints       (things never to do, hard limits, explicit prohibitions)

## Confidence

- high: user stated this explicitly ("always use X", "I want Y", "never do Z")
- medium: strongly implied by repeated behavior or clear pattern (did X multiple times)
- low: inferred from single occurrence, may be context-specific

## Rules

- Output only JSON, no prose, no markdown fences.
- Prefer update/archive over duplicate create when an existing item already matches.
- Do NOT output full markdown memory files.
- Do NOT output file paths.
- Do NOT invent IDs for create.
- Keep content specific and actionable — skip vague or transient details.
- One fact per item. Do not bundle multiple unrelated facts into one content string.
"""

_USER_TEMPLATE = """\
Recent conversation:
{conversation}

Memory snapshot:
{snapshot}

Return operations JSON only.
"""


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def _is_trivial_turn(messages: list[Any]) -> bool:
    """Return True when the last user message carries no extractable information."""
    user_msgs = [m for m in messages if getattr(m, "type", "") == "human"]
    if not user_msgs:
        return True
    content = getattr(user_msgs[-1], "content", "")
    if isinstance(content, list):
        content = " ".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        )
    text = str(content).strip()
    if not text:
        return True
    # Short user messages can still be memory-worthy (especially in Chinese).
    if _MEMORY_SIGNAL_RE.search(text):
        return False
    return bool(_TRIVIAL_RE.match(text))


def _is_task_complete(messages: list[Any]) -> bool:
    """Return True when all tool calls have completed and AI has given final response."""
    if not messages:
        return False

    last_msg = messages[-1]
    msg_type = getattr(last_msg, "type", "")

    if msg_type == "tool":
        return False

    if msg_type == "ai":
        tool_calls = getattr(last_msg, "tool_calls", None)
        if tool_calls:
            return False
        return True

    return False


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_store(scope: str) -> dict[str, Any]:
    return {"version": 1, "scope": scope, "items": []}


def _normalize_scope(scope: Any) -> str | None:
    if not isinstance(scope, str):
        return None
    normalized = scope.strip().lower()
    if normalized in _ALLOWED_SCOPE:
        return normalized
    return None


def _normalize_status(status: Any) -> str:
    if isinstance(status, str):
        normalized = status.strip().lower()
        if normalized in _ALLOWED_STATUS:
            return normalized
    return "active"


def _normalize_confidence(value: Any, default: str = "medium") -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _ALLOWED_CONFIDENCE:
            return normalized
    return default


def _normalize_text(value: Any, *, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.strip())[:max_chars]


def _normalize_hash(section: str, content: str) -> str:
    return f"{section.strip().casefold()}::{content.strip().casefold()}"


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
    if isinstance(data.get("scope"), str) and _normalize_scope(data.get("scope")) is None:
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
        section = _normalize_text(raw.get("section", ""), max_chars=MAX_SECTION_NAME_CHARS)
        content = _normalize_text(raw.get("content", ""), max_chars=MAX_ITEM_CONTENT_CHARS)
        if not section or not content:
            continue
        status = _normalize_status(raw.get("status"))
        created_at = raw.get("created_at") if isinstance(raw.get("created_at"), str) else _iso_now()
        updated_at = raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else created_at
        archived_at = raw.get("archived_at") if isinstance(raw.get("archived_at"), str) else None
        source_thread_id = (
            raw.get("source_thread_id")
            if isinstance(raw.get("source_thread_id"), str)
            else "__default_thread__"
        )
        source_anchor = raw.get("source_anchor") if isinstance(raw.get("source_anchor"), str) else ""
        confidence = _normalize_confidence(raw.get("confidence"), default="medium")

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
                "norm_hash": _normalize_hash(section, content),
            }
        )

    return store


def _write_memory_store(path: Path, store: dict[str, Any]) -> None:
    # Internal guard flags are runtime-only and must not be persisted.
    write_store = {k: v for k, v in store.items() if not str(k).startswith("__")}
    payload = json.dumps(write_store, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(path, payload)


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


def _build_memory_snapshot(
    user_store: dict[str, Any] | None,
    project_store: dict[str, Any] | None,
) -> dict[str, Any]:
    def _items_for_snapshot(store: dict[str, Any] | None) -> list[dict[str, Any]]:
        if store is None:
            return []
        items: list[dict[str, Any]] = []
        for item in store.get("items", []):
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "id": item.get("id"),
                    "section": item.get("section"),
                    "content": item.get("content"),
                    "status": item.get("status"),
                }
            )
        items.sort(
            key=lambda x: (
                str(x.get("section", "")).casefold(),
                str(x.get("id", "")),
            )
        )
        return items

    return {
        "user": _items_for_snapshot(user_store),
        "project": _items_for_snapshot(project_store),
    }


def _normalize_and_validate_operations(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw_ops = payload.get("operations", [])
    if not isinstance(raw_ops, list):
        return []
    if len(raw_ops) > MAX_OPERATIONS_PER_RUN:
        raw_ops = raw_ops[:MAX_OPERATIONS_PER_RUN]

    normalized: list[dict[str, Any]] = []
    for raw in raw_ops:
        if not isinstance(raw, dict):
            continue
        op = raw.get("op")
        if not isinstance(op, str):
            continue
        op = op.strip().lower()
        if op not in _ALLOWED_OPS:
            continue
        if op == "noop":
            normalized.append({"op": "noop"})
            continue

        scope = _normalize_scope(raw.get("scope"))
        if scope is None:
            continue

        if op == "create":
            if raw.get("id") not in (None, ""):
                continue
            section = _normalize_text(raw.get("section"), max_chars=MAX_SECTION_NAME_CHARS)
            content = _normalize_text(raw.get("content"), max_chars=MAX_ITEM_CONTENT_CHARS)
            if not section or not content:
                continue
            confidence = _normalize_confidence(raw.get("confidence"), default="high")
            normalized.append(
                {
                    "op": "create",
                    "scope": scope,
                    "section": section,
                    "content": content,
                    "confidence": confidence,
                }
            )
            continue

        if op == "update":
            item_id = raw.get("id")
            if not isinstance(item_id, str) or not item_id.strip():
                continue
            content = _normalize_text(raw.get("content"), max_chars=MAX_ITEM_CONTENT_CHARS)
            if not content:
                continue
            confidence = _normalize_confidence(raw.get("confidence"), default="high")
            normalized.append(
                {
                    "op": "update",
                    "scope": scope,
                    "id": item_id.strip(),
                    "content": content,
                    "confidence": confidence,
                }
            )
            continue

        if op == "archive":
            item_id = raw.get("id")
            if not isinstance(item_id, str) or not item_id.strip():
                continue
            reason = _normalize_text(raw.get("reason"), max_chars=120)
            normalized.append(
                {
                    "op": "archive",
                    "scope": scope,
                    "id": item_id.strip(),
                    "reason": reason or None,
                }
            )

    return normalized


def _find_item(store: dict[str, Any] | None, item_id: str) -> dict[str, Any] | None:
    if store is None:
        return None
    for item in store.get("items", []):
        if isinstance(item, dict) and item.get("id") == item_id:
            return item
    return None


def _active_items(store: dict[str, Any] | None) -> list[dict[str, Any]]:
    if store is None:
        return []
    return [
        item
        for item in store.get("items", [])
        if isinstance(item, dict) and item.get("status") == "active"
    ]


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

    # Archive ratio guard (per-scope).
    archive_target_count: dict[str, int] = {"user": 0, "project": 0}
    for op in operations:
        if op.get("op") != "archive":
            continue
        scope = op.get("scope")
        item_id = op.get("id")
        if not isinstance(scope, str) or not isinstance(item_id, str):
            continue
        if item_id in conflicted_ids:
            continue
        store = user_store if scope == "user" else project_store
        item = _find_item(store, item_id)
        if item is not None and item.get("status") == "active":
            archive_target_count[scope] += 1

    archive_blocked_scope: set[str] = set()
    for scope in ("user", "project"):
        store = user_store if scope == "user" else project_store
        active_total = len(_active_items(store))
        if active_total <= 0:
            continue
        allowed = max(1, math.floor(active_total * 0.2))
        if archive_target_count[scope] > allowed:
            archive_blocked_scope.add(scope)

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
        if op_name == "archive" and scope in archive_blocked_scope:
            continue

        store = _get_or_create_store(scope)
        if op_name == "create":
            section = str(op["section"])
            content = str(op["content"])
            duplicate = any(
                item.get("status") == "active" and item.get("content", "").strip() == content
                for item in store.get("items", [])
                if isinstance(item, dict)
            )
            if duplicate:
                continue
            item_id = _next_memory_id(store, scope)
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
                    "confidence": _normalize_confidence(op.get("confidence"), default="high"),
                    "norm_hash": _normalize_hash(section, content),
                }
            )
            changed_scopes.add(scope)
            continue

        item_id = op.get("id")
        if not isinstance(item_id, str) or item_id in conflicted_ids:
            continue
        item = _find_item(store, item_id)
        if item is None:
            continue
        if op_name == "update":
            content = str(op["content"]).strip()
            if not content:
                continue
            item["content"] = content
            item["updated_at"] = now_iso
            item["source_thread_id"] = thread_id
            item["source_anchor"] = source_anchor
            item["confidence"] = _normalize_confidence(op.get("confidence"), default="high")
            item["norm_hash"] = _normalize_hash(str(item.get("section", "")), content)
            if item.get("status") == "archived":
                item["status"] = "active"
                item["archived_at"] = None
            changed_scopes.add(scope)
        elif op_name == "archive":
            if item.get("status") == "archived":
                continue
            item["status"] = "archived"
            item["updated_at"] = now_iso
            item["archived_at"] = now_iso
            item["source_thread_id"] = thread_id
            item["source_anchor"] = source_anchor
            changed_scopes.add(scope)

    return user_store, project_store, sorted(changed_scopes)


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
        logger.warning("Memory agent: failed to back up unreadable store %s", path, exc_info=True)
        return None


class MemoryAgentState(AgentState):
    """Private state fields for MemoryAgentMiddleware."""

    _auto_memory_updated_paths: Annotated[NotRequired[list[str]], PrivateStateAttr]


class MemoryAgentMiddleware(AgentMiddleware):
    """Dedicated memory agent that runs after every non-trivial conversation turn."""

    state_schema = MemoryAgentState

    def __init__(
        self,
        *,
        memory_paths: list[str],
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
                default=2,
                minimum=1,
            )
        if min_seconds_between_runs is None:
            min_seconds_between_runs = _env_float(
                "INVINCAT_MEMORY_MIN_SECONDS_BETWEEN_RUNS",
                default=8.0,
                minimum=0.0,
            )
        if file_cooldown_seconds is None:
            file_cooldown_seconds = _env_float(
                "INVINCAT_MEMORY_FILE_COOLDOWN_SECONDS",
                default=5.0,
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

        self._allowed_paths: frozenset[str] = frozenset(self._memory_store_paths.values())

        self._captured_model: Any = None
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
        for msg in reversed(messages):
            if getattr(msg, "type", "") != "human":
                continue
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                parts = [
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                ]
                return " ".join(filter(None, parts)).strip()
            return str(content).strip()
        return ""

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

    def _slice_incremental_messages(self, thread_id: str, messages: list[Any]) -> list[Any]:
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
        self._turn_index += 1
        turns_since_last = self._turn_index - self._last_run_turn
        interval_due = turns_since_last >= self._min_turn_interval
        human_text = self._last_human_text(messages)
        signal_match = bool(_MEMORY_SIGNAL_RE.search(human_text))

        if self._min_seconds_between_runs > 0:
            elapsed = time.monotonic() - self._last_run_at
            if elapsed < self._min_seconds_between_runs and not signal_match:
                logger.debug(
                    "Memory agent: throttled by wall-clock cooldown (%.2fs < %.2fs)",
                    elapsed,
                    self._min_seconds_between_runs,
                )
                return False

        if self._memory_files_recently_updated() and not signal_match:
            logger.debug("Memory agent: throttled by file-update cooldown")
            return False

        if interval_due or signal_match:
            return True

        logger.debug(
            "Memory agent: throttled by turn interval (%d < %d)",
            turns_since_last,
            self._min_turn_interval,
        )
        return False

    @staticmethod
    def _format_messages(messages: list[Any]) -> str:
        lines: list[str] = []
        for msg in messages:
            role = getattr(msg, "type", "unknown")
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                text_parts = [
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                    if not (
                        isinstance(p, dict)
                        and p.get("type") in ("tool_use", "tool_result")
                    )
                ]
                content = " ".join(filter(None, text_parts))
            if content:
                lines.append(f"{role}: {str(content)[:_MAX_CONVERSATION_CHARS]}")
        return "\n".join(lines)

    def _load_or_recover_store(
        self, scope: str, thread_id: str, source_anchor: str
    ) -> dict[str, Any] | None:
        del thread_id, source_anchor
        store_path_raw = self._memory_store_paths.get(scope)
        if not store_path_raw:
            return None
        store_path = Path(store_path_raw).expanduser().resolve()
        if store_path.exists():
            store = _read_memory_store(store_path, scope)
            if not store.get("__read_error__"):
                return store
            logger.warning("Memory agent: attempting auto-recovery for unreadable %s store", scope)
            backup = _backup_corrupt_store(store_path)
            if backup is not None:
                logger.warning(
                    "Memory agent: backed up unreadable store to %s before recovery",
                    backup,
                )
        store = _new_store(scope)
        if self._is_authorized_path(store_path):
            _write_memory_store(store_path, store)
        return store

    async def _extract_and_write(
        self,
        model: Any,
        messages: list[Any],
        *,
        thread_id: str,
        source_anchor: str,
    ) -> list[str] | None:
        written_store_paths: list[str] = []
        try:
            conversation = self._format_messages(messages)
            user_store = await asyncio.to_thread(
                self._load_or_recover_store, "user", thread_id, source_anchor
            )
            project_store = await asyncio.to_thread(
                self._load_or_recover_store, "project", thread_id, source_anchor
            )
            unreadable_scopes: list[str] = []
            if isinstance(user_store, dict) and user_store.get("__read_error__"):
                unreadable_scopes.append("user")
            if isinstance(project_store, dict) and project_store.get("__read_error__"):
                unreadable_scopes.append("project")
            if unreadable_scopes:
                logger.warning(
                    "Memory agent: skip write because store is unreadable (scopes=%s)",
                    ",".join(unreadable_scopes),
                )
                return []
            user_before = deepcopy(user_store)
            project_before = deepcopy(project_store)
            snapshot = _build_memory_snapshot(user_store, project_store)

            response = await model.bind(max_tokens=_MAX_OUTPUT_TOKENS).ainvoke(
                [
                    SystemMessage(content=_SYSTEM_PROMPT),
                    HumanMessage(
                        content=_USER_TEMPLATE.format(
                            conversation=conversation,
                            snapshot=json.dumps(snapshot, ensure_ascii=False, indent=2),
                        )
                    ),
                ],
                config={"metadata": {"lc_source": "memory_agent"}},
            )

            raw: str = response.content
            if isinstance(raw, list):
                raw = " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in raw
                )
            start = raw.find("{")
            if start == -1:
                logger.debug("Memory agent: no JSON found in response")
                return []
            data, _ = json.JSONDecoder().raw_decode(raw, start)
            operations = _normalize_and_validate_operations(data)
            if not operations:
                return []

            now_iso = _iso_now()
            new_user, new_project, changed_scopes = _apply_operations(
                user_store,
                project_store,
                operations,
                thread_id=thread_id,
                source_anchor=source_anchor,
                now_iso=now_iso,
            )
            if not changed_scopes:
                return []

            for scope in changed_scopes:
                store = new_user if scope == "user" else new_project
                before_store = user_before if scope == "user" else project_before
                if store is None:
                    continue

                store_path_raw = self._memory_store_paths.get(scope)
                if not store_path_raw:
                    continue
                store_path = Path(store_path_raw).expanduser().resolve()
                if not self._is_authorized_path(store_path):
                    logger.warning("Memory agent: rejected unauthorized write for %s scope", scope)
                    continue

                before_items = (
                    before_store.get("items", [])
                    if isinstance(before_store, dict)
                    else []
                )
                if before_items and not _active_items(store):
                    logger.warning(
                        "Memory agent: refusing full-active wipe for non-empty %s store",
                        scope,
                    )
                    continue

                await asyncio.to_thread(_write_memory_store, store_path, store)
                written_store_paths.append(str(store_path))

            if written_store_paths:
                self._last_run_turn = self._turn_index
                self._last_run_at = time.monotonic()
            return written_store_paths

        except json.JSONDecodeError:
            logger.debug("Memory agent: model returned malformed JSON", exc_info=True)
            return []
        except Exception:
            logger.warning("Memory agent extraction failed unexpectedly", exc_info=True)
            return None

    @staticmethod
    def _emit_memory_status(runtime: Any, status: str) -> None:
        try:
            writer = getattr(runtime, "stream_writer", None)
            if callable(writer):
                writer({"event": "memory_agent", "status": status})
        except Exception:
            logger.debug("Memory agent: failed to emit status=%s", status, exc_info=True)

    def wrap_model_call(
        self, request: ModelRequest, handler: Any
    ) -> ModelResponse:
        self._captured_model = request.model
        return handler(request)

    async def awrap_model_call(
        self, request: ModelRequest, handler: Any
    ) -> ModelResponse:
        self._captured_model = request.model
        return await handler(request)

    async def aafter_agent(
        self, state: Any, runtime: Any
    ) -> dict[str, Any] | None:
        try:
            logger.debug("Memory agent: aafter_agent called")
            model = self._captured_model
            if model is None:
                return None
            if state.get("__interrupt__"):
                logger.debug("Memory agent: skipping extraction — pending interrupts")
                return None

            messages = state.get("messages", [])
            if not messages:
                return None
            if not _is_task_complete(messages):
                logger.debug("Memory agent: skipping extraction — task not complete")
                return None
            if _is_trivial_turn(messages):
                logger.debug("Memory agent: skipping trivial turn")
                return None
            if not self._should_run_for_turn(messages):
                return None

            thread_id = self._resolve_thread_id()
            incremental = self._slice_incremental_messages(thread_id, messages)
            if not incremental:
                return None

            if self._context_messages <= 0:
                recent = incremental
            else:
                recent = incremental[-self._context_messages :]

            if self._context_messages > 0:
                human_indices = [
                    i for i, m in enumerate(messages) if getattr(m, "type", "") == "human"
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
                )
            finally:
                self._emit_memory_status(runtime, "done")
            if written is None:
                logger.debug("Memory agent: extraction failed, cursor is not advanced")
                return None
            self._advance_cursor(thread_id, messages)
            if written:
                return {
                    "memory_contents": None,
                    "_auto_memory_updated_paths": written,
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
    ) -> list[str] | None:
        try:
            return await self._extract_and_write(
                model,
                messages,
                thread_id=thread_id,
                source_anchor=source_anchor,
            )
        except asyncio.CancelledError:
            logger.debug(
                "Memory agent: extraction cancelled — re-scheduling task cancellation"
            )
            current = asyncio.current_task()
            if current is not None:
                current.cancel()
            return None
