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
import os
import re
import tempfile
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
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

_MAX_PER_FILE_CHARS = 4000   # truncation limit per memory file sent to the agent
_MAX_OUTPUT_TOKENS = 2000    # upper bound for memory agent JSON response
_MAX_UPDATES = 4
_MAX_UPDATE_CONTENT_CHARS = 32000
_MAX_SECTION_LINES = 400
_ROOT_SECTION = "__root__"
_MEMORY_SIGNAL_RE = re.compile(
    r"\b("
    r"always|never|prefer|preference|style|convention|rule|guideline|"
    r"remember|remember this|best practice|pattern|decision|constraint"
    r")\b|"
    r"(记住|偏好|规范|约定|规则|风格|最佳实践|约束|决策)",
    re.IGNORECASE,
)

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

## Mission

Write only durable, reusable guidance for future turns. Memory should read like
an operating manual, not a chat transcript.

## File roles

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

## Output contract (strict)

- Return EXACTLY one JSON object with key `"updates"`.
- Each update must include:
  - `"file"`: absolute path, must match one of the provided files
  - `"content"`: complete replacement content for that file
- If nothing should change, return `{"updates": []}`.
- Do not emit comments, explanations, or extra keys.

## Editing policy

- Preserve existing useful content; do not rewrite for style-only reasons.
- Merge new facts incrementally; avoid broad reformatting.
- Deduplicate semantically similar bullets.
- If a new rule conflicts with old memory, keep only the latest explicit rule.
- Keep content compact and scannable:
  - short headings
  - bullet points
  - no long narrative paragraphs

## What to capture

- Explicit user preferences and rules ("always X", "never Y", "prefer Z")
- Coding style: language choice, formatting, naming, comment style
- Project decisions: chosen frameworks, patterns, constraints, rationale
- Recurring workflow patterns unique to this user or project

## What to skip hard

- Transient one-off task details that will not recur
- Information already present verbatim in current memory
- Generic knowledge not specific to this user or project
- Intermediate reasoning or tool outputs with no lasting relevance

## Quality gate before writing

Each kept bullet should be:
- Actionable: changes future behavior
- Stable: likely useful beyond this single task
- Specific: concrete enough to apply consistently

If fewer than one high-confidence item exists, output `{"updates": []}`.
"""

_USER_TEMPLATE = """\
Recent conversation:
{conversation}

Current memory files:
{memory}

Decide whether memory should be updated.
Apply the rules above and output JSON only.
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


def _normalize_line_for_dedupe(line: str) -> str:
    stripped = line.strip()
    # Treat markdown list marker variants as equivalent for dedupe.
    if stripped.startswith(("- ", "* ")):
        stripped = stripped[2:].strip()
    return re.sub(r"\s+", " ", stripped).casefold()


def _extract_rule_conflict_key(line: str) -> str | None:
    """Extract a normalized rule topic key for conflict resolution.

    Returns a key when the line looks like an imperative preference/rule, so
    newer contradictory rules can replace older ones.
    """
    text = line.strip()
    if not text:
        return None

    # Strip common markdown bullet prefixes.
    if text.startswith(("- ", "* ")):
        text = text[2:].strip()

    # English rule markers (always/never/prefer/avoid/must/should).
    eng = re.match(
        r"^(always|never|prefer|avoid|must|should|do not|don't)\s+(.+)$",
        text,
        re.IGNORECASE,
    )
    if eng:
        tail = eng.group(2)
        tail = re.sub(r"^(to\s+)", "", tail, flags=re.IGNORECASE)
        tail = re.sub(r"[.。!！?？]+$", "", tail).strip()
        key = re.sub(r"\s+", " ", tail).casefold()
        return f"rule:{key}" if key else None

    # Chinese rule markers.
    zh = re.match(r"^(总是|不要|避免|优先|尽量|必须|应该)\s*(.+)$", text)
    if zh:
        tail = re.sub(r"[。！!？?]+$", "", zh.group(2)).strip()
        key = re.sub(r"\s+", " ", tail).casefold()
        return f"rule:{key}" if key else None

    return None


def _parse_markdown_sections(content: str) -> tuple[list[str], dict[str, list[str]]]:
    ordered: list[str] = []
    sections: dict[str, list[str]] = {}
    current = _ROOT_SECTION
    ordered.append(current)
    sections[current] = []

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        if re.match(r"^#{1,6}\s+", line):
            current = line.strip()
            if current not in sections:
                ordered.append(current)
                sections[current] = []
            continue
        sections[current].append(line)

    return ordered, sections


def _merge_section_lines(existing: list[str], proposed: list[str]) -> list[str]:
    merged: list[str] = []
    seen_exact: set[str] = set()
    conflict_index: dict[str, int] = {}
    for line in [*existing, *proposed]:
        key = _normalize_line_for_dedupe(line)
        if not key:
            # collapse repeated blank lines
            if merged and merged[-1] == "":
                continue
            if len(merged) >= _MAX_SECTION_LINES:
                break
            merged.append("")
            continue
        if key in seen_exact:
            continue

        # If this line is a rule on the same topic as an existing one, keep
        # only the latest line (latest wins) to avoid stale contradictions.
        conflict_key = _extract_rule_conflict_key(line)
        if conflict_key is not None and conflict_key in conflict_index:
            idx = conflict_index[conflict_key]
            old_line = merged[idx]
            old_key = _normalize_line_for_dedupe(old_line)
            if old_key:
                seen_exact.discard(old_key)
            merged[idx] = line
            seen_exact.add(key)
            continue

        if len(merged) >= _MAX_SECTION_LINES:
            break

        merged.append(line)
        seen_exact.add(key)
        if conflict_key is not None:
            conflict_index[conflict_key] = len(merged) - 1
    return merged


def _structured_merge_memory(existing: str, proposed: str) -> str:
    existing = existing.strip()
    proposed = proposed.strip()
    if not existing:
        return proposed
    if not proposed:
        return existing

    existing_order, existing_sections = _parse_markdown_sections(existing)
    proposed_order, proposed_sections = _parse_markdown_sections(proposed)

    final_order = list(existing_order)
    for heading in proposed_order:
        if heading not in final_order:
            final_order.append(heading)

    final_sections: dict[str, list[str]] = {}
    for heading in final_order:
        final_sections[heading] = _merge_section_lines(
            existing_sections.get(heading, []),
            proposed_sections.get(heading, []),
        )

    lines: list[str] = []
    for idx, heading in enumerate(final_order):
        if heading != _ROOT_SECTION:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(heading)
        section_lines = final_sections[heading]
        if section_lines:
            if heading != _ROOT_SECTION and lines[-1] != "":
                lines.append("")
            lines.extend(section_lines)
        # Keep sections visually separated.
        if idx < len(final_order) - 1 and lines and lines[-1] != "":
            lines.append("")

    merged = "\n".join(lines).strip()
    return merged if merged else proposed


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
        min_turn_interval: int | None = None,
        min_seconds_between_runs: float | None = None,
        file_cooldown_seconds: float | None = None,
    ) -> None:
        """
        Args:
            memory_paths: Absolute paths to AGENTS.md files to maintain.
            context_messages: Number of recent messages fed to the memory
                agent (default 10, roughly 5 turns).
            min_turn_interval: Minimum turns between extraction runs.
            min_seconds_between_runs: Minimum wall-clock seconds between runs.
            file_cooldown_seconds: Skip extraction when memory file was
                updated too recently.
        """
        if min_turn_interval is None:
            min_turn_interval = _env_int(
                "INVINCAT_MEMORY_MIN_TURN_INTERVAL",
                default=5,
                minimum=1,
            )
        if min_seconds_between_runs is None:
            min_seconds_between_runs = _env_float(
                "INVINCAT_MEMORY_MIN_SECONDS_BETWEEN_RUNS",
                default=15.0,
                minimum=0.0,
            )
        if file_cooldown_seconds is None:
            file_cooldown_seconds = _env_float(
                "INVINCAT_MEMORY_FILE_COOLDOWN_SECONDS",
                default=8.0,
                minimum=0.0,
            )

        self._memory_paths = memory_paths
        self._context_messages = context_messages
        self._min_turn_interval = max(1, min_turn_interval)
        self._min_seconds_between_runs = max(0.0, min_seconds_between_runs)
        self._file_cooldown_seconds = max(0.0, file_cooldown_seconds)
        # Pre-resolve allowed paths once so the whitelist check is O(1).
        self._allowed_paths: frozenset[str] = frozenset(
            str(Path(p).expanduser().resolve()) for p in memory_paths
        )
        self._captured_model: Any = None
        self._turn_index = 0
        self._last_run_turn = 0
        self._last_run_at = 0.0

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

    def _memory_files_recently_updated(self) -> bool:
        if self._file_cooldown_seconds <= 0:
            return False
        now = time.time()
        for path in self._memory_paths:
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

    def _normalize_and_validate_updates(self, data: Any) -> list[tuple[str, str]]:
        if not isinstance(data, dict):
            return []

        updates_raw = data.get("updates", [])
        if not isinstance(updates_raw, list):
            logger.debug("Memory agent: updates field is not a list")
            return []
        if len(updates_raw) > _MAX_UPDATES:
            logger.warning(
                "Memory agent: too many updates (%d > %d), truncating",
                len(updates_raw),
                _MAX_UPDATES,
            )
            updates_raw = updates_raw[:_MAX_UPDATES]

        normalized: list[tuple[str, str]] = []
        for idx, update in enumerate(updates_raw):
            if not isinstance(update, dict):
                logger.debug("Memory agent: skipping non-object update at idx=%d", idx)
                continue
            file_path = update.get("file")
            content = update.get("content")
            if not isinstance(file_path, str) or not file_path.strip():
                logger.debug("Memory agent: skipping update with invalid file at idx=%d", idx)
                continue
            if not isinstance(content, str):
                logger.debug(
                    "Memory agent: skipping update with non-string content at idx=%d",
                    idx,
                )
                continue
            if len(content) > _MAX_UPDATE_CONTENT_CHARS:
                logger.warning(
                    "Memory agent: skipping oversize content at idx=%d (%d chars)",
                    idx,
                    len(content),
                )
                continue
            normalized.append((file_path, content.strip()))

        return normalized

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
            updates = self._normalize_and_validate_updates(data)
            if not updates:
                return []

            for file_path, content in updates:
                # Security: reject any path not in the pre-approved whitelist.
                resolved = str(Path(file_path).expanduser().resolve())
                if resolved not in self._allowed_paths:
                    logger.warning(
                        "Memory agent: rejected write to unauthorized path %s",
                        file_path,
                    )
                    continue

                p = Path(resolved)
                current = ""
                try:
                    current = await asyncio.to_thread(p.read_text, "utf-8")
                except OSError:
                    current = ""

                merged = _structured_merge_memory(current, content)
                if merged.strip() == current.strip():
                    logger.debug("Memory agent: merged content unchanged for %s", p)
                    continue
                await asyncio.to_thread(_atomic_write_text, p, merged + "\n")
                written.append(str(p))  # store the expanded path for consistent display
                logger.debug("Memory agent wrote: %s", p)

            if written:
                self._last_run_turn = self._turn_index
                self._last_run_at = time.monotonic()
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

        if not self._should_run_for_turn(messages):
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
