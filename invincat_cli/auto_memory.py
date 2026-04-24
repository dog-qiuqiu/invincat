"""Memory middleware for loading and refreshing structured JSON memory stores."""

from __future__ import annotations

import json
import logging
import asyncio
from pathlib import Path
from typing import Any

from deepagents.middleware.memory import append_to_system_message
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
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


def _select_items_for_injection(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in items:
        if not _is_valid_store_item(raw):
            continue
        if str(raw.get("status", "")).strip().lower() != "active":
            continue
        tier = _normalize_item_tier(raw.get("tier"))
        if tier == "cold":
            continue
        grouped.setdefault(section, []).append((item_id, content))

    if not grouped:
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

    for item in selected:
        line = (
            f"- [{item['tier']}:{item['score']}] "
            f"{item['section']}: {item['content']}"
        )
        if not _try_append(line):
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
            key = f"{scope}::{store_path}"
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
        if state.get("memory_contents") is None:
            logger.debug("Refreshing memory contents")
            return {"memory_contents": self._load_memory_contents()}
        return None

    async def abefore_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        if state.get("memory_contents") is None:
            logger.debug("Refreshing memory contents (async)")
            contents = await asyncio.to_thread(self._load_memory_contents)
            return {"memory_contents": contents}
        return None

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        contents = request.state.get("memory_contents", {})
        if not isinstance(contents, dict):
            contents = {}
        memory_block = self._format_agent_memory(contents)
        new_system: SystemMessage = append_to_system_message(request.system_message, memory_block)
        return handler(request.override(system_message=new_system))

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        contents = request.state.get("memory_contents", {})
        if not isinstance(contents, dict):
            contents = {}
        memory_block = self._format_agent_memory(contents)
        new_system: SystemMessage = append_to_system_message(request.system_message, memory_block)
        return await handler(request.override(system_message=new_system))


__all__ = [
    "RefreshableMemoryMiddleware",
]
