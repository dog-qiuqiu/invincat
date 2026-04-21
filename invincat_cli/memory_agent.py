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

_MAX_PER_FILE_CHARS = 4000   # truncation limit per memory file sent to the agent
_MAX_OUTPUT_TOKENS = 2000    # upper bound for memory agent JSON response

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a memory curator for an AI assistant. After each conversation turn you
decide what information deserves to be persisted across sessions.

Output ONLY valid JSON — no prose, no markdown code fences:
{
  "updates": [
    {"file": "<absolute path>", "content": "<complete new file content>"}
  ]
}

Return {"updates": []} when nothing needs updating.

## File roles

You will be given one or two memory files. Identify each by its path:

- **User-level** (`~/.invincat/.../AGENTS.md`): personal preferences that apply
  across ALL projects — coding style, language, tone, tool preferences, general
  rules ("always", "never", "prefer"), recurring workflow habits.

- **Project-level** (`<project_root>/.invincat/AGENTS.md` or
  `<project_root>/AGENTS.md`): facts specific to THIS project — tech stack,
  architecture decisions and their rationale, naming conventions, file layout,
  project-specific constraints or rules.

When only one file is present, write everything there.
When both are present, route each piece of information to the correct file.
If a project file does not exist yet ("file does not exist yet"), create it only
when there is genuine project-specific content to store.

## Writing rules

- Provide the **complete new file content** (not a patch).
- Preserve existing entries unless superseded or contradicted.
- Keep entries concise and scannable — bullet points, not paragraphs.
- Group related entries under short headings when the file grows beyond a few items.

## Capture

- Explicit user preferences and rules ("always X", "never Y", "prefer Z")
- Coding style: language choice, formatting, naming, comment style
- Project decisions: chosen frameworks, patterns, constraints, rationale
- Recurring workflow patterns unique to this user or project

## Skip

- Transient one-off task details that will not recur
- Information already present verbatim in current memory
- Generic knowledge not specific to this user or project
- Intermediate reasoning or tool outputs with no lasting relevance
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


def _is_task_complete(messages: list[Any]) -> bool:
    """Return True when all tool calls have completed and AI has given final response.

    A task is considered complete when:
    - The last message is an AI message with no pending tool calls
    - This means the agent has finished all tool invocations and provided a response

    Returns False when:
    - The last message is a ToolMessage (tool just finished, AI hasn't responded yet)
    - The last message is an AI message with non-empty tool_calls (pending tools)
    """
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


# ---------------------------------------------------------------------------
# Private state schema
# ---------------------------------------------------------------------------


class MemoryAgentState(AgentState):
    """Private state fields for MemoryAgentMiddleware.

    Declared with ``PrivateStateAttr`` so LangGraph resets them automatically
    at the start of every agent turn, preventing stale values from leaking
    between turns.
    """

    _auto_memory_updated_paths: Annotated[NotRequired[list[str]], PrivateStateAttr]


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
                content = Path(path).expanduser().read_text(encoding="utf-8").strip()
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
        written: list[str] = []
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
            ], config={"metadata": {"lc_source": "memory_agent"}})

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
            updates = data.get("updates", [])
            if not updates or not isinstance(updates, list):
                return []

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
            return []  # JSONDecodeError occurs before any writes, written is always []
        except Exception:
            logger.warning("Memory agent extraction failed unexpectedly", exc_info=True)
            return written  # return any paths successfully written before the failure

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

        # Only extract when the task is truly complete — not mid-turn while waiting
        # for HITL approval.  LangGraph stores pending interrupts in state under
        # "__interrupt__"; if that key is non-empty the user still needs to respond.
        if state.get("__interrupt__"):
            logger.debug("Memory agent: skipping extraction — pending interrupts")
            return None

        messages = state.get("messages", [])
        if not messages:
            return None

        # Only extract when all tool calls have completed and AI has given final response.
        # This ensures memory extraction happens at task boundaries, not mid-turn.
        if not _is_task_complete(messages):
            logger.debug("Memory agent: skipping extraction — task not complete (pending tool calls)")
            return None

        # Trivial check uses the FULL message list so that a user message
        # followed by many tool calls is not mistakenly skipped because the
        # human message fell outside the context window.
        if _is_trivial_turn(messages):
            logger.debug("Memory agent: skipping trivial turn")
            return None

        recent = messages[-self._context_messages :]

        # If the last human message was pushed out of the window by many tool
        # calls in the same turn, prepend it so the memory agent always sees
        # what the user actually said.
        human_indices = [
            i for i, m in enumerate(messages) if getattr(m, "type", "") == "human"
        ]
        if human_indices:
            last_human_idx = human_indices[-1]
            window_start = len(messages) - self._context_messages
            if last_human_idx < window_start:
                recent = [messages[last_human_idx]] + list(recent)

        written = await self._safe_extract_and_write(model, recent)
        if written:
            return {
                "memory_contents": None,               # triggers RefreshableMemoryMiddleware reload
                "_auto_memory_updated_paths": written,  # triggers toast in app.py
            }
        return None

    async def _safe_extract_and_write(self, model: Any, messages: list[Any]) -> list[str]:
        """Run extraction, absorbing CancelledError so it doesn't escape into agent.astream().

        If the outer task is being cancelled (ESC), we re-request cancellation on the
        current task so it fires at the next await *outside* the middleware, preserving
        correct ESC behaviour while preventing a silent no-error interruption here.
        """
        try:
            return await self._extract_and_write(model, messages)
        except asyncio.CancelledError:
            logger.debug("Memory agent: extraction cancelled — re-scheduling task cancellation")
            current = asyncio.current_task()
            if current is not None:
                current.cancel()
            return []
