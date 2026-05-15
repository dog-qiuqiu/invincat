"""ScheduleMiddleware — exposes scheduled-task management tools to the agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from invincat_cli.scheduler.tool_constants import (
    MANAGEMENT_TOOLS,
    SCHEDULE_CANCEL_TYPE,
    SCHEDULE_CONTEXT_FLAG,
    SCHEDULE_CREATE_TYPE,
    SCHEDULE_LIST_TYPE,
    SCHEDULE_RUN_NOW_TYPE,
    SCHEDULE_UPDATE_TYPE,
)
from invincat_cli.scheduler.tool_factories import ScheduleToolFactoryMixin
from invincat_cli.scheduler.tool_validation import (
    is_once_schedule_marker,
    parse_once_at,
    parse_schedule_tool_result,
    validate_schedule_create_options,
    validate_timezone_name,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command

    from invincat_cli.scheduler.store import SchedulerStore

_MANAGEMENT_TOOLS = MANAGEMENT_TOOLS


def _is_scheduled_run(runtime: Any) -> bool:  # noqa: ANN401
    ctx = getattr(runtime, "context", None)
    return isinstance(ctx, dict) and bool(ctx.get(SCHEDULE_CONTEXT_FLAG))


def _tool_name(t: Any) -> str:  # noqa: ANN401
    if hasattr(t, "name"):
        return str(t.name)
    if isinstance(t, dict):
        return str(t.get("name", ""))
    return ""


def _is_once_schedule_marker(schedule: str) -> bool:
    """Compatibility wrapper for older internal imports."""
    return is_once_schedule_marker(schedule)


class ScheduleMiddleware(ScheduleToolFactoryMixin, AgentMiddleware):
    """Expose scheduled-task tools to the agent.

    During automated scheduled runs (``SCHEDULE_CONTEXT_FLAG`` is set in the
    runtime context) all management tools are hidden to prevent recursive task
    creation.
    """

    def __init__(self, *, store: SchedulerStore) -> None:  # noqa: F821
        super().__init__()
        self._store = store
        self.tools = [
            self._make_create_tool(),
            self._make_list_tool(),
            self._make_update_tool(),
            self._make_cancel_tool(),
            self._make_delete_tool(),
            self._make_run_now_tool(),
        ]

    def _filter_tools(self, tools: list, runtime: Any) -> list:  # noqa: ANN401
        if _is_scheduled_run(runtime):
            return [tool for tool in tools if _tool_name(tool) not in MANAGEMENT_TOOLS]
        return tools

    def _reject_management_tool_during_scheduled_run(
        self,
        request: ToolCallRequest,
    ) -> ToolMessage | None:
        name = str(request.tool_call.get("name", ""))
        if name not in MANAGEMENT_TOOLS:
            return None
        if not _is_scheduled_run(getattr(request, "runtime", None)):
            return None
        return ToolMessage(
            content="Scheduled-task management tools are not available during scheduled runs.",
            name=name,
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        tools = self._filter_tools(list(getattr(request, "tools", [])), request.runtime)
        return handler(request.override(tools=tools))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        tools = self._filter_tools(list(getattr(request, "tools", [])), request.runtime)
        return await handler(request.override(tools=tools))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if (
            rejection := self._reject_management_tool_during_scheduled_run(request)
        ) is not None:
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        if (
            rejection := self._reject_management_tool_during_scheduled_run(request)
        ) is not None:
            return rejection
        return await handler(request)


__all__ = [
    "SCHEDULE_CANCEL_TYPE",
    "SCHEDULE_CONTEXT_FLAG",
    "SCHEDULE_CREATE_TYPE",
    "SCHEDULE_LIST_TYPE",
    "SCHEDULE_RUN_NOW_TYPE",
    "SCHEDULE_UPDATE_TYPE",
    "ScheduleMiddleware",
    "_is_once_schedule_marker",
    "_is_scheduled_run",
    "_tool_name",
    "parse_once_at",
    "parse_schedule_tool_result",
    "validate_schedule_create_options",
    "validate_timezone_name",
]
