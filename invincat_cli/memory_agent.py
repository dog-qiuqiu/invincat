"""Dedicated memory agent middleware.

Runs an independent model call with a focused system prompt after every
conversation turn to extract and persist important information to memory
files.  Uses its own system prompt, outputs structured JSON, and writes
directly to disk — independent of the main agent's judgment.

The extraction runs in ``aafter_agent`` so it does not block the user
from receiving the main response.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a memory curator for an AI assistant.  After each conversation
turn you decide what information deserves to be persisted across sessions.

Output ONLY valid JSON — no prose, no markdown code fences:
{
  "updates": [
    {"file": "<absolute path>", "content": "<complete new file content>"}
  ]
}

Return {"updates": []} when nothing needs updating.

Rules:
- Provide the complete new file content (not a patch).
- Preserve existing entries unless they are superseded or contradicted.
- Keep entries concise; memory should be scannable in seconds.

Capture:
- User preferences (coding style, language, formatting, naming conventions)
- Explicit rules ("always X", "never Y", "prefer Z")
- Project-level decisions and their rationale
- Recurring workflow patterns specific to this user/project

Skip:
- Transient one-off task details that will not recur
- Information already present verbatim in current memory
- Generic knowledge not specific to this user or project
"""

_USER_TEMPLATE = """\
Recent conversation:
{conversation}

Current memory files:
{memory}

What (if anything) should be persisted to memory?
"""

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class MemoryAgentState(AgentState):
    """Private state carried by MemoryAgentMiddleware."""

    _memory_agent_model: Annotated[NotRequired[Any], PrivateStateAttr]


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class MemoryAgentMiddleware(AgentMiddleware):
    """Dedicated memory agent that runs after every conversation turn.

    Captures ``request.model`` from the middleware chain and uses it in
    ``aafter_agent`` to make an independent extraction call.  Results are
    written directly to the configured memory files; the standard
    ``_auto_memory_updated_paths`` state key is set so the app's existing
    toast notification and ``RefreshableMemoryMiddleware`` refresh still
    fire correctly.
    """

    state_schema = MemoryAgentState

    def __init__(
        self,
        *,
        memory_paths: list[str],
        context_messages: int = 10,
    ) -> None:
        """
        Args:
            memory_paths: Absolute paths to AGENTS.md files to maintain.
            context_messages: Number of recent messages fed to the memory
                agent (default 10, roughly 5 turns).
        """
        self._memory_paths = memory_paths
        self._context_messages = context_messages
        self._pre_agent_hashes: dict[str, str] = {}
        self._captured_model: Any = None

    # ------------------------------------------------------------------
    # File helpers
    # ------------------------------------------------------------------

    def _snapshot_hashes(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for path in self._memory_paths:
            try:
                data = Path(path).read_bytes()
                hashes[path] = hashlib.md5(data, usedforsecurity=False).hexdigest()
            except OSError:
                hashes[path] = ""
        return hashes

    def _changed_paths(self, before: dict[str, str]) -> list[str]:
        changed: list[str] = []
        for path, old_hash in before.items():
            try:
                data = Path(path).read_bytes()
                new_hash = hashlib.md5(data, usedforsecurity=False).hexdigest()
            except OSError:
                new_hash = ""
            if new_hash != old_hash:
                changed.append(path)
        return changed

    def _read_memory_files(self) -> str:
        parts: list[str] = []
        for path in self._memory_paths:
            try:
                content = Path(path).read_text(encoding="utf-8").strip()
                label = f"[{path}]\n{content}" if content else f"[{path}]\n(empty)"
            except OSError:
                label = f"[{path}]\n(file does not exist yet)"
            parts.append(label)
        return "\n\n".join(parts) if parts else "(no memory files configured)"

    # ------------------------------------------------------------------
    # Conversation formatting
    # ------------------------------------------------------------------

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
                lines.append(f"{role}: {str(content)[:1500]}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Core extraction
    # ------------------------------------------------------------------

    async def _extract_and_write(
        self, model: Any, messages: list[Any]
    ) -> list[str]:
        """Run the memory agent and write any updates.  Returns changed paths."""
        try:
            conversation = await asyncio.to_thread(self._format_messages, messages)
            memory = await asyncio.to_thread(self._read_memory_files)
            pre_hashes = await asyncio.to_thread(self._snapshot_hashes)

            lc_messages = [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(
                    content=_USER_TEMPLATE.format(
                        conversation=conversation, memory=memory
                    )
                ),
            ]
            response = await model.ainvoke(lc_messages)

            raw: str = response.content
            if isinstance(raw, list):
                raw = " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in raw
                )

            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start == -1 or end == 0:
                logger.debug("Memory agent: no JSON found in response")
                return []

            data = json.loads(raw[start:end])
            updates: list[dict[str, str]] = data.get("updates", [])
            if not updates:
                return []

            for update in updates:
                file_path = update.get("file")
                content = update.get("content")
                if not file_path or content is None:
                    continue
                p = Path(file_path)
                await asyncio.to_thread(p.parent.mkdir, parents=True, exist_ok=True)
                await asyncio.to_thread(p.write_text, content, "utf-8")
                logger.debug("Memory agent wrote: %s", file_path)

            return await asyncio.to_thread(self._changed_paths, pre_hashes)

        except Exception:
            logger.debug("Memory agent extraction failed", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Middleware hooks
    # ------------------------------------------------------------------

    def before_agent(
        self, state: MemoryAgentState, runtime: Any
    ) -> dict[str, Any] | None:
        self._pre_agent_hashes = self._snapshot_hashes()
        return None

    async def abefore_agent(
        self, state: MemoryAgentState, runtime: Any
    ) -> dict[str, Any] | None:
        self._pre_agent_hashes = await asyncio.to_thread(self._snapshot_hashes)
        return None

    def wrap_model_call(
        self, request: ModelRequest, handler: Any
    ) -> ModelResponse:
        if self._captured_model is None:
            self._captured_model = request.model
        return handler(request)

    async def awrap_model_call(
        self, request: ModelRequest, handler: Any
    ) -> ModelResponse:
        if self._captured_model is None:
            self._captured_model = request.model
        return await handler(request)

    async def aafter_agent(
        self, state: MemoryAgentState, runtime: Any
    ) -> dict[str, Any] | None:
        model = self._captured_model
        if model is None:
            return None

        messages = state.get("messages", [])
        if not messages:
            return None

        recent = messages[-self._context_messages :]
        changed = await self._extract_and_write(model, recent)

        if changed:
            return {
                "memory_contents": None,        # triggers RefreshableMemoryMiddleware
                "_auto_memory_updated_paths": changed,  # triggers toast in app.py
            }
        return None

    def after_agent(
        self, state: MemoryAgentState, runtime: Any
    ) -> dict[str, Any] | None:
        # Sync fallback: fire-and-forget if an event loop is already running.
        # aafter_agent is preferred; this path only runs in sync execution contexts.
        model = self._captured_model
        if model is None:
            return None

        messages = state.get("messages", [])
        if not messages:
            return None

        recent = messages[-self._context_messages :]
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._extract_and_write(model, recent))
        except RuntimeError:
            logger.debug("Memory agent: no running event loop for sync fallback")

        return None
