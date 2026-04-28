"""Memory middleware for loading and refreshing structured JSON memory stores."""

from __future__ import annotations

import json
import logging
import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, NotRequired

from deepagents.middleware.memory import append_to_system_message
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
)
from langchain_core.messages import SystemMessage
from invincat_cli.memory_agent import (
    MAX_HOT_ITEMS_PER_SCOPE as _MAX_HOT_ITEMS_PER_SCOPE,
)
from invincat_cli.memory_agent import (
    MAX_WARM_ITEMS_PER_SCOPE as _MAX_WARM_ITEMS_PER_SCOPE,
)

logger = logging.getLogger(__name__)

_MEMORY_INJECTION_TEMPLATE = """<agent_memory>
{agent_memory}
</agent_memory>
"""
_MAX_SCOPE_RENDER_CHARS = 4000
_MAX_TOTAL_INJECTION_CHARS = 8000
_ALLOWED_ITEM_STATUS = {"active", "archived"}
_ALLOWED_ITEM_TIER = {"hot", "warm", "cold"}


class RefreshableMemoryState(AgentState):
    """Private state fields used by RefreshableMemoryMiddleware."""

    memory_contents: Annotated[NotRequired[dict[str, str]], PrivateStateAttr]


def _normalize_text(value: Any, *, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:max_chars]


def _is_valid_store_item(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    item_id = raw.get("id")
    section = raw.get("section")
    content = raw.get("content")
    status = raw.get("status")
    if not isinstance(item_id, str) or not item_id.strip():
        return False
    if not isinstance(section, str) or not section.strip():
        return False
    if not isinstance(content, str) or not content.strip():
        return False
    if not isinstance(status, str) or status.strip().lower() not in _ALLOWED_ITEM_STATUS:
        return False
    return True


def _normalize_item_tier(raw: Any) -> str:
    if isinstance(raw, str):
        tier = raw.strip().lower()
        if tier in _ALLOWED_ITEM_TIER:
            return tier
    return "warm"


def _normalize_item_score(raw: Any) -> int:
    try:
        score = int(raw)
    except (TypeError, ValueError):
        score = 50
    return max(0, min(100, score))


def _select_items_for_injection(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (hot_items, warm_items) sorted by score descending, cold excluded."""
    normalized: list[dict[str, Any]] = []
    for raw in items:
        if not _is_valid_store_item(raw):
            continue
        if str(raw.get("status", "")).strip().lower() != "active":
            continue
        tier = _normalize_item_tier(raw.get("tier"))
        if tier == "cold":
            continue
        normalized.append(
            {
                "id": str(raw.get("id", "")).strip(),
                "section": _normalize_text(raw.get("section") or "Imported Notes", max_chars=80)
                or "Imported Notes",
                "content": _normalize_text(raw.get("content"), max_chars=500),
                "tier": tier,
                "score": _normalize_item_score(raw.get("score")),
            }
        )

    hot_items = sorted(
        (item for item in normalized if item["tier"] == "hot"),
        key=lambda item: (-int(item["score"]), str(item["id"])),
    )[:_MAX_HOT_ITEMS_PER_SCOPE]
    warm_items = sorted(
        (item for item in normalized if item["tier"] == "warm"),
        key=lambda item: (-int(item["score"]), str(item["id"])),
    )[:_MAX_WARM_ITEMS_PER_SCOPE]
    return hot_items, warm_items


def _render_store_content(store: dict[str, Any], *, max_chars: int = _MAX_SCOPE_RENDER_CHARS) -> str:
    items = store.get("items", [])
    if not isinstance(items, list):
        return ""
    hot_items, warm_items = _select_items_for_injection(items)
    if not hot_items and not warm_items:
        return ""

    lines: list[str] = []
    used_chars = 0

    def _try_append(line: str) -> bool:
        nonlocal used_chars
        line_len = len(line) + 1
        if used_chars + line_len > max_chars:
            return False
        lines.append(line)
        used_chars += line_len
        return True

    if hot_items:
        _try_append("### Always Apply")
        for item in hot_items:
            if not _try_append(f"- {item['section']}: {item['content']}"):
                break

    if warm_items:
        if hot_items:
            _try_append("")
        _try_append("### When Relevant")
        for item in warm_items:
            if not _try_append(f"- {item['section']}: {item['content']}"):
                break

    return "\n".join(lines).strip()


def _store_content_if_valid(path: Path) -> tuple[bool, str]:
    """Return (is_valid_store, rendered_memory_content)."""
    if not path.exists():
        return False, ""
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("Memory store unreadable at %s; skipping store content", path)
        return False, ""

    if not isinstance(data, dict):
        logger.warning("Memory store schema invalid at %s; skipping store content", path)
        return False, ""
    if not isinstance(data.get("items"), list):
        logger.warning("Memory store items invalid at %s; skipping store content", path)
        return False, ""

    rendered = _render_store_content(data)
    return True, rendered


class RefreshableMemoryMiddleware(AgentMiddleware):
    """Memory middleware backed by `memory_*.json` stores.

    Loads and renders structured memory stores into markdown snippets, keeps
    the result in `memory_contents`, and injects it into the system prompt.
    Reloads when `memory_contents` is absent or None.
    """

    state_schema = RefreshableMemoryState

    def __init__(
        self,
        *,
        backend: Any,  # kept for backward-compatible constructor shape
        sources: list[str] | None = None,  # deprecated
        memory_store_paths: dict[str, str] | None = None,
    ) -> None:
        self.sources = sources or []
        self._memory_store_paths: dict[str, str] = {}
        for scope in ("user", "project"):
            raw = (memory_store_paths or {}).get(scope)
            if isinstance(raw, str) and raw.strip():
                self._memory_store_paths[scope] = str(Path(raw).expanduser().resolve())
        self._last_store_signatures: dict[str, tuple[bool, int, int]] | None = None
        self._cached_contents: dict[str, str] = {}

    def _snapshot_store_signatures(self) -> dict[str, tuple[bool, int, int]]:
        signatures: dict[str, tuple[bool, int, int]] = {}
        for scope, store_path in self._memory_store_paths.items():
            path = Path(store_path)
            try:
                stat = path.stat()
                signatures[scope] = (True, int(stat.st_mtime_ns), int(stat.st_size))
            except OSError:
                signatures[scope] = (False, 0, 0)
        return signatures

    def _load_memory_contents_if_needed(self, *, force: bool = False) -> dict[str, str]:
        signatures = self._snapshot_store_signatures()
        if not force and self._last_store_signatures == signatures:
            return dict(self._cached_contents)
        contents = self._load_memory_contents()
        self._last_store_signatures = signatures
        self._cached_contents = dict(contents)
        return contents

    def _load_memory_contents(self) -> dict[str, str]:
        contents: dict[str, str] = {}
        total_chars = 0
        for scope in ("user", "project"):
            store_path = self._memory_store_paths.get(scope)
            if not store_path:
                continue
            is_valid_store, rendered = _store_content_if_valid(Path(store_path))
            if not is_valid_store:
                continue
            key = "User Memory" if scope == "user" else "Project Memory"
            if rendered:
                remaining = _MAX_TOTAL_INJECTION_CHARS - total_chars
                if remaining <= 0:
                    break
                clipped = rendered[:remaining]
                if clipped.strip():
                    contents[key] = clipped
                    total_chars += len(clipped)
        return contents

    @staticmethod
    def _format_agent_memory(contents: dict[str, str]) -> str:
        if not contents:
            return _MEMORY_INJECTION_TEMPLATE.format(agent_memory="(No memory loaded)")

        sections = [f"{name}\n{body}" for name, body in contents.items() if body]
        if not sections:
            return _MEMORY_INJECTION_TEMPLATE.format(agent_memory="(No memory loaded)")
        return _MEMORY_INJECTION_TEMPLATE.format(agent_memory="\n\n".join(sections))

    def before_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        logger.debug("Refreshing memory contents")
        return {"memory_contents": self._load_memory_contents_if_needed(force=True)}

    async def abefore_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        logger.debug("Refreshing memory contents (async)")
        contents = await asyncio.to_thread(self._load_memory_contents_if_needed, force=True)
        return {"memory_contents": contents}

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        contents = self._load_memory_contents_if_needed(force=False)
        memory_block = self._format_agent_memory(contents)
        new_system: SystemMessage = append_to_system_message(request.system_message, memory_block)
        return handler(request.override(system_message=new_system))

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        raw_state = request.state
        has_state_memory = False
        contents: Any = None
        if isinstance(raw_state, Mapping):
            has_state_memory = "memory_contents" in raw_state
            contents = raw_state.get("memory_contents") if has_state_memory else None
        elif hasattr(raw_state, "get"):
            try:
                contents = raw_state.get("memory_contents")
                has_state_memory = contents is not None
            except Exception:
                has_state_memory = False
                contents = None
        if not has_state_memory or not isinstance(contents, dict):
            contents = {}
        memory_block = self._format_agent_memory(contents)
        new_system: SystemMessage = append_to_system_message(request.system_message, memory_block)
        return await handler(request.override(system_message=new_system))


__all__ = [
    "RefreshableMemoryMiddleware",
]
