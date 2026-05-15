"""Schedule payload persistence for headless WeCom mode."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


async def process_schedule_payload(
    *,
    payload: dict[str, Any],
    inbound_frame: dict[str, Any],
    cwd: Path,
    on_schedule_run_now: Any,
) -> None:
    """Persist a schedule management payload from a headless WeCom agent turn."""
    from invincat_cli.scheduler.runner import _parse_dt, compute_next_run
    from invincat_cli.scheduler.store import SchedulerStore
    from invincat_cli.scheduler.tool import (
        SCHEDULE_CANCEL_TYPE,
        SCHEDULE_CREATE_TYPE,
        SCHEDULE_RUN_NOW_TYPE,
        SCHEDULE_UPDATE_TYPE,
        validate_timezone_name,
    )

    ptype = payload.get("type")
    store = SchedulerStore()
    current_cwd = str(cwd)

    def load_current_cwd_task(task_id: str, operation: str) -> Any:
        task = store.load_task(task_id)
        if task is None:
            logger.warning("%s: task %r not found", operation, task_id)
            raise ValueError(f"{operation}: scheduled task {task_id!r} not found")
        if task.cwd != current_cwd:
            logger.warning(
                "%s: refusing to operate on task %r from cwd=%r while daemon cwd=%r",
                operation,
                task_id,
                task.cwd,
                current_cwd,
            )
            raise ValueError(
                f"{operation}: scheduled task {task_id!r} belongs to another "
                f"project (task cwd={task.cwd!r}, daemon cwd={current_cwd!r})"
            )
        return task

    if ptype == SCHEDULE_CREATE_TYPE:
        task = _build_create_task(payload, inbound_frame, cwd)
        store.save_task(task)
        logger.info(
            "Scheduled task created: %r id=%s next_run=%s delivery=%s",
            task.title,
            task.id,
            task.next_run_at,
            [c.get("type") for c in (task.delivery.channels or [])],
        )
        return

    if ptype == SCHEDULE_UPDATE_TYPE:
        task_id = payload.get("task_id", "")
        task = load_current_cwd_task(task_id, "schedule_update")
        updates = payload.get("updates", {})
        if "title" in updates:
            task.title = updates["title"]
        if "cron" in updates:
            task.cron = updates["cron"]
        if "prompt" in updates:
            task.prompt = updates["prompt"]
        if "enabled" in updates:
            task.enabled = bool(updates["enabled"])
        if "timezone" in updates:
            task.timezone = validate_timezone_name(updates["timezone"])
        if "cron" in updates or "timezone" in updates:
            next_run = (
                _parse_dt(task.run_at)
                if task.schedule_type == "once"
                else compute_next_run(task.cron, datetime.now(UTC), task.timezone)
            )
            if next_run is None:
                raise ValueError(
                    "Could not compute the next scheduled run time. "
                    "Check the schedule, timezone, and once_at value."
                )
            task.next_run_at = next_run.isoformat() if next_run else None
        task.updated_at = datetime.now(UTC).isoformat()
        store.save_task(task)
        logger.info("Scheduled task updated: %r id=%s", task.title, task_id)
        return

    if ptype == SCHEDULE_CANCEL_TYPE:
        task_id = payload.get("task_id", "")
        load_current_cwd_task(task_id, "schedule_cancel")
        store.delete_task(task_id)
        logger.info("Scheduled task deleted: id=%s", task_id)
        return

    if ptype == SCHEDULE_RUN_NOW_TYPE and on_schedule_run_now is not None:
        task_id = payload.get("task_id", "")
        task = load_current_cwd_task(task_id, "schedule_run_now")
        await on_schedule_run_now(task)
        logger.info("Scheduled task fired immediately: %r id=%s", task.title, task_id)


def _build_create_task(
    payload: dict[str, Any],
    inbound_frame: dict[str, Any],
    cwd: Path,
):
    from invincat_cli.scheduler.models import ReportSpec, ScheduledTask
    from invincat_cli.scheduler.runner import _parse_dt, compute_next_run
    from invincat_cli.scheduler.tool import (
        validate_schedule_create_options,
        validate_timezone_name,
    )

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
    slug = re.sub(r"[^\w\-]", "-", title.lower())[:40].strip("-")
    delivery = _resolve_delivery(payload, inbound_frame)

    now = datetime.now(UTC)
    next_run = (
        _parse_dt(run_at) if schedule_type == "once" else compute_next_run(cron, now, tz)
    )
    if next_run is None:
        raise ValueError(
            "Could not compute the next scheduled run time. "
            "Check the schedule, timezone, and once_at value."
        )
    return ScheduledTask(
        id=task_id,
        title=title,
        enabled=True,
        prompt=prompt_text,
        cron=cron,
        timezone=tz,
        cwd=str(cwd),
        delivery=delivery,
        report=ReportSpec(
            mode=output_mode,
            output_dir="reports",
            filename_template=f"{slug}-{{date}}.{report_format.lower().replace('markdown', 'md')}",
            format=report_format,
        ),
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
        next_run_at=next_run.isoformat() if next_run else None,
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


def _resolve_delivery(payload: dict[str, Any], inbound_frame: dict[str, Any]):
    from invincat_cli.scheduler.models import DeliverySpec
    from invincat_cli.wecom.protocol import resolve_wecom_active_chat_id

    delivery = DeliverySpec()
    frame_chatid = str((inbound_frame.get("body") or {}).get("chatid", ""))
    if frame_chatid.startswith("__scheduled_"):
        return delivery

    chatid_to_use = ""
    try:
        chatid_to_use = resolve_wecom_active_chat_id(inbound_frame) or ""
    except Exception:
        logger.warning(
            "resolve_wecom_active_chat_id failed for scheduled task %r; "
            "WeCom delivery will not be configured (frame_chatid=%r)",
            payload.get("task_id", ""),
            frame_chatid,
            exc_info=True,
        )
    if chatid_to_use:
        delivery = DeliverySpec(channels=[{"type": "wecom", "chatid": chatid_to_use}])
        logger.info(
            "Scheduled task %r WeCom delivery configured: chatid=%s (single-chat fallback=%s)",
            payload.get("task_id", ""),
            chatid_to_use,
            not bool(frame_chatid),
        )
    else:
        logger.warning(
            "WeCom chatid is empty for scheduled task %r; WeCom delivery will not be configured",
            payload.get("task_id", ""),
        )
    return delivery
