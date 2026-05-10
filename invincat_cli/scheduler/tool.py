"""ScheduleMiddleware — exposes scheduled-task management tools to the agent."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.tools import InjectedToolCallId, tool

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langgraph.prebuilt.tool_node import ToolCallRequest

SCHEDULE_CONTEXT_FLAG = "scheduled_run"
"""Set to True in agent runtime context during an automated scheduled run."""

SCHEDULE_CREATE_TYPE = "schedule_create"
SCHEDULE_LIST_TYPE = "schedule_list"
SCHEDULE_UPDATE_TYPE = "schedule_update"
SCHEDULE_CANCEL_TYPE = "schedule_cancel"
SCHEDULE_RUN_NOW_TYPE = "schedule_run_now"

_MANAGEMENT_TOOLS = frozenset({
    "create_scheduled_task",
    "list_scheduled_tasks",
    "update_scheduled_task",
    "cancel_scheduled_task",
    "delete_scheduled_task",
    "run_scheduled_task_now",
})


def _is_scheduled_run(runtime: Any) -> bool:  # noqa: ANN401
    ctx = getattr(runtime, "context", None)
    return isinstance(ctx, dict) and bool(ctx.get(SCHEDULE_CONTEXT_FLAG))


def _tool_name(t: Any) -> str:  # noqa: ANN401
    if hasattr(t, "name"):
        return str(t.name)
    if isinstance(t, dict):
        return str(t.get("name", ""))
    return ""


def parse_once_at(value: str, timezone_name: str) -> str:
    """Parse an absolute one-shot run time and return an ISO UTC timestamp."""
    from datetime import datetime, timezone
    import zoneinfo

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
        dt = dt.replace(tzinfo=zoneinfo.ZoneInfo(timezone_name))
    return dt.astimezone(timezone.utc).isoformat()


def parse_schedule_tool_result(content: Any) -> dict[str, Any] | None:  # noqa: ANN401
    """Try to parse a ToolMessage content as a schedule management payload."""
    if isinstance(content, list):
        parts = [
            str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text"
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

    def __init__(self, *, store: "SchedulerStore") -> None:  # noqa: F821
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
        store = self._store

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
        ) -> str:
            """Create a scheduled or one-shot delayed task.

            Use this tool for all user requests that ask to run something later,
            at a specific time, or on a recurrence. Do not emulate scheduling
            with shell scripts, sleep loops, cron, or background executor jobs.

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
            """
            from invincat_cli.scheduler.parser import parse_schedule

            schedule_type = "once" if once_at else "recurring"
            run_at = None
            if once_at:
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

            if output_mode not in {"message", "report"}:
                return json.dumps(
                    {"error": "output_mode must be 'message' or 'report'"},
                    ensure_ascii=False,
                )

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
            tasks = store.list_tasks()
            result = []
            for t in tasks:
                channels = getattr(t.delivery, "channels", []) or []
                result.append({
                    "id": t.id,
                    "title": t.title,
                    "enabled": t.enabled,
                    "cron": t.cron,
                    "schedule_type": t.schedule_type,
                    "run_at": t.run_at,
                    "delete_after_run": t.delete_after_run,
                    "timezone": t.timezone,
                    "next_run_at": t.next_run_at,
                    "last_status": t.last_status,
                    "run_count": t.run_count,
                    "delivery": channels,
                    "output_mode": getattr(t.report, "mode", "message"),
                })
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
                return json.dumps({"error": f"Task {task_id!r} not found"}, ensure_ascii=False)

            cron = task.cron
            if schedule is not None:
                try:
                    cron = parse_schedule(schedule)
                except ValueError as exc:
                    return json.dumps({"error": str(exc)}, ensure_ascii=False)

            payload = {
                "type": SCHEDULE_UPDATE_TYPE,
                "task_id": task_id,
                "updates": {
                    k: v for k, v in {
                        "title": title,
                        "cron": cron if schedule else None,
                        "schedule_input": schedule,
                        "prompt": prompt,
                        "enabled": enabled,
                        "timezone": timezone,
                    }.items() if v is not None
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
            payload = {
                "type": SCHEDULE_CANCEL_TYPE,
                "task_id": task_id,
                "tool_call_id": tool_call_id,
            }
            return json.dumps(payload, ensure_ascii=False)

        return cancel_scheduled_task

    def _make_delete_tool(self):  # noqa: ANN202
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
                return json.dumps({"error": f"Task {task_id!r} not found"}, ensure_ascii=False)
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

    def wrap_model_call(
        self,
        request: "ModelRequest",
        handler: "Callable[[ModelRequest], ModelResponse]",
    ) -> "ModelResponse":
        tools = self._filter_tools(list(getattr(request, "tools", [])), request.runtime)
        return handler(request.override(tools=tools))

    async def awrap_model_call(
        self,
        request: "ModelRequest",
        handler: "Callable[[ModelRequest], Awaitable[ModelResponse]]",
    ) -> "ModelResponse":
        tools = self._filter_tools(list(getattr(request, "tools", [])), request.runtime)
        return await handler(request.override(tools=tools))
