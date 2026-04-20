"""Dedicated memory agent middleware.

Runs an independent model call with a focused system prompt after every
non-trivial conversation turn to extract and persist important information
to memory files.  Uses its own system prompt, outputs structured JSON,
and writes directly to disk — independent of the main agent's judgment.

The extraction runs in ``aafter_agent`` so it does not block the user
from receiving the main response.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

_MAX_PER_FILE_CHARS = 4000   # truncation limit per memory file sent to the agent
_MAX_OUTPUT_TOKENS = 2000    # upper bound for memory agent JSON response

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
# Trivial-turn detection
# ---------------------------------------------------------------------------

# Short acknowledgment messages that carry no memory-worthy information.
_TRIVIAL_RE = re.compile(
    r"^\s*("
    r"ok|okay|thanks|thank you|got it|sure|yes|no|confirmed|done|"
    r"continue|go ahead|proceed|sounds good|great|perfect|nice|"
    r"好的|谢谢|明白|知道了|好|嗯|是的|对|继续|好的好的|没问题|可以"
    r")\s*[.!?。！？]?\s*$",
    re.IGNORECASE,
)


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
    return len(text) < 10 or bool(_TRIVIAL_RE.match(text))


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class MemoryAgentMiddleware(AgentMiddleware):
    """Dedicated memory agent that runs after every non-trivial conversation turn.

    Captures ``request.model`` on every ``wrap_model_call`` so that runtime
    model switches (``/model``) are picked up immediately.  In ``aafter_agent``
    an independent extraction call is made; results are written directly to the
    configured memory files.

    Security: only paths present in ``memory_paths`` (resolved to absolute) are
    ever written; any other path returned by the model is rejected with a warning.
    """

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
        # Pre-resolve allowed paths once so the whitelist check is O(1).
        self._allowed_paths: frozenset[str] = frozenset(
            str(Path(p).expanduser().resolve()) for p in memory_paths
        )
        self._captured_model: Any = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_memory_files(self) -> str:
        parts: list[str] = []
        for path in self._memory_paths:
            try:
                content = Path(path).read_text(encoding="utf-8").strip()
                if len(content) > _MAX_PER_FILE_CHARS:
                    content = content[:_MAX_PER_FILE_CHARS] + f"\n[...{len(content) - _MAX_PER_FILE_CHARS} chars truncated]"
                parts.append(f"[{path}]\n{content}" if content else f"[{path}]\n(empty)")
            except OSError:
                parts.append(f"[{path}]\n(file does not exist yet)")
        return "\n\n".join(parts) if parts else "(no memory files configured)"

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
        """Run the memory agent and write updates.  Returns list of written paths."""
        try:
            conversation = self._format_messages(messages)
            memory = await asyncio.to_thread(self._read_memory_files)

            response = await model.bind(max_tokens=_MAX_OUTPUT_TOKENS).ainvoke([
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(
                    content=_USER_TEMPLATE.format(
                        conversation=conversation, memory=memory
                    )
                ),
            ])

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
            updates: list[dict[str, str]] = data.get("updates", [])
            if not updates:
                return []

            written: list[str] = []
            for update in updates:
                file_path = update.get("file")
                content = update.get("content")
                if not file_path or content is None:
                    continue

                # Security: reject any path not in the pre-approved whitelist.
                resolved = str(Path(file_path).expanduser().resolve())
                if resolved not in self._allowed_paths:
                    logger.warning(
                        "Memory agent: rejected write to unauthorized path %s",
                        file_path,
                    )
                    continue

                p = Path(file_path).expanduser()
                await asyncio.to_thread(p.parent.mkdir, parents=True, exist_ok=True)
                await asyncio.to_thread(p.write_text, content, "utf-8")
                written.append(str(p))  # store the expanded path for consistent display
                logger.debug("Memory agent wrote: %s", p)

            return written

        except json.JSONDecodeError:
            logger.debug("Memory agent: model returned malformed JSON", exc_info=True)
            return []
        except Exception:
            logger.warning("Memory agent extraction failed unexpectedly", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Middleware hooks
    # ------------------------------------------------------------------

    def wrap_model_call(
        self, request: ModelRequest, handler: Any
    ) -> ModelResponse:
        # Always update so /model switches are reflected immediately.
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
        logger.debug("Memory agent: aafter_agent called")
        model = self._captured_model
        if model is None:
            return None

        messages = state.get("messages", [])
        if not messages:
            return None

        # Trivial check uses the FULL message list so that a user message
        # followed by many tool calls is not mistakenly skipped because the
        # human message fell outside the context window.
        if _is_trivial_turn(messages):
            logger.debug("Memory agent: skipping trivial turn")
            return None

        recent = messages[-self._context_messages :]
        written = await self._extract_and_write(model, recent)
        if written:
            return {
                "memory_contents": None,               # triggers RefreshableMemoryMiddleware reload
                "_auto_memory_updated_paths": written,  # triggers toast in app.py
            }
        return None
