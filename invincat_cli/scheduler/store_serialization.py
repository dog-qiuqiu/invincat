"""Row serialization helpers for scheduler storage."""

from __future__ import annotations

import json
from typing import Any


def task_to_row(task: Any) -> dict[str, Any]:
    """Convert a ScheduledTask-like object to a SQLite row mapping."""
    from invincat_cli.scheduler.models import DeliverySpec, ReportSpec

    delivery = task.delivery
    report = task.report
    return {
        "id": task.id,
        "title": task.title,
        "enabled": int(task.enabled),
        "prompt": task.prompt,
        "cron": task.cron,
        "timezone": task.timezone,
        "cwd": task.cwd,
        "delivery": json.dumps(
            delivery.__dict__ if isinstance(delivery, DeliverySpec) else delivery,
            ensure_ascii=False,
        ),
        "report": json.dumps(
            report.__dict__ if isinstance(report, ReportSpec) else report,
            ensure_ascii=False,
        ),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "next_run_at": task.next_run_at,
        "last_run_at": task.last_run_at,
        "last_status": task.last_status,
        "last_error": task.last_error,
        "run_count": task.run_count,
        "failure_count": task.failure_count,
        "misfire_policy": task.misfire_policy,
        "schedule_type": task.schedule_type,
        "run_at": task.run_at,
        "delete_after_run": int(task.delete_after_run),
        "timeout_seconds": task.timeout_seconds,
    }


def row_to_task(row):
    """Convert a scheduled_tasks SQLite row to ScheduledTask."""
    from invincat_cli.scheduler.models import DeliverySpec, ReportSpec, ScheduledTask

    delivery_d = json.loads(row["delivery"] or "{}")
    report_d = json.loads(row["report"] or "{}")
    return ScheduledTask(
        id=row["id"],
        title=row["title"],
        enabled=bool(row["enabled"]),
        prompt=row["prompt"],
        cron=row["cron"],
        timezone=row["timezone"],
        cwd=row["cwd"],
        delivery=DeliverySpec(**delivery_d) if delivery_d else DeliverySpec(),
        report=ReportSpec(**report_d) if report_d else ReportSpec(),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        next_run_at=row["next_run_at"],
        last_run_at=row["last_run_at"],
        last_status=row["last_status"],
        last_error=row["last_error"],
        run_count=row["run_count"],
        failure_count=row["failure_count"],
        misfire_policy=row["misfire_policy"],
        schedule_type=row["schedule_type"],
        run_at=row["run_at"],
        delete_after_run=bool(row["delete_after_run"]),
        timeout_seconds=row["timeout_seconds"],
    )


def row_to_run(row):
    """Convert a scheduled_task_runs SQLite row to TaskRun."""
    from invincat_cli.scheduler.models import TaskRun

    return TaskRun(
        id=row["id"],
        task_id=row["task_id"],
        scheduled_for=row["scheduled_for"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        report_path=row["report_path"],
        error=row["error"],
        thread_id=row["thread_id"],
        cwd=row["cwd"],
        delivery_status=row["delivery_status"],
        delivery_error=row["delivery_error"],
        delivered_at=row["delivered_at"],
        delivery_attempts=row["delivery_attempts"],
        runner_id=row["runner_id"],
        runner_kind=row["runner_kind"],
        runner_pid=row["runner_pid"],
    )
