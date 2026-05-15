"""SQLite connection and running-row health helpers for scheduler storage."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


def connect(path: Path | None = None):
    """Open and initialize a scheduler SQLite connection."""
    from invincat_cli.scheduler import store as _store

    db_path = path or _store.get_scheduler_db_path()
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        msg = f"unable to create scheduler database directory for {db_path}: {exc}"
        raise _store.sqlite3.OperationalError(msg) from exc
    try:
        conn = _store.sqlite3.connect(str(db_path))
    except _store.sqlite3.OperationalError as exc:
        msg = f"unable to open scheduler database at {db_path}: {exc}"
        raise _store.sqlite3.OperationalError(msg) from exc
    conn.row_factory = _store.sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_store.DDL)
    _store.migrate(conn)
    return conn


def pid_is_alive(pid: int | None) -> bool:
    """Return True if *pid* currently exists and can be signalled."""
    from invincat_cli.scheduler import store as _store

    if pid is None or pid <= 0:
        return False
    try:
        _store.os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == _store.errno.ESRCH:
            return False
        if exc.errno == _store.errno.EPERM:
            return True
        raise
    return True


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse an ISO datetime as UTC, accepting naive timestamps as UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def running_row_is_stale(
    row,
    *,
    task_timeout_seconds: int,
    now: datetime,
) -> bool:
    """Return True when a persisted running row is safe to recover."""
    from invincat_cli.scheduler import store as _store

    try:
        pid = row["runner_pid"]
    except (IndexError, KeyError):
        pid = None
    if not _store._pid_is_alive(pid):
        return True

    if task_timeout_seconds <= 0:
        return False

    started_at = _store._parse_iso_datetime(row["started_at"])
    if started_at is None:
        return False
    stale_after = task_timeout_seconds + _store._RUNNING_STALE_GRACE_SECONDS
    return (now - started_at).total_seconds() > stale_after
