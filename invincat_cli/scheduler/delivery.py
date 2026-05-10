"""Delivery channels for scheduled task results."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from invincat_cli.scheduler.models import ScheduledTask

logger = logging.getLogger(__name__)


def check_report_exists(task: "ScheduledTask", date_str: str) -> str | None:
    """Return the report path if the file exists, else None."""
    import re

    report = task.report
    filename = report.filename_template.format(
        task_slug=re.sub(r"[^\w\-]", "-", task.title.lower())[:40].strip("-"),
        date=date_str,
    )
    report_path = Path(task.cwd) / report.output_dir / filename
    if report_path.exists() and report_path.stat().st_size > 0:
        return str(report_path)
    return None


def save_fallback_report(task: "ScheduledTask", content: str, date_str: str) -> str | None:
    """Write agent response text as a fallback report and return the path."""
    import re

    if not content.strip():
        return None
    try:
        report = task.report
        slug = re.sub(r"[^\w\-]", "-", task.title.lower())[:40].strip("-")
        filename = report.filename_template.format(task_slug=slug, date=date_str)
        report_dir = Path(task.cwd) / report.output_dir
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / filename
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
