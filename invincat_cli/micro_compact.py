"""Lightweight tool-output trimming that runs before every model call.

Inspired by the "micro-compact" layer described in Claude Code's context
compression design: a pure rule-based pass that clears old tool results
**without any LLM call**, running in <1 ms with minimal information loss.

How it works
------------
Before each model call, the middleware groups the conversation messages into
"tool-call groups" (one AIMessage with tool_calls + its following
ToolMessages).  The last ``KEEP_RECENT_GROUPS`` groups are left intact.
Older groups have whitelisted ToolMessage content replaced with a short
placeholder, reducing the tokens sent to the model.

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

KEEP_RECENT_GROUPS: int = 3
"""Number of most-recent tool-call groups to preserve intact."""

_MIN_GROUPS_TO_TRIM: int = KEEP_RECENT_GROUPS + 1
"""Do nothing when there are fewer groups than this threshold."""

_PLACEHOLDER = "[cleared]"
"""Replacement content for old compressible tool results."""


# ---------------------------------------------------------------------------
# Core trimming function (pure, no side-effects)
# ---------------------------------------------------------------------------


def micro_compact_messages(messages: list[Any]) -> tuple[list[Any], int]:
    """Return a trimmed copy of *messages* and the number of results cleared.

    Groups messages into tool-call batches (AIMessage with tool_calls +
    subsequent ToolMessages).  The last ``KEEP_RECENT_GROUPS`` groups are kept
    intact.  Older groups have compressible ToolMessage content replaced with
    ``_PLACEHOLDER``.

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

    if len(groups) < _MIN_GROUPS_TO_TRIM:
        return messages, 0

    # ------------------------------------------------------------------
    # Step 2: clear old compressible results
    # ------------------------------------------------------------------
    cutoff = len(groups) - KEEP_RECENT_GROUPS
    result: list[Any] = list(messages)  # shallow copy — only replace changed items
    cleared = 0

    for _ai_idx, tool_indices in groups[:cutoff]:
        for idx in tool_indices:
            msg = result[idx]
            if not isinstance(msg, ToolMessage):
                continue
            tool_name: str = getattr(msg, "name", "") or ""
            if tool_name not in COMPRESSIBLE_TOOLS:
                continue
            content = msg.content
            if not isinstance(content, str) or not content or content == _PLACEHOLDER:
                continue  # already cleared or empty

            result[idx] = ToolMessage(
                content=_PLACEHOLDER,
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
    without touching the persisted graph state.  When nothing needs trimming
    (fewer than ``KEEP_RECENT_GROUPS + 1`` groups), the call passes through
    with zero overhead.
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
