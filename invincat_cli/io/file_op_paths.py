"""Path and filesystem helpers for file operation tracking."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def safe_read(path: Path) -> str | None:
    """Read file content, returning None on failure."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.debug("Failed to read file %s: %s", path, e)
        return None


def resolve_physical_path(
    path_str: str | None,
    assistant_id: str | None,
    *,
    path_cls: Any = Path,
) -> Path | None:
    """Convert a virtual/relative path to a physical filesystem path."""
    if not path_str:
        return None
    try:
        if assistant_id and path_str.startswith("/memories/"):
            from invincat_cli.config import settings

            agent_dir = settings.get_agent_dir(assistant_id)
            suffix = path_str.removeprefix("/memories/").lstrip("/")
            return (agent_dir / suffix).resolve()
        path = path_cls(path_str)
        if path.is_absolute():
            return path
        return (path_cls.cwd() / path).resolve()
    except (OSError, ValueError):
        return None


def format_display_path(path_str: str | None, *, path_cls: Any = Path) -> str:
    """Format a path for display."""
    if not path_str:
        return "(unknown)"
    try:
        path = path_cls(path_str)
        if path.is_absolute():
            return path.name or str(path)
        return str(path)
    except (OSError, ValueError):
        return str(path_str)
