"""Persistent UI-only diff history for resumed threads."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_THREAD_DIFF_RECORDS = 1000


@dataclass(frozen=True, slots=True)
class ThreadDiffRecord:
    """A file diff shown in chat for a tool call."""

    tool_call_id: str
    display_path: str
    diff: str
    created_at: float


def _history_root() -> Path:
    return Path.home() / ".invincat" / "thread_diffs"


def _thread_path(thread_id: str) -> Path:
    digest = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    return _history_root() / f"{digest}.json"


def save_thread_diff(
    *,
    thread_id: str | None,
    tool_call_id: str | int | None,
    display_path: str,
    diff: str | None,
) -> None:
    """Persist a UI diff so `/thread` resume can render it later."""
    if not thread_id or tool_call_id is None or not diff:
        return

    path = _thread_path(thread_id)
    try:
        records = _load_raw(path)
        record = {
            "tool_call_id": str(tool_call_id),
            "display_path": display_path,
            "diff": diff,
            "created_at": time.time(),
        }
        records = [
            item
            for item in records
            if str(item.get("tool_call_id", "")) != record["tool_call_id"]
        ]
        records.append(record)
        records = records[-MAX_THREAD_DIFF_RECORDS:]

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        payload = {"thread_id": thread_id, "records": records}
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(path)
    except OSError:
        logger.warning(
            "Failed to persist thread diff history for %s",
            thread_id,
            exc_info=True,
        )
    except Exception:
        logger.warning(
            "Unexpected thread diff history persistence failure",
            exc_info=True,
        )


def load_thread_diffs(thread_id: str | None) -> list[ThreadDiffRecord]:
    """Load persisted UI diffs for a thread."""
    if not thread_id:
        return []

    try:
        records = _load_raw(_thread_path(thread_id))
    except OSError:
        logger.warning(
            "Failed to load thread diff history for %s",
            thread_id,
            exc_info=True,
        )
        return []
    except Exception:
        logger.warning("Unexpected thread diff history load failure", exc_info=True)
        return []

    result: list[ThreadDiffRecord] = []
    for item in records:
        record = _coerce_record(item)
        if record is not None:
            result.append(record)
    return result


def _load_raw(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return []
    records = data.get("records")
    if not isinstance(records, list):
        return []
    return [item for item in records if isinstance(item, dict)]


def _coerce_record(item: dict[str, Any]) -> ThreadDiffRecord | None:
    tool_call_id = item.get("tool_call_id")
    display_path = item.get("display_path")
    diff = item.get("diff")
    created_at = item.get("created_at", 0.0)
    if not isinstance(tool_call_id, str) or not tool_call_id:
        return None
    if not isinstance(display_path, str):
        return None
    if not isinstance(diff, str) or not diff:
        return None
    if not isinstance(created_at, int | float):
        created_at = 0.0
    return ThreadDiffRecord(
        tool_call_id=tool_call_id,
        display_path=display_path,
        diff=diff,
        created_at=float(created_at),
    )
