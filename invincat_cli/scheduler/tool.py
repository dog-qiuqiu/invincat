"""ScheduleMiddleware — exposes scheduled-task management tools to the agent."""

from __future__ import annotations

import json
import uuid
from datetime import UTC
from typing import TYPE_CHECKING, Annotated, Any, Literal

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command

    from invincat_cli.scheduler.store import SchedulerStore

SCHEDULE_CONTEXT_FLAG = "scheduled_run"
"""Set to True in agent runtime context during an automated scheduled run."""

SCHEDULE_CREATE_TYPE = "schedule_create"
SCHEDULE_LIST_TYPE = "schedule_list"
SCHEDULE_UPDATE_TYPE = "schedule_update"
SCHEDULE_CANCEL_TYPE = "schedule_cancel"
SCHEDULE_RUN_NOW_TYPE = "schedule_run_now"

_MANAGEMENT_TOOLS = frozenset(
    {
        "create_scheduled_task",
        "list_scheduled_tasks",
        "update_scheduled_task",
        "cancel_scheduled_task",
        "delete_scheduled_task",
        "run_scheduled_task_now",
    }
)


def _is_scheduled_run(runtime: Any) -> bool:  # noqa: ANN401
    ctx = getattr(runtime, "context", None)
    return isinstance(ctx, dict) and bool(ctx.get(SCHEDULE_CONTEXT_FLAG))


def _tool_name(t: Any) -> str:  # noqa: ANN401
    if hasattr(t, "name"):
        return str(t.name)
    if isinstance(t, dict):
        return str(t.get("name", ""))
    return ""


def validate_timezone_name(timezone_name: str) -> str:
    """Validate and normalize an IANA timezone name."""
    import zoneinfo

    name = str(timezone_name or "").strip()
    if not name:
        raise ValueError("timezone must not be empty")
    try:
        zoneinfo.ZoneInfo(name)
    except zoneinfo.ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid timezone: {name!r}") from exc
    return name


def parse_once_at(value: str, timezone_name: str) -> str:
    """Parse an absolute one-shot run time and return an ISO UTC timestamp."""
    from datetime import datetime

    timezone_name = validate_timezone_name(timezone_name)
    raw = value.strip()
    if not raw:
        raise ValueError("once_at must not be empty")
    normalized = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            "once_at must be an ISO datetime, e.g. 2026-05-10T20:00:00+08:00"
        ) from exc
    if dt.tzinfo is None:
        from zoneinfo import ZoneInfo

        dt = dt.replace(tzinfo=ZoneInfo(timezone_name))
    return dt.astimezone(UTC).isoformat()


def validate_schedule_create_options(
    *,
    output_mode: Any,
    report_format: Any,
    misfire_policy: Any,
    timeout_seconds: Any,
) -> tuple[
    Literal["message", "report"],
    Literal["markdown", "text"],
    Literal["run_once", "skip"],
    int,
]:
    """Validate shared create-task options from tool or payload boundaries."""
    output_mode_s = str(output_mode or "message")
    if output_mode_s not in {"message", "report"}:
        raise ValueError("output_mode must be 'message' or 'report'")
    output_mode_v: Literal["message", "report"] = (
        "report" if output_mode_s == "report" else "message"
    )

    report_format_s = str(report_format or "markdown")
    if report_format_s not in {"markdown", "text"}:
        raise ValueError("report_format must be 'markdown' or 'text'")
    report_format_v: Literal["markdown", "text"] = (
        "text" if report_format_s == "text" else "markdown"
    )

    misfire_policy_s = str(misfire_policy or "run_once")
    if misfire_policy_s not in {"run_once", "skip"}:
        raise ValueError("misfire_policy must be 'run_once' or 'skip'")
    misfire_policy_v: Literal["run_once", "skip"] = (
        "skip" if misfire_policy_s == "skip" else "run_once"
    )

    try:
        timeout_seconds_i = int(timeout_seconds if timeout_seconds is not None else 600)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be an integer >= 0") from exc
    if timeout_seconds_i < 0:
        raise ValueError("timeout_seconds must be >= 0")

    return output_mode_v, report_format_v, misfire_policy_v, timeout_seconds_i


def _is_once_schedule_marker(schedule: str) -> bool:
    normalized = schedule.strip().lower().replace("_", "-")
    return normalized in {"once", "one-shot", "oneshot", "delay", "delayed"}


def parse_schedule_tool_result(content: Any) -> dict[str, Any] | None:  # noqa: ANN401
    """Try to parse a ToolMessage content as a schedule management payload."""
    if isinstance(content, list):
        parts = [
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        raw = "\n".join(parts).strip()
    else:
        raw = str(content or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("type") not in {
        SCHEDULE_CREATE_TYPE,
        SCHEDULE_LIST_TYPE,
        SCHEDULE_UPDATE_TYPE,
        SCHEDULE_CANCEL_TYPE,
        SCHEDULE_RUN_NOW_TYPE,
    }:
        return None
    return payload


class ScheduleMiddleware(AgentMiddleware):
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

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    def _make_create_tool(self):  # noqa: ANN202
        @tool
        def create_scheduled_task(
            title: str,
            schedule: str,
            prompt: str,
            tool_call_id: Annotated[str, InjectedToolCallId],
            timezone: str = "Asia/Shanghai",
            delivery: str = "tui",
            output_mode: str = "message",
            report_format: str = "markdown",
            misfire_policy: str = "run_once",
            once_at: str | None = None,
            delete_after_run: bool = False,
            timeout_seconds: int = 600,
        ) -> str:
            """Create a scheduled or one-shot delayed task.

            Use this tool for all user requests that ask to run something later,
            at a specific time, or on a recurrence. Do not emulate scheduling
            with shell scripts, sleep loops, cron, or background executor jobs.
            For recurring tasks, do not pass once_at. For one-shot tasks, pass
            schedule="once" and an exact absolute once_at datetime.

            Args:
                title: Short human-readable title (e.g. "Daily project analysis").
                schedule: When to run for recurring tasks. Supported:
                    - "daily HH:MM" (e.g. "daily 08:00")
                    - "weekly <weekday> HH:MM" (e.g. "weekly mon 08:00")
                    - "monthly <day> HH:MM" (e.g. "monthly 1 08:00")
                    - "interval <N>h" or "interval <N>m"
                    - "cron 0 8 * * *"
                    - bare cron: "0 8 * * *"
                    For one-shot delayed tasks, pass any valid value such as "once" and set once_at.
                prompt: The task instructions to execute on each run.
                timezone: IANA timezone name (default "Asia/Shanghai").
                delivery: Delivery channel. Use "tui" normally; WeCom turns are delivered back to WeCom automatically.
                output_mode: "message" for lightweight text result (default), or "report" to require a saved report file.
                report_format: Output format, "markdown" or "text".
                misfire_policy: "run_once" (default) or "skip" if TUI was closed.
                once_at: Optional ISO datetime for a one-shot task, e.g. 2026-05-10T20:00:00+08:00.
                delete_after_run: Delete a one-shot task after it finishes instead of disabling it.
                timeout_seconds: Maximum runtime before the run is marked timed out. Use 0 to disable timeout.
            """
            from invincat_cli.scheduler.parser import parse_schedule

            try:
                timezone = validate_timezone_name(timezone)
            except ValueError as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

            schedule_type = "once" if once_at else "recurring"
            run_at = None
            if once_at:
                if not _is_once_schedule_marker(schedule):
                    return json.dumps(
                        {
                            "error": (
                                "once_at is only valid for one-shot tasks. "
                                "Use schedule='once' with once_at, or omit once_at "
                                "for recurring schedules."
                            )
                        },
                        ensure_ascii=False,
                    )
                try:
                    run_at = parse_once_at(once_at, timezone)
                except ValueError as exc:
                    return json.dumps({"error": str(exc)}, ensure_ascii=False)
                cron = "0 0 * * *"
            else:
                try:
                    cron = parse_schedule(schedule)
                except ValueError as exc:
                    return json.dumps({"error": str(exc)}, ensure_ascii=False)

            try:
                output_mode, report_format, misfire_policy, timeout_seconds = (
                    validate_schedule_create_options(
                        output_mode=output_mode,
                        report_format=report_format,
                        misfire_policy=misfire_policy,
                        timeout_seconds=timeout_seconds,
                    )
                )
            except ValueError as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

            payload = {
                "type": SCHEDULE_CREATE_TYPE,
                "task_id": str(uuid.uuid4()),
                "title": title,
                "schedule_input": schedule,
                "cron": cron,
                "prompt": prompt,
                "timezone": timezone,
                "delivery": delivery,
                "schedule_type": schedule_type,
                "run_at": run_at,
                "delete_after_run": delete_after_run,
                "output_mode": output_mode,
                "report_format": report_format,
                "misfire_policy": misfire_policy,
                "timeout_seconds": timeout_seconds,
                "tool_call_id": tool_call_id,
            }
            return json.dumps(payload, ensure_ascii=False)

        return create_scheduled_task

    def _make_list_tool(self):  # noqa: ANN202
        store = self._store

        @tool
        def list_scheduled_tasks(
            tool_call_id: Annotated[str, InjectedToolCallId],
        ) -> str:
            """List all scheduled tasks with their status and next run time."""
            from invincat_cli.scheduler.display import (
                format_schedule_time_for_display,
            )

            tasks = store.list_tasks()
            result = []
            for t in tasks:
                channels = getattr(t.delivery, "channels", []) or []
                result.append(
                    {
                        "id": t.id,
                        "title": t.title,
                        "enabled": t.enabled,
                        "cron": t.cron,
                        "schedule_type": t.schedule_type,
                        "run_at": t.run_at,
                        "delete_after_run": t.delete_after_run,
                        "timezone": t.timezone,
                        "next_run_at": t.next_run_at,
                        "next_run_display": format_schedule_time_for_display(
                            t.next_run_at,
                            t.timezone,
                            missing="—",
                        ),
                        "last_status": t.last_status,
                        "run_count": t.run_count,
                        "delivery": channels,
                        "output_mode": getattr(t.report, "mode", "message"),
                    }
                )
            payload = {
                "type": SCHEDULE_LIST_TYPE,
                "tasks": result,
                "tool_call_id": tool_call_id,
            }
            return json.dumps(payload, ensure_ascii=False)

        return list_scheduled_tasks

    def _make_update_tool(self):  # noqa: ANN202
        store = self._store

        @tool
        def update_scheduled_task(
            task_id: str,
            tool_call_id: Annotated[str, InjectedToolCallId],
            title: str | None = None,
            schedule: str | None = None,
            prompt: str | None = None,
            enabled: bool | None = None,
            timezone: str | None = None,
        ) -> str:
            """Update an existing scheduled task.

            Args:
                task_id: The ID of the task to update.
                title: New title (optional).
                schedule: New schedule expression (optional).
                prompt: New prompt (optional).
                enabled: Enable or disable the task (optional).
                timezone: New timezone (optional).
            """
            from invincat_cli.scheduler.parser import parse_schedule

            task = store.load_task(task_id)
            if task is None:
                return json.dumps(
                    {"error": f"Task {task_id!r} not found"}, ensure_ascii=False
                )
            if schedule is not None and task.schedule_type == "once":
                return json.dumps(
                    {
                        "error": (
                            "Updating the schedule of a one-shot task is not supported; "
                            "create a new one-shot task instead."
                        )
                    },
                    ensure_ascii=False,
                )

            cron = task.cron
            if timezone is not None:
                try:
                    timezone = validate_timezone_name(timezone)
                except ValueError as exc:
                    return json.dumps({"error": str(exc)}, ensure_ascii=False)

            if schedule is not None:
                try:
                    cron = parse_schedule(schedule)
                except ValueError as exc:
                    return json.dumps({"error": str(exc)}, ensure_ascii=False)

            payload = {
                "type": SCHEDULE_UPDATE_TYPE,
                "task_id": task_id,
                "updates": {
                    k: v
                    for k, v in {
                        "title": title,
                        "cron": cron if schedule else None,
                        "schedule_input": schedule,
                        "prompt": prompt,
                        "enabled": enabled,
                        "timezone": timezone,
                    }.items()
                    if v is not None
                },
                "tool_call_id": tool_call_id,
            }
            return json.dumps(payload, ensure_ascii=False)

        return update_scheduled_task

    def _make_cancel_tool(self):  # noqa: ANN202
        store = self._store

        @tool
        def cancel_scheduled_task(
            task_id: str,
            tool_call_id: Annotated[str, InjectedToolCallId],
        ) -> str:
            """Delete a scheduled task permanently.

            Args:
                task_id: The ID of the task to delete.
            """
            task = store.load_task(task_id)
            if task is None:
                return json.dumps(
                    {"error": f"Task {task_id!r} not found"},
                    ensure_ascii=False,
                )
            payload = {
                "type": SCHEDULE_CANCEL_TYPE,
                "task_id": task_id,
                "tool_call_id": tool_call_id,
            }
            return json.dumps(payload, ensure_ascii=False)

        return cancel_scheduled_task

    def _make_delete_tool(self):  # noqa: ANN202
        store = self._store

        @tool
        def delete_scheduled_task(
            task_id: str,
            tool_call_id: Annotated[str, InjectedToolCallId],
        ) -> str:
            """Delete a scheduled task permanently.

            This is an alias of cancel_scheduled_task for users who say
            "delete" rather than "cancel".

            Args:
                task_id: The ID of the task to delete.
            """
            task = store.load_task(task_id)
            if task is None:
                return json.dumps(
                    {"error": f"Task {task_id!r} not found"},
                    ensure_ascii=False,
                )
            payload = {
                "type": SCHEDULE_CANCEL_TYPE,
                "task_id": task_id,
                "tool_call_id": tool_call_id,
            }
            return json.dumps(payload, ensure_ascii=False)

        return delete_scheduled_task

    def _make_run_now_tool(self):  # noqa: ANN202
        store = self._store

        @tool
        def run_scheduled_task_now(
            task_id: str,
            tool_call_id: Annotated[str, InjectedToolCallId],
        ) -> str:
            """Trigger a scheduled task to run immediately.

            Args:
                task_id: The ID of the task to run.
            """
            task = store.load_task(task_id)
            if task is None:
                return json.dumps(
                    {"error": f"Task {task_id!r} not found"}, ensure_ascii=False
                )
            payload = {
                "type": SCHEDULE_RUN_NOW_TYPE,
                "task_id": task_id,
                "title": task.title,
                "tool_call_id": tool_call_id,
            }
            return json.dumps(payload, ensure_ascii=False)

        return run_scheduled_task_now

    # ------------------------------------------------------------------
    # Middleware hooks — hide tools during scheduled runs
    # ------------------------------------------------------------------

    def _filter_tools(self, tools: list, runtime: Any) -> list:  # noqa: ANN401
        if _is_scheduled_run(runtime):
            return [t for t in tools if _tool_name(t) not in _MANAGEMENT_TOOLS]
        return tools

    def _reject_management_tool_during_scheduled_run(
        self,
        request: ToolCallRequest,
    ) -> ToolMessage | None:
        name = str(request.tool_call.get("name", ""))
        if name not in _MANAGEMENT_TOOLS:
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
