"""LangChain tool factories for scheduler management."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Annotated

from langchain_core.tools import InjectedToolCallId, tool

from invincat_cli.scheduler.tool_constants import (
    SCHEDULE_CANCEL_TYPE,
    SCHEDULE_CREATE_TYPE,
    SCHEDULE_LIST_TYPE,
    SCHEDULE_RUN_NOW_TYPE,
    SCHEDULE_UPDATE_TYPE,
)
from invincat_cli.scheduler.tool_validation import (
    is_once_schedule_marker,
    parse_once_at,
    validate_schedule_create_options,
    validate_timezone_name,
)

if TYPE_CHECKING:
    from invincat_cli.scheduler.store import SchedulerStore


class ScheduleToolFactoryMixin:
    """Build scheduler management tools bound to ``self._store``."""

    if TYPE_CHECKING:
        _store: SchedulerStore

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
            """Create a scheduled or one-shot delayed task."""
            from invincat_cli.scheduler.parser import parse_schedule

            try:
                timezone = validate_timezone_name(timezone)
            except ValueError as exc:
                return _error(str(exc))

            schedule_type = "once" if once_at else "recurring"
            run_at = None
            if once_at:
                if not is_once_schedule_marker(schedule):
                    return _error(
                        "once_at is only valid for one-shot tasks. "
                        "Use schedule='once' with once_at, or omit once_at "
                        "for recurring schedules."
                    )
                try:
                    run_at = parse_once_at(once_at, timezone)
                except ValueError as exc:
                    return _error(str(exc))
                cron = "0 0 * * *"
            else:
                try:
                    cron = parse_schedule(schedule)
                except ValueError as exc:
                    return _error(str(exc))

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
                return _error(str(exc))

            return _json(
                {
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
            )

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
            for task in tasks:
                channels = getattr(task.delivery, "channels", []) or []
                result.append(
                    {
                        "id": task.id,
                        "title": task.title,
                        "enabled": task.enabled,
                        "cron": task.cron,
                        "schedule_type": task.schedule_type,
                        "run_at": task.run_at,
                        "delete_after_run": task.delete_after_run,
                        "timezone": task.timezone,
                        "next_run_at": task.next_run_at,
                        "next_run_display": format_schedule_time_for_display(
                            task.next_run_at,
                            task.timezone,
                            missing="—",
                        ),
                        "last_status": task.last_status,
                        "run_count": task.run_count,
                        "delivery": channels,
                        "output_mode": getattr(task.report, "mode", "message"),
                    }
                )
            return _json(
                {
                    "type": SCHEDULE_LIST_TYPE,
                    "tasks": result,
                    "tool_call_id": tool_call_id,
                }
            )

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
            """Update an existing scheduled task."""
            from invincat_cli.scheduler.parser import parse_schedule

            task = store.load_task(task_id)
            if task is None:
                return _error(f"Task {task_id!r} not found")
            if schedule is not None and task.schedule_type == "once":
                return _error(
                    "Updating the schedule of a one-shot task is not supported; "
                    "create a new one-shot task instead."
                )

            cron = task.cron
            if timezone is not None:
                try:
                    timezone = validate_timezone_name(timezone)
                except ValueError as exc:
                    return _error(str(exc))

            if schedule is not None:
                try:
                    cron = parse_schedule(schedule)
                except ValueError as exc:
                    return _error(str(exc))

            return _json(
                {
                    "type": SCHEDULE_UPDATE_TYPE,
                    "task_id": task_id,
                    "updates": {
                        key: value
                        for key, value in {
                            "title": title,
                            "cron": cron if schedule else None,
                            "schedule_input": schedule,
                            "prompt": prompt,
                            "enabled": enabled,
                            "timezone": timezone,
                        }.items()
                        if value is not None
                    },
                    "tool_call_id": tool_call_id,
                }
            )

        return update_scheduled_task

    def _make_cancel_tool(self):  # noqa: ANN202
        return self._make_delete_like_tool("cancel_scheduled_task")

    def _make_delete_tool(self):  # noqa: ANN202
        return self._make_delete_like_tool("delete_scheduled_task")

    def _make_delete_like_tool(self, tool_name: str):  # noqa: ANN202
        store = self._store

        def _delete(task_id: str, tool_call_id: Annotated[str, InjectedToolCallId]):
            task = store.load_task(task_id)
            if task is None:
                return _error(f"Task {task_id!r} not found")
            return _json(
                {
                    "type": SCHEDULE_CANCEL_TYPE,
                    "task_id": task_id,
                    "tool_call_id": tool_call_id,
                }
            )

        _delete.__name__ = tool_name
        _delete.__doc__ = "Delete a scheduled task permanently."
        return tool(_delete)

    def _make_run_now_tool(self):  # noqa: ANN202
        store = self._store

        @tool
        def run_scheduled_task_now(
            task_id: str,
            tool_call_id: Annotated[str, InjectedToolCallId],
        ) -> str:
            """Trigger a scheduled task to run immediately."""
            task = store.load_task(task_id)
            if task is None:
                return _error(f"Task {task_id!r} not found")
            return _json(
                {
                    "type": SCHEDULE_RUN_NOW_TYPE,
                    "task_id": task_id,
                    "title": task.title,
                    "tool_call_id": tool_call_id,
                }
            )

        return run_scheduled_task_now


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _error(message: str) -> str:
    return _json({"error": message})
