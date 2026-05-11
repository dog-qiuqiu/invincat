"""Parse natural-language / shorthand schedule strings into cron expressions."""

from __future__ import annotations

import re

_WEEKDAY_MAP = {
    "mon": "1", "monday": "1",
    "tue": "2", "tuesday": "2",
    "wed": "3", "wednesday": "3",
    "thu": "4", "thursday": "4",
    "fri": "5", "friday": "5",
    "sat": "6", "saturday": "6",
    "sun": "0", "sunday": "0",
}

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_INTERVAL_RE = re.compile(r"^interval\s+(\d+)(h|m|min|hours?|minutes?)$", re.IGNORECASE)
_CRON_RE = re.compile(r"^(\S+\s+){4}\S+$")


def _parse_time(time_str: str) -> tuple[str, str]:
    """Return (minute, hour) for a HH:MM string."""
    m = _TIME_RE.match(time_str.strip())
    if not m:
        msg = f"Invalid time format: {time_str!r} (expected HH:MM)"
        raise ValueError(msg)
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        msg = f"Time out of range: {time_str!r}"
        raise ValueError(msg)
    return str(minute), str(hour)


def parse_schedule(expr: str) -> str:
    """Convert a schedule expression to a standard 5-field cron string.

    Supported formats:
        daily HH:MM
        daily                   (defaults to 08:00)
        weekly <weekday> HH:MM
        weekly <weekday>        (defaults to 08:00)
        monthly <day> HH:MM
        monthly <day>           (defaults to 08:00)
        interval <N>h | <N>m
        cron <5-field expr>
        <bare 5-field cron>

    Returns a normalised cron string like "0 8 * * *".
    """
    expr = expr.strip()

    # Bare 5-field cron
    if _CRON_RE.match(expr):
        _validate_cron(expr)
        return expr

    lower = expr.lower()

    # interval Nh / interval Nm
    m = _INTERVAL_RE.match(lower)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        if unit.startswith("h"):
            if not (1 <= n <= 23):
                msg = f"Hour interval must be 1-23, got {n}"
                raise ValueError(msg)
            return f"0 */{n} * * *"
        else:
            if not (1 <= n <= 59):
                msg = f"Minute interval must be 1-59, got {n}"
                raise ValueError(msg)
            return f"*/{n} * * * *"

    parts = expr.split()
    keyword = parts[0].lower() if parts else ""

    # cron <expr>
    if keyword == "cron":
        cron_expr = " ".join(parts[1:])
        if not _CRON_RE.match(cron_expr):
            msg = f"Invalid cron expression after 'cron': {cron_expr!r}"
            raise ValueError(msg)
        _validate_cron(cron_expr)
        return cron_expr

    # daily [HH:MM]
    if keyword == "daily":
        if len(parts) > 2:
            msg = "daily accepts at most one time argument: e.g. 'daily 08:00'"
            raise ValueError(msg)
        time_str = parts[1] if len(parts) > 1 else "08:00"
        minute, hour = _parse_time(time_str)
        return f"{minute} {hour} * * *"

    # weekly <weekday> [HH:MM]
    if keyword == "weekly":
        if len(parts) < 2:
            msg = "weekly requires a weekday: e.g. 'weekly mon 08:00'"
            raise ValueError(msg)
        if len(parts) > 3:
            msg = "weekly accepts only weekday and optional time: e.g. 'weekly mon 08:00'"
            raise ValueError(msg)
        day_str = parts[1].lower()
        dow = _WEEKDAY_MAP.get(day_str)
        if dow is None:
            msg = f"Unknown weekday: {parts[1]!r}"
            raise ValueError(msg)
        time_str = parts[2] if len(parts) > 2 else "08:00"
        minute, hour = _parse_time(time_str)
        return f"{minute} {hour} * * {dow}"

    # monthly <day> [HH:MM]
    if keyword == "monthly":
        if len(parts) < 2:
            msg = "monthly requires a day-of-month: e.g. 'monthly 1 08:00'"
            raise ValueError(msg)
        if len(parts) > 3:
            msg = "monthly accepts only day and optional time: e.g. 'monthly 1 08:00'"
            raise ValueError(msg)
        try:
            dom = int(parts[1])
        except ValueError:
            msg = f"Invalid day-of-month: {parts[1]!r}"
            raise ValueError(msg) from None
        if not (1 <= dom <= 31):
            msg = f"Day-of-month must be 1-31 (got {dom})"
            raise ValueError(msg)
        time_str = parts[2] if len(parts) > 2 else "08:00"
        minute, hour = _parse_time(time_str)
        return f"{minute} {hour} {dom} * *"

    msg = f"Unrecognised schedule expression: {expr!r}"
    raise ValueError(msg)


def _validate_cron(expr: str) -> None:
    """Raise ValueError if croniter rejects the expression."""
    try:
        from croniter import croniter

        if not croniter.is_valid(expr):
            msg = f"Invalid cron expression: {expr!r}"
            raise ValueError(msg)
    except ImportError:
        pass


def describe_schedule(cron: str, timezone: str = "UTC") -> str:
    """Return a human-readable description of a cron expression."""
    parts = cron.split()
    if len(parts) != 5:
        return cron
    minute, hour, dom, month, dow = parts

    # daily at fixed time
    if dom == "*" and month == "*" and dow == "*" and not minute.startswith("*/") and not hour.startswith("*/"):
        try:
            h, m = int(hour), int(minute)
            return f"daily {h:02d}:{m:02d}"
        except ValueError:
            pass

    # interval hours
    if minute == "0" and hour.startswith("*/") and dom == "*" and month == "*" and dow == "*":
        return f"every {hour[2:]} hours"

    # interval minutes
    if minute.startswith("*/") and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return f"every {minute[2:]} minutes"

    # weekly
    if dom == "*" and month == "*" and dow != "*":
        rev = {v: k for k, v in _WEEKDAY_MAP.items() if len(k) == 3}
        day_name = rev.get(dow, dow)
        try:
            h, m = int(hour), int(minute)
            return f"weekly {day_name} {h:02d}:{m:02d}"
        except ValueError:
            pass

    # monthly
    if dom != "*" and month == "*" and dow == "*":
        try:
            h, m = int(hour), int(minute)
            return f"monthly {dom} {h:02d}:{m:02d}"
        except ValueError:
            pass

    return cron
