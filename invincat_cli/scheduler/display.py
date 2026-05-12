"""Display helpers for scheduled task times."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def describe_schedule_for_display(
    cron: str,
    timezone_name: str,
    schedule_type: str,
) -> str:
    """Return user-facing schedule text without exposing one-shot placeholder cron."""
    if schedule_type == "once":
        return "once"
    from invincat_cli.scheduler.parser import describe_schedule

    return describe_schedule(cron, timezone_name)


def format_schedule_time_for_display(
    value: Any,  # noqa: ANN401
    timezone_name: str,
    *,
    missing: str = "unknown",
) -> str:
    """Format an ISO/UTC scheduled timestamp in the task's local timezone."""
    if value in (None, ""):
        return missing
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return str(value)
    else:
        return str(value)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return dt.astimezone(timezone.utc).isoformat(timespec="minutes")
    return dt.astimezone(tz).isoformat(timespec="minutes")
