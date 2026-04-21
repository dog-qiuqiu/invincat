"""Lightweight tool-output trimming that runs before every model call.

Inspired by the "micro-compact" layer described in Claude Code's context
compression design: a pure rule-based pass that clears old tool results
**without any LLM call**, running in <1 ms with minimal information loss.

How it works
------------
Before each model call, the middleware groups the conversation messages into
"tool-call groups" (one AIMessage with tool_calls + its following
ToolMessages).  A dynamic number of most-recent groups is left intact.
Older groups have whitelisted ToolMessage content replaced with placeholders,
reducing the tokens sent to the model.

The modification is applied only to the per-call ``ModelRequest`` — it does
**not** alter the persisted graph state, so the full message history remains
intact in the checkpoint and can be summarised or replayed at any time.

Compressible tools
------------------
Only tools whose outputs tend to be large and whose exact content is rarely
needed after the model has processed them once are cleared:

- ``read_file``  — file contents, potentially thousands of lines
- ``edit_file``  — diff output
- ``write_file`` — confirmation + written content
- ``execute``    — shell output
- ``grep``       — search results
- ``glob``       — file-path lists
- ``ls``         — directory listings
- ``web_search`` — search snippets
- ``fetch_url``  — page content

Excluded (never cleared): agent/subagent results, ``ask_user`` responses,
MCP tool outputs, ``compact_conversation`` — their content carries unique
context that cannot be re-derived cheaply.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Callable

from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ModelRequest, ModelResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COMPRESSIBLE_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "edit_file",
        "write_file",
        "execute",
        "grep",
        "glob",
        "ls",
        "web_search",
        "fetch_url",
    }
)
"""Tools whose old results are safe to replace with a placeholder."""

def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


BASE_KEEP_RECENT_GROUPS: int = _env_int(
    "INVINCAT_MICRO_COMPACT_KEEP_RECENT_GROUPS",
    default=3,
    minimum=1,
)
"""Base number of most-recent tool-call groups preserved intact."""

DYNAMIC_GROUP_FACTOR: int = _env_int(
    "INVINCAT_MICRO_COMPACT_DYNAMIC_GROUP_FACTOR",
    default=12,
    minimum=1,
)
"""Add one extra preserved group for every N total groups."""

MAX_KEEP_RECENT_GROUPS: int = _env_int(
    "INVINCAT_MICRO_COMPACT_MAX_KEEP_RECENT_GROUPS",
    default=8,
    minimum=BASE_KEEP_RECENT_GROUPS,
)
"""Upper bound for dynamically preserved recent groups."""

LIGHT_NEAR_CUTOFF_GROUPS: int = _env_int(
    "INVINCAT_MICRO_COMPACT_LIGHT_NEAR_CUTOFF_GROUPS",
    default=2,
    minimum=0,
)
"""How many oldest-trimmable groups nearest the cutoff use light compression."""

MIN_COMPRESS_CHARS: int = _env_int(
    "INVINCAT_MICRO_COMPACT_MIN_COMPRESS_CHARS",
    default=240,
    minimum=0,
)
"""Skip compression for very small tool outputs."""

_CLEARED_LIGHT_PREFIX = "[cleared-light"
_CLEARED_HEAVY_PREFIX = "[cleared-heavy"
_CLEARED_PREFIX = "[cleared"
"""Known prefixes used to identify already-compressed placeholders."""


def _make_heavy_placeholder(content: str, tool_name: str) -> str:
    """Build a compact placeholder with first line and line count."""
    first_line = content.split("\n", 1)[0].strip()[:120]
    total_lines = content.count("\n") + 1
    summary = f"{total_lines} lines" if total_lines > 1 else "1 line"
    if first_line:
        return f"[cleared-heavy — {tool_name}, {summary}: {first_line}…]"
    return f"[cleared-heavy — {tool_name}, {summary}]"


def _make_light_placeholder(content: str, tool_name: str) -> str:
    """Build a richer placeholder that preserves both head and tail signals."""
    lines = content.splitlines()
    total_lines = len(lines) if lines else 1
    first_line = (lines[0].strip() if lines else "")[:100]
    last_non_empty = ""
    for line in reversed(lines):
        if line.strip():
            last_non_empty = line.strip()[:100]
            break
    summary = f"{total_lines} lines" if total_lines > 1 else "1 line"
    if first_line and last_non_empty and first_line != last_non_empty:
        return (
            f"[cleared-light — {tool_name}, {summary}: "
            f"head={first_line}… | tail={last_non_empty}…]"
        )
    if first_line:
        return f"[cleared-light — {tool_name}, {summary}: {first_line}…]"
    return f"[cleared-light — {tool_name}, {summary}]"


def _resolve_keep_recent_groups(total_groups: int, total_messages: int) -> int:
    """Compute dynamic keep window from conversation size."""
    # Scale with tool-call group count first.
    dynamic_extra = total_groups // DYNAMIC_GROUP_FACTOR
    keep = BASE_KEEP_RECENT_GROUPS + dynamic_extra

    # For very long threads, preserve one extra recent group for stability.
    if total_messages >= 120:
        keep += 1

    return min(MAX_KEEP_RECENT_GROUPS, max(BASE_KEEP_RECENT_GROUPS, keep))


# ---------------------------------------------------------------------------
# Core trimming function (pure, no side-effects)
# ---------------------------------------------------------------------------


def micro_compact_messages(messages: list[Any]) -> tuple[list[Any], int]:
    """Return a trimmed copy of *messages* and the number of results cleared.

    Groups messages into tool-call batches (AIMessage with tool_calls +
    subsequent ToolMessages).  A dynamic recent window is kept intact.
    Older groups have compressible ToolMessage content replaced with either
    light or heavy placeholders.

    The input list is never mutated; a new list is returned with only the
    modified ToolMessages replaced (all other messages reused by reference).

    Args:
        messages: Full conversation message list from agent state.

    Returns:
        ``(trimmed_messages, cleared_count)`` where *cleared_count* is 0 when
        no trimming was performed (caller can skip the override).
    """
    from langchain_core.messages import AIMessage, ToolMessage

    # ------------------------------------------------------------------
    # Step 1: identify tool-call groups
    # ------------------------------------------------------------------
    # Each group is (ai_msg_index, [tool_msg_indices]).
    groups: list[tuple[int, list[int]]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            tool_indices: list[int] = []
            j = i + 1
            while j < len(messages) and isinstance(messages[j], ToolMessage):
                tool_indices.append(j)
                j += 1
            if tool_indices:
                groups.append((i, tool_indices))
            i = j
        else:
            i += 1

    keep_recent_groups = _resolve_keep_recent_groups(
        total_groups=len(groups),
        total_messages=len(messages),
    )
    if len(groups) <= keep_recent_groups:
        return messages, 0

    # ------------------------------------------------------------------
    # Step 2: compress old compressible results
    # ------------------------------------------------------------------
    cutoff = len(groups) - keep_recent_groups
    result: list[Any] = list(messages)  # shallow copy — only replace changed items
    cleared = 0

    for group_idx, (_ai_idx, tool_indices) in enumerate(groups[:cutoff]):
        # Larger distance means older group.
        distance_from_cutoff = cutoff - group_idx
        use_light = distance_from_cutoff <= LIGHT_NEAR_CUTOFF_GROUPS
        for idx in tool_indices:
            msg = result[idx]
            if not isinstance(msg, ToolMessage):
                continue
            tool_name: str = getattr(msg, "name", "") or ""
            if tool_name not in COMPRESSIBLE_TOOLS:
                continue
            content = msg.content
            if not isinstance(content, str) or not content:
                continue
            if len(content) < MIN_COMPRESS_CHARS:
                continue

            # Already heavy-compressed.
            if content.startswith(_CLEARED_HEAVY_PREFIX):
                continue
            # Already compressed by previous runs:
            # - keep light as-is when light is still the target;
            # - upgrade light -> heavy when this group ages out.
            if content.startswith(_CLEARED_LIGHT_PREFIX) and use_light:
                continue
            # Backward compatibility with legacy "[cleared — ...]" format.
            if content.startswith(_CLEARED_PREFIX) and not content.startswith(
                (_CLEARED_LIGHT_PREFIX, _CLEARED_HEAVY_PREFIX)
            ):
                continue

            result[idx] = ToolMessage(
                content=(
                    _make_light_placeholder(content, tool_name)
                    if use_light
                    else _make_heavy_placeholder(content, tool_name)
                ),
                tool_call_id=msg.tool_call_id,
                name=tool_name,
                additional_kwargs=getattr(msg, "additional_kwargs", {}),
            )
            cleared += 1

    return result, cleared


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class MicroCompactMiddleware(AgentMiddleware):
    """Apply micro-compact trimming before every model call.

    Trims old compressible tool results in the per-call ``ModelRequest``
    without touching the persisted graph state. When nothing needs trimming,
    the call passes through with zero overhead.
    """

    def _apply(self, request: ModelRequest) -> ModelRequest | None:
        """Return a trimmed request, or ``None`` if no trimming was needed."""
        messages = request.state.get("messages", [])
        trimmed, cleared = micro_compact_messages(messages)
        if not cleared:
            return None
        logger.debug("micro-compact: cleared %d old tool outputs", cleared)
        # Create a copy of the request with trimmed messages.
        # request.state is the live in-turn state dict; we replace only the
        # "messages" key so the LLM sees the trimmed list while the rest of
        # the state (including all private fields) is preserved.
        modified = request.override(system_prompt=request.system_prompt or "")
        modified.state["messages"] = trimmed
        return modified

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        modified = self._apply(request)
        return handler(modified if modified is not None else request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Any],
    ) -> Any:
        modified = self._apply(request)
        return await handler(modified if modified is not None else request)
