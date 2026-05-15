"""SQLite persistence for scheduled tasks and run history."""

from __future__ import annotations

import errno as errno
import logging
import os as os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from invincat_cli.scheduler.schema import DDL as DDL
from invincat_cli.scheduler.schema import migrate as migrate
from invincat_cli.scheduler.store_run_ops import SchedulerRunOpsMixin

if TYPE_CHECKING:
    from invincat_cli.scheduler.models import ScheduledTask, TaskRun

logger = logging.getLogger(__name__)

_DB_PATH: Path | None = None
_RUNNING_STALE_GRACE_SECONDS = 60


def get_scheduler_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = Path.home() / ".invincat" / "scheduler.db"
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _DB_PATH


def _connect(path: Path | None = None) -> sqlite3.Connection:
    from invincat_cli.scheduler.store_db import connect

    return connect(path)


def _pid_is_alive(pid: int | None) -> bool:
    """Return True if *pid* currently exists and can be signalled."""
    from invincat_cli.scheduler.store_db import pid_is_alive

    return pid_is_alive(pid)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    from invincat_cli.scheduler.store_db import parse_iso_datetime

    return parse_iso_datetime(value)


def _running_row_is_stale(
    row: sqlite3.Row,
    *,
    task_timeout_seconds: int,
    now: datetime,
) -> bool:
    """Return True when a persisted running row is safe to recover.

    Current-version runners persist their owning process PID.  A missing or
    dead owner means the row is stale.  A live owner is preserved unless the
    run has exceeded its configured timeout plus a short grace period; in that
    case the runner should already have marked it timed out.
    """
    from invincat_cli.scheduler.store_db import running_row_is_stale

    return running_row_is_stale(
        row,
        task_timeout_seconds=task_timeout_seconds,
        now=now,
    )


class SchedulerStore(SchedulerRunOpsMixin):
    """Thread-safe synchronous store for scheduler data."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or get_scheduler_db_path()
        # Ensure schema exists on construction
        with _connect(self._db_path) as conn:
            conn.commit()

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def save_task(self, task: ScheduledTask) -> None:  # noqa: F821
        from invincat_cli.scheduler.models import ScheduledTask

        assert isinstance(task, ScheduledTask)
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO scheduled_tasks
                    (id, title, enabled, prompt, cron, timezone, cwd,
                     delivery, report, created_at, updated_at,
                     next_run_at, last_run_at, last_status, last_error,
                     run_count, failure_count, misfire_policy,
                     schedule_type, run_at, delete_after_run, timeout_seconds)
                VALUES
                    (:id,:title,:enabled,:prompt,:cron,:timezone,:cwd,
                     :delivery,:report,:created_at,:updated_at,
                     :next_run_at,:last_run_at,:last_status,:last_error,
                     :run_count,:failure_count,:misfire_policy,
                     :schedule_type,:run_at,:delete_after_run,:timeout_seconds)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    enabled=excluded.enabled,
                    prompt=excluded.prompt,
                    cron=excluded.cron,
                    timezone=excluded.timezone,
                    cwd=excluded.cwd,
                    delivery=excluded.delivery,
                    report=excluded.report,
                    updated_at=excluded.updated_at,
                    next_run_at=excluded.next_run_at,
                    last_run_at=excluded.last_run_at,
                    last_status=excluded.last_status,
                    last_error=excluded.last_error,
                    run_count=excluded.run_count,
                    failure_count=excluded.failure_count,
                    misfire_policy=excluded.misfire_policy,
                    schedule_type=excluded.schedule_type,
                    run_at=excluded.run_at,
                    delete_after_run=excluded.delete_after_run,
                    timeout_seconds=excluded.timeout_seconds
                """,
                _task_to_row(task),
            )
            conn.commit()

    def load_task(self, task_id: str) -> ScheduledTask | None:  # noqa: F821
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return _row_to_task(row) if row else None

    def list_tasks(
        self,
        *,
        enabled_only: bool = False,
        cwd: str | None = None,
    ) -> list[ScheduledTask]:  # noqa: F821
        with _connect(self._db_path) as conn:
            clauses: list[str] = []
            params: list[Any] = []
            if enabled_only:
                clauses.append("enabled=1")
            if cwd is not None:
                clauses.append("cwd=?")
                params.append(cwd)
            sql = "SELECT * FROM scheduled_tasks"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created_at"
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_task(r) for r in rows]

    def try_start_run(
        self,
        task_id: str,
        run: TaskRun,  # noqa: F821
        *,
        expected_next_run_at: str | None = None,
        next_run_at: str | None = None,
        clear_next_run_at: bool = False,
        require_enabled: bool = True,
    ) -> bool:
        """Atomically claim a task run if no other runner already claimed it.

        This method is the cross-process guard for scheduler execution.  It
        creates the run row and moves the task to ``running`` inside a single
        SQLite write transaction.  Competing TUI/daemon runners serialize on
        ``BEGIN IMMEDIATE``; only the first caller whose task state still
        matches succeeds.
        """
        from invincat_cli.scheduler.models import TaskRun

        assert isinstance(run, TaskRun)
        now = datetime.now(UTC).isoformat()
        with _connect(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                return False
            if require_enabled and not bool(row["enabled"]):
                conn.rollback()
                return False
            if (
                expected_next_run_at is not None
                and row["next_run_at"] != expected_next_run_at
            ):
                conn.rollback()
                return False
            active_rows = conn.execute(
                """
                SELECT * FROM scheduled_task_runs
                WHERE task_id=? AND status='running' AND finished_at IS NULL
                """,
                (task_id,),
            ).fetchall()
            live_rows = [
                active
                for active in active_rows
                if not _running_row_is_stale(
                    active,
                    task_timeout_seconds=int(row["timeout_seconds"] or 600),
                    now=datetime.now(UTC),
                )
            ]
            if live_rows:
                conn.rollback()
                return False
            stale_ids = [active["id"] for active in active_rows]
            if stale_ids:
                placeholders = ",".join("?" for _ in stale_ids)
                conn.execute(
                    f"""
                    UPDATE scheduled_task_runs SET
                        status='failed',
                        finished_at=?,
                        error=COALESCE(error, ?)
                    WHERE id IN ({placeholders})
                        AND status='running'
                        AND finished_at IS NULL
                    """,
                    (
                        now,
                        "recovered stale scheduled run before starting new run",
                        *stale_ids,
                    ),
                )

            conn.execute(
                """
                INSERT INTO scheduled_task_runs
                    (id, task_id, scheduled_for, started_at, finished_at,
                     status, report_path, error, thread_id, cwd,
                     delivery_status, delivery_error, delivered_at, delivery_attempts,
                     runner_id, runner_kind, runner_pid)
                VALUES
                    (:id,:task_id,:scheduled_for,:started_at,:finished_at,
                     :status,:report_path,:error,:thread_id,:cwd,
                     :delivery_status,:delivery_error,:delivered_at,:delivery_attempts,
                     :runner_id,:runner_kind,:runner_pid)
                """,
                {
                    "id": run.id,
                    "task_id": run.task_id,
                    "scheduled_for": run.scheduled_for,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "status": run.status,
                    "report_path": run.report_path,
                    "error": run.error,
                    "thread_id": run.thread_id,
                    "cwd": run.cwd,
                    "delivery_status": run.delivery_status,
                    "delivery_error": run.delivery_error,
                    "delivered_at": run.delivered_at,
                    "delivery_attempts": run.delivery_attempts,
                    "runner_id": run.runner_id,
                    "runner_kind": run.runner_kind,
                    "runner_pid": run.runner_pid,
                },
            )
            conn.execute(
                """
                UPDATE scheduled_tasks SET
                    last_status='running',
                    last_run_at=?,
                    next_run_at=CASE WHEN ? THEN NULL ELSE ? END,
                    last_error=NULL,
                    updated_at=?
                WHERE id=?
                """,
                (
                    run.started_at,
                    int(clear_next_run_at),
                    next_run_at,
                    now,
                    task_id,
                ),
            )
            conn.commit()
            return True

    def update_task_status(
        self,
        task_id: str,
        *,
        last_status: str,
        last_run_at: str | None = None,
        next_run_at: str | None = None,
        clear_next_run_at: bool = False,
        last_error: str | None = None,
        run_count_delta: int = 0,
        failure_count_delta: int = 0,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE scheduled_tasks SET
                    last_status=?,
                    last_run_at=COALESCE(?,last_run_at),
                    next_run_at=CASE WHEN ? THEN NULL ELSE COALESCE(?,next_run_at) END,
                    last_error=?,
                    run_count=run_count+?,
                    failure_count=failure_count+?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    last_status,
                    last_run_at,
                    int(clear_next_run_at),
                    next_run_at,
                    last_error,
                    run_count_delta,
                    failure_count_delta,
                    now,
                    task_id,
                ),
            )
            conn.commit()

    def set_task_enabled(self, task_id: str, enabled: bool) -> None:
        now = datetime.now(UTC).isoformat()
        with _connect(self._db_path) as conn:
            conn.execute(
                "UPDATE scheduled_tasks SET enabled=?, updated_at=? WHERE id=?",
                (int(enabled), now, task_id),
            )
            conn.commit()

    def disable_task_after_run(self, task_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        with _connect(self._db_path) as conn:
            conn.execute(
                "UPDATE scheduled_tasks SET enabled=0, next_run_at=NULL, updated_at=? WHERE id=?",
                (now, task_id),
            )
            conn.commit()

    def delete_task(self, task_id: str) -> bool:
        with _connect(self._db_path) as conn:
            cur = conn.execute("DELETE FROM scheduled_tasks WHERE id=?", (task_id,))
            conn.commit()
        return cur.rowcount > 0







from invincat_cli.scheduler.store_views import (  # noqa: E402,I001
    CwdScopedSchedulerStore as CwdScopedSchedulerStore,
    FilteredSchedulerStore as FilteredSchedulerStore,
)


def _task_to_row(task: Any) -> dict[str, Any]:
    from invincat_cli.scheduler.store_serialization import task_to_row

    return task_to_row(task)


def _row_to_task(row: sqlite3.Row) -> ScheduledTask:  # noqa: F821
    from invincat_cli.scheduler.store_serialization import row_to_task

    return row_to_task(row)


def _row_to_run(row: sqlite3.Row) -> TaskRun:  # noqa: F821
    from invincat_cli.scheduler.store_serialization import row_to_run

    return row_to_run(row)
