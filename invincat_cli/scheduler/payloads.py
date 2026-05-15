"""Helpers for schedule tool payloads consumed by app frontends."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from invincat_cli.scheduler.display import (
    describe_schedule_for_display,
    format_schedule_time_for_display,
)
from invincat_cli.scheduler.models import DeliverySpec, ReportSpec, ScheduledTask
from invincat_cli.scheduler.runner import _parse_dt, compute_next_run
from invincat_cli.scheduler.tool import (
    validate_schedule_create_options,
    validate_timezone_name,
)
from invincat_cli.wecom.protocol import resolve_wecom_active_chat_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScheduleCreatePayloadResult:
    """Normalized scheduled task plus display fields for the create payload."""

    task: ScheduledTask
    schedule_description: str
    next_run_display: str
    report_path_display: str


def _report_filename_template(title: str, report_format: str) -> str:
    slug = re.sub(r"[^\w\-]", "-", title.lower())[:40].strip("-")
    extension = report_format.lower().replace("markdown", "md")
    return f"{slug}-{{date}}.{extension}"


def build_schedule_create_payload_result(
    payload: dict[str, Any],
    *,
    cwd: str | Path,
    active_wecom_frame: dict[str, Any] | None = None,
    now: datetime | None = None,
    resolve_active_chat_id: Callable[
        [dict[str, Any]], str
    ] = resolve_wecom_active_chat_id,
) -> ScheduleCreatePayloadResult:
    """Validate a schedule_create payload and build the task to persist."""
    task_id = payload.get("task_id") or str(uuid.uuid4())
    title = payload.get("title", "Untitled")
    cron = payload.get("cron", "0 8 * * *")
    tz = validate_timezone_name(payload.get("timezone", "Asia/Shanghai"))
    prompt_text = payload.get("prompt", "")
    schedule_type = payload.get("schedule_type", "recurring")
    if schedule_type not in {"recurring", "once"}:
        schedule_type = "recurring"
    run_at = payload.get("run_at")
    delete_after_run = bool(payload.get("delete_after_run", False))
    output_mode, report_format, misfire_policy, timeout_seconds = (
        validate_schedule_create_options(
            output_mode=payload.get("output_mode", "message"),
            report_format=payload.get("report_format", "markdown"),
            misfire_policy=payload.get("misfire_policy", "run_once"),
            timeout_seconds=payload.get("timeout_seconds", 600),
        )
    )

    delivery_channel = payload.get("delivery", "tui")
    delivery = DeliverySpec()
    if active_wecom_frame is not None and delivery_channel in {"tui", "wecom"}:
        try:
            delivery = DeliverySpec(
                channels=[
                    {
                        "type": "wecom",
                        "chatid": resolve_active_chat_id(active_wecom_frame),
                    }
                ]
            )
        except Exception:
            logger.warning("Could not resolve WeCom delivery target", exc_info=True)

    effective_now = now or datetime.now(UTC)
    next_run = (
        _parse_dt(run_at)
        if schedule_type == "once"
        else compute_next_run(cron, effective_now, tz)
    )
    if next_run is None:
        msg = (
            "Could not compute the next scheduled run time. "
            "Check the schedule, timezone, and once_at value."
        )
        raise ValueError(msg)

    task = ScheduledTask(
        id=str(task_id),
        title=str(title),
        enabled=True,
        prompt=str(prompt_text),
        cron=str(cron),
        timezone=tz,
        cwd=str(cwd),
        delivery=delivery,
        report=ReportSpec(
            mode=output_mode,
            output_dir="reports",
            filename_template=_report_filename_template(str(title), report_format),
            format=report_format,
        ),
        created_at=effective_now.isoformat(),
        updated_at=effective_now.isoformat(),
        next_run_at=next_run.isoformat(),
        last_run_at=None,
        last_status="never",
        last_error=None,
        run_count=0,
        failure_count=0,
        misfire_policy=misfire_policy,
        schedule_type=schedule_type,
        run_at=run_at if schedule_type == "once" else None,
        delete_after_run=delete_after_run,
        timeout_seconds=timeout_seconds,
    )

    report_path = "message only"
    if output_mode == "report":
        from invincat_cli.scheduler.delivery import report_display_path

        report_path = report_display_path(task, "{date}")

    return ScheduleCreatePayloadResult(
        task=task,
        schedule_description=describe_schedule_for_display(cron, tz, schedule_type),
        next_run_display=format_schedule_time_for_display(next_run, tz),
        report_path_display=report_path,
    )


def apply_schedule_update_payload(
    task: ScheduledTask,
    updates: dict[str, Any],
    *,
    now: datetime | None = None,
) -> ScheduledTask:
    """Apply a schedule_update payload to an existing task."""
    if "title" in updates:
        task.title = str(updates["title"])
    if "cron" in updates:
        task.cron = str(updates["cron"])
    if "prompt" in updates:
        task.prompt = str(updates["prompt"])
    if "enabled" in updates:
        task.enabled = bool(updates["enabled"])
    if "timezone" in updates:
        task.timezone = validate_timezone_name(updates["timezone"])

    if "cron" in updates or "timezone" in updates:
        effective_now = now or datetime.now(UTC)
        next_run = (
            _parse_dt(task.run_at)
            if task.schedule_type == "once"
            else compute_next_run(task.cron, effective_now, task.timezone)
        )
        if next_run is None:
            msg = (
                "Could not compute the next scheduled run time. "
                "Check the schedule, timezone, and once_at value."
            )
            raise ValueError(msg)
        task.next_run_at = next_run.isoformat()

    task.updated_at = (now or datetime.now(UTC)).isoformat()
    return task


def format_schedule_list_item(task_info: dict[str, Any]) -> str:
    """Format one item from a schedule_list tool payload."""
    status_icon = "✓" if task_info.get("enabled") else "✗"
    tz = task_info.get("timezone", "UTC")
    desc = describe_schedule_for_display(
        task_info.get("cron", ""),
        tz,
        task_info.get("schedule_type", "recurring"),
    )
    next_run = (
        task_info.get("next_run_display")
        or format_schedule_time_for_display(
            task_info.get("next_run_at"),
            tz,
            missing="—",
        )
    ).replace("T", " ")
    return (
        f"  {status_icon} {task_info['title']} — {desc} — next: {next_run}"
        f"  [id: {task_info['id'][:8]}]"
    )
