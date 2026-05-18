"""Runtime tool boundaries for built-in subagents."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from langgraph.prebuilt.tool_node import ToolCallRequest


READ_ONLY_SUBAGENT_ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "file_info",
        "ls",
        "glob",
        "grep",
        "web_search",
        "fetch_url",
        "ask_user",
    }
)
"""Tool names allowed for read-only built-in subagents."""


class ReadOnlySubagentToolMiddleware(AgentMiddleware):
    """Runtime guard for read-only subagents.

    Prompt instructions are useful but insufficient for hard safety boundaries.
    This middleware also hides disallowed tool schemas and rejects accidental
    calls before they execute.
    """

    def __init__(self, allowed_tools: set[str] | frozenset[str] | None = None) -> None:
        super().__init__()
        self._allowed_tools = set(allowed_tools or READ_ONLY_SUBAGENT_ALLOWED_TOOLS)

    @staticmethod
    def _tool_name(tool: Any) -> str:  # noqa: ANN401
        if hasattr(tool, "name"):
            return str(getattr(tool, "name", "")).strip()
        if isinstance(tool, dict):
            return str(tool.get("name", "")).strip()
        return ""

    def _filter_tools(self, tools: list[Any]) -> list[Any]:  # noqa: ANN401
        return [tool for tool in tools if self._tool_name(tool) in self._allowed_tools]

    def _reject_if_disallowed(self, request: ToolCallRequest) -> ToolMessage | None:
        tool_name = str(request.tool_call.get("name", "")).strip()
        if tool_name in self._allowed_tools:
            return None
        allowed = ", ".join(sorted(self._allowed_tools))
        return ToolMessage(
            content=(
                f"Tool '{tool_name}' is not allowed for this read-only subagent. "
                f"Allowed tools: {allowed}."
            ),
            name=tool_name,
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage],
    ) -> ToolMessage:
        if (rejection := self._reject_if_disallowed(request)) is not None:
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> ToolMessage:
        if (rejection := self._reject_if_disallowed(request)) is not None:
            return rejection
        return await handler(request)

    def wrap_model_call(
        self,
        request: Any,  # noqa: ANN401
        handler: Callable[[Any], Any],  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        filtered_tools = self._filter_tools(list(getattr(request, "tools", [])))
        return handler(request.override(tools=filtered_tools))

    async def awrap_model_call(
        self,
        request: Any,  # noqa: ANN401
        handler: Callable[[Any], Any],  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        filtered_tools = self._filter_tools(list(getattr(request, "tools", [])))
        return await handler(request.override(tools=filtered_tools))
