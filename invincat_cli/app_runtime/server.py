"""Server startup runtime helpers for the Textual app."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class McpPreloadResult:
    """MCP metadata preload result extracted from startup gather output."""

    info: Any | None
    error: BaseException | None


@dataclass(frozen=True, slots=True)
class ResumeNotice:
    """I18n key and params for resume-thread notices."""

    key: str
    params: dict[str, object]


def normalize_server_start_error(result: object) -> Exception | None:
    """Return a postable Exception when server startup returned an error."""
    if not isinstance(result, BaseException):
        return None
    if isinstance(result, Exception):
        return result
    return RuntimeError(str(result))


def resolve_mcp_preload_result(results: Sequence[object]) -> McpPreloadResult:
    """Extract MCP metadata or preload error from startup gather results."""
    if len(results) <= 1:
        return McpPreloadResult(info=None, error=None)
    result = results[1]
    if isinstance(result, BaseException):
        return McpPreloadResult(info=None, error=result)
    return McpPreloadResult(info=result, error=None)


def count_mcp_tools(mcp_server_info: Sequence[Any] | None) -> int:
    """Count loaded MCP tools from server metadata."""
    return sum(len(server.tools) for server in (mcp_server_info or []))


def should_drain_deferred_on_server_ready(
    *,
    deferred_action_count: int,
    agent_running: bool,
) -> bool:
    """Return whether deferred actions should drain after server ready."""
    return deferred_action_count > 0 and not agent_running


def should_drain_queue_on_server_ready(
    *,
    pending_message_count: int,
    initial_prompt: str | None,
) -> bool:
    """Return whether queued user messages should drain after server ready."""
    has_initial_prompt = bool(initial_prompt and initial_prompt.strip())
    return pending_message_count > 0 and not has_initial_prompt


def should_update_default_agent_from_thread(
    *,
    assistant_id: str,
    default_agent: str = "agent",
) -> bool:
    """Return whether a resumed thread should override the default agent name."""
    return assistant_id == default_agent


def resolve_most_recent_agent_filter(
    *,
    assistant_id: str,
    default_agent: str = "agent",
) -> str | None:
    """Return the agent filter for most-recent thread lookup."""
    return assistant_id if assistant_id != default_agent else None


def format_similar_threads(similar: Sequence[object]) -> str:
    """Format similar thread candidates for a not-found message."""
    return ", ".join(str(thread_id) for thread_id in similar)


def resolve_no_recent_threads_notice(agent_filter: str | None) -> ResumeNotice:
    """Return the notice shown when no recent thread exists."""
    if agent_filter:
        return ResumeNotice(key="app.no_threads_agent", params={"agent": agent_filter})
    return ResumeNotice(key="app.no_threads", params={})


def resolve_thread_not_found_notice(
    *,
    thread_id: str,
    similar: Sequence[object],
) -> ResumeNotice:
    """Return the notice shown when a requested thread ID cannot be found."""
    if similar:
        return ResumeNotice(
            key="app.thread_not_found",
            params={
                "thread_id": thread_id,
                "similar": format_similar_threads(similar),
            },
        )
    return ResumeNotice(
        key="app.thread_not_found_simple",
        params={"thread_id": thread_id},
    )
