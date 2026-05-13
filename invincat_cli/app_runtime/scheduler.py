"""Scheduler runtime helpers for the Textual app."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from pathlib import Path

from invincat_cli.app_runtime.state import QueuedMessage


def remove_scheduled_messages(
    messages: Iterable[QueuedMessage],
    *,
    run_id: str,
    task_id: str,
) -> deque[QueuedMessage]:
    """Return queued messages excluding one scheduled run."""
    return deque(
        msg
        for msg in messages
        if not (
            msg.scheduled_run_id == run_id
            and msg.scheduled_task_id == task_id
        )
    )


def scheduled_run_matches(
    active_scheduled_run: tuple[str, str] | None,
    *,
    run_id: str,
    task_id: str,
) -> bool:
    """Return whether the active scheduled run matches the given ids."""
    return active_scheduled_run == (run_id, task_id)


def active_scheduled_task_id(
    active_scheduled_run: tuple[str, str] | None,
) -> str | None:
    """Return the task id for the active scheduled run, if any."""
    if active_scheduled_run is None:
        return None
    _run_id, task_id = active_scheduled_run
    return task_id


def resolve_scheduled_wecom_file_path(
    raw_path: object,
    *,
    cwd: str | Path,
) -> Path:
    """Resolve and validate a scheduled WeCom file-send path."""
    raw = str(raw_path or "").strip()
    if not raw:
        raise ValueError("send_wecom_file payload missing path")

    path = Path(raw).expanduser().resolve()
    root = Path(cwd).expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"WeCom file sending is limited to the current project: {root}"
        ) from exc
    if not path.is_file():
        raise ValueError(f"File does not exist or is not a regular file: {path}")
    return path
