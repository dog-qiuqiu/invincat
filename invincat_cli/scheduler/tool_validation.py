"""Validation and payload parsing helpers for scheduler tools."""

from __future__ import annotations

import json
from datetime import UTC
from typing import Any, Literal

from invincat_cli.scheduler.tool_constants import (
    SCHEDULE_CANCEL_TYPE,
    SCHEDULE_CREATE_TYPE,
    SCHEDULE_LIST_TYPE,
    SCHEDULE_RUN_NOW_TYPE,
    SCHEDULE_UPDATE_TYPE,
)


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
    output_mode_v = _validate_choice(
        output_mode, "message", {"message", "report"}, "output_mode"
    )
    report_format_v = _validate_choice(
        report_format, "markdown", {"markdown", "text"}, "report_format"
    )
    misfire_policy_v = _validate_choice(
        misfire_policy, "run_once", {"run_once", "skip"}, "misfire_policy"
    )
    try:
        timeout_seconds_i = int(timeout_seconds if timeout_seconds is not None else 600)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be an integer >= 0") from exc
    if timeout_seconds_i < 0:
        raise ValueError("timeout_seconds must be >= 0")

    return (
        "report" if output_mode_v == "report" else "message",
        "text" if report_format_v == "text" else "markdown",
        "skip" if misfire_policy_v == "skip" else "run_once",
        timeout_seconds_i,
    )


def is_once_schedule_marker(schedule: str) -> bool:
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


def _validate_choice(value: Any, default: str, allowed: set[str], name: str) -> str:
    normalized = str(value or default)
    if normalized not in allowed:
        allowed_text = " or ".join(f"'{item}'" for item in sorted(allowed))
        raise ValueError(f"{name} must be {allowed_text}")
    return normalized
