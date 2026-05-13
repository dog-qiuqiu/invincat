"""Delivery channels for scheduled task results."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from invincat_cli.scheduler.models import ScheduledTask

logger = logging.getLogger(__name__)


def scheduled_task_wecom_chatid(task: Any) -> str:  # noqa: ANN401
    """Return the non-empty WeCom chatid configured for a scheduled task."""
    delivery = getattr(task, "delivery", None)
    channels = getattr(delivery, "channels", []) or []
    for channel in channels:
        if not isinstance(channel, dict) or channel.get("type") != "wecom":
            continue
        chatid = str(channel.get("chatid") or "").strip()
        if chatid:
            return chatid
    return ""


def is_wecom_deliverable_task(task: Any) -> bool:  # noqa: ANN401
    """Return True if a scheduled task has a concrete WeCom delivery target."""
    return bool(scheduled_task_wecom_chatid(task))


def _report_filename(task: ScheduledTask, date_str: str) -> str:
    import re

    report = task.report
    return report.filename_template.format(
        task_slug=re.sub(r"[^\w\-]", "-", task.title.lower())[:40].strip("-"),
        date=date_str,
    )


def resolve_report_path(task: ScheduledTask, date_str: str) -> Path:
    """Return a checked absolute report path under the task working directory."""
    report = task.report
    filename = _report_filename(task, date_str)
    cwd = Path(task.cwd).expanduser().resolve()
    output_dir = Path(report.output_dir)
    filename_path = Path(filename)
    if output_dir.is_absolute() or filename_path.is_absolute():
        raise ValueError("Scheduled report paths must be relative")
    report_path = (cwd / output_dir / filename_path).resolve()
    try:
        report_path.relative_to(cwd)
    except ValueError as exc:
        raise ValueError("Scheduled report path escapes the task working directory") from exc
    return report_path


def report_display_path(task: ScheduledTask, date_str: str) -> str:
    """Return the relative report path shown to users and agent prompts."""
    path = resolve_report_path(task, date_str)
    return path.relative_to(Path(task.cwd).expanduser().resolve()).as_posix()


def check_report_exists(task: ScheduledTask, date_str: str) -> str | None:
    """Return the report path if the file exists, else None."""
    try:
        report_path = resolve_report_path(task, date_str)
    except ValueError:
        logger.warning("Invalid scheduled report path", exc_info=True)
        return None
    if report_path.exists() and report_path.stat().st_size > 0:
        return str(report_path)
    return None


def save_fallback_report(task: ScheduledTask, content: str, date_str: str) -> str | None:
    """Write agent response text as a fallback report and return the path."""
    if not content.strip():
        return None
    try:
        report_path = resolve_report_path(task, date_str)
        report_dir = report_path.parent
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(content, encoding="utf-8")
        logger.info("Saved fallback report to %s", report_path)
        return str(report_path)
    except Exception:
        logger.warning("Failed to save fallback report", exc_info=True)
        return None


async def deliver_webhook(url: str, payload: dict) -> None:
    """POST payload to a webhook URL."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
    except Exception:
        logger.warning("Webhook delivery failed to %s", url, exc_info=True)
