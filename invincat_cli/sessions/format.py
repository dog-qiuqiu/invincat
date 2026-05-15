"""Display formatting helpers for session/thread metadata."""

from __future__ import annotations

from datetime import datetime

from invincat_cli import sessions as _sessions


def format_timestamp(iso_timestamp: str | None) -> str:
    """Format ISO timestamp for display (e.g., 'Dec 30, 6:10pm')."""
    if not iso_timestamp:
        return ""
    try:
        dt = datetime.fromisoformat(iso_timestamp).astimezone()
        return (
            dt.strftime("%b %d, %-I:%M%p")
            .lower()
            .replace("am", "am")
            .replace("pm", "pm")
        )
    except (ValueError, TypeError):
        _sessions.logger.debug(
            "Failed to parse timestamp %r; displaying as blank",
            iso_timestamp,
            exc_info=True,
        )
        return ""


def format_relative_timestamp(iso_timestamp: str | None) -> str:
    """Format ISO timestamp as relative time (e.g., '5m ago', '2h ago')."""
    if not iso_timestamp:
        return ""
    try:
        dt = datetime.fromisoformat(iso_timestamp).astimezone()
    except (ValueError, TypeError):
        _sessions.logger.debug(
            "Failed to parse timestamp %r; displaying as blank",
            iso_timestamp,
            exc_info=True,
        )
        return ""

    delta = datetime.now(tz=dt.tzinfo) - dt
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:  # noqa: PLR2004
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:  # noqa: PLR2004
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:  # noqa: PLR2004
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:  # noqa: PLR2004
        return f"{days}d ago"
    months = days // 30
    if months < 12:  # noqa: PLR2004
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"


def format_path(path: str | None) -> str:
    """Format a filesystem path for display."""
    if not path:
        return ""
    try:
        home = str(_sessions.Path.home())
        if path == home:
            return "~"
        prefix = home + "/"
        if path.startswith(prefix):
            return "~/" + path[len(prefix) :]
    except (RuntimeError, KeyError, OSError):
        _sessions.logger.debug(
            "Could not resolve home directory for path formatting", exc_info=True
        )
        return path
    else:
        return path
