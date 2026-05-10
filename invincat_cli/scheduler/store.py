"""SQLite persistence for scheduled tasks and run history."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DB_PATH: Path | None = None


def get_scheduler_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = Path.home() / ".invincat" / "scheduler.db"
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _DB_PATH


_DDL = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    prompt      TEXT NOT NULL,
    cron        TEXT NOT NULL,
    timezone    TEXT NOT NULL DEFAULT 'UTC',
    cwd         TEXT NOT NULL,
    delivery    TEXT NOT NULL DEFAULT '{}',
    report      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    next_run_at TEXT,
    last_run_at TEXT,
    last_status TEXT NOT NULL DEFAULT 'never',
    last_error  TEXT,
    run_count   INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    misfire_policy TEXT NOT NULL DEFAULT 'run_once',
    schedule_type TEXT NOT NULL DEFAULT 'recurring',
    run_at TEXT,
    delete_after_run INTEGER NOT NULL DEFAULT 0,
    timeout_seconds INTEGER NOT NULL DEFAULT 600
);

CREATE TABLE IF NOT EXISTS scheduled_task_runs (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    report_path  TEXT,
    error        TEXT,
    thread_id    TEXT,
    cwd          TEXT NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'none',
    delivery_error TEXT,
    delivered_at TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)
);
"""

_RUN_COLUMN_MIGRATIONS = {
    "delivery_status": "ALTER TABLE scheduled_task_runs ADD COLUMN delivery_status TEXT NOT NULL DEFAULT 'none'",
    "delivery_error": "ALTER TABLE scheduled_task_runs ADD COLUMN delivery_error TEXT",
    "delivered_at": "ALTER TABLE scheduled_task_runs ADD COLUMN delivered_at TEXT",
    "delivery_attempts": "ALTER TABLE scheduled_task_runs ADD COLUMN delivery_attempts INTEGER NOT NULL DEFAULT 0",
}

_TASK_COLUMN_MIGRATIONS = {
    "schedule_type": "ALTER TABLE scheduled_tasks ADD COLUMN schedule_type TEXT NOT NULL DEFAULT 'recurring'",
    "run_at": "ALTER TABLE scheduled_tasks ADD COLUMN run_at TEXT",
    "delete_after_run": "ALTER TABLE scheduled_tasks ADD COLUMN delete_after_run INTEGER NOT NULL DEFAULT 0",
}


def _connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or get_scheduler_db_path()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_DDL)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    task_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(scheduled_tasks)").fetchall()
    }
    for column, sql in _TASK_COLUMN_MIGRATIONS.items():
        if column not in task_columns:
            conn.execute(sql)

    run_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(scheduled_task_runs)").fetchall()
    }
    for column, sql in _RUN_COLUMN_MIGRATIONS.items():
        if column not in run_columns:
            conn.execute(sql)


class SchedulerStore:
    """Thread-safe synchronous store for scheduler data."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or get_scheduler_db_path()
        # Ensure schema exists on construction
        with _connect(self._db_path) as conn:
            conn.commit()

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def save_task(self, task: "ScheduledTask") -> None:  # noqa: F821
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

    def load_task(self, task_id: str) -> "ScheduledTask | None":  # noqa: F821
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return _row_to_task(row) if row else None

    def list_tasks(self, *, enabled_only: bool = False) -> list["ScheduledTask"]:  # noqa: F821
        with _connect(self._db_path) as conn:
            if enabled_only:
                rows = conn.execute(
                    "SELECT * FROM scheduled_tasks WHERE enabled=1 ORDER BY created_at"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM scheduled_tasks ORDER BY created_at"
                ).fetchall()
        return [_row_to_task(r) for r in rows]

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
        now = datetime.now(timezone.utc).isoformat()
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
        now = datetime.now(timezone.utc).isoformat()
        with _connect(self._db_path) as conn:
            conn.execute(
                "UPDATE scheduled_tasks SET enabled=?, updated_at=? WHERE id=?",
                (int(enabled), now, task_id),
            )
            conn.commit()

    def disable_task_after_run(self, task_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with _connect(self._db_path) as conn:
            conn.execute(
                "UPDATE scheduled_tasks SET enabled=0, next_run_at=NULL, updated_at=? WHERE id=?",
                (now, task_id),
            )
            conn.commit()

    def delete_task(self, task_id: str) -> bool:
        with _connect(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM scheduled_tasks WHERE id=?", (task_id,)
            )
            conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def save_run(self, run: "TaskRun") -> None:  # noqa: F821
        from invincat_cli.scheduler.models import TaskRun

        assert isinstance(run, TaskRun)
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO scheduled_task_runs
                    (id, task_id, scheduled_for, started_at, finished_at,
                     status, report_path, error, thread_id, cwd,
                     delivery_status, delivery_error, delivered_at, delivery_attempts)
                VALUES
                    (:id,:task_id,:scheduled_for,:started_at,:finished_at,
                     :status,:report_path,:error,:thread_id,:cwd,
                     :delivery_status,:delivery_error,:delivered_at,:delivery_attempts)
                ON CONFLICT(id) DO UPDATE SET
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at,
                    status=excluded.status,
                    report_path=excluded.report_path,
                    error=excluded.error,
                    thread_id=excluded.thread_id,
                    delivery_status=excluded.delivery_status,
                    delivery_error=excluded.delivery_error,
                    delivered_at=excluded.delivered_at,
                    delivery_attempts=excluded.delivery_attempts
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
                },
            )
            conn.commit()

    def load_run(self, run_id: str) -> "TaskRun | None":  # noqa: F821
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_task_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return _row_to_run(row) if row else None

    def list_runs(self, task_id: str, limit: int = 20) -> list["TaskRun"]:  # noqa: F821
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM scheduled_task_runs
                WHERE task_id=?
                ORDER BY scheduled_for DESC
                LIMIT ?
                """,
                (task_id, limit),
            ).fetchall()
        return [_row_to_run(r) for r in rows]

    def update_run_delivery(
        self,
        run_id: str,
        *,
        status: str,
        error: str | None = None,
        delivered_at: str | None = None,
        attempts_delta: int = 1,
    ) -> None:
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE scheduled_task_runs SET
                    delivery_status=?,
                    delivery_error=?,
                    delivered_at=COALESCE(?, delivered_at),
                    delivery_attempts=delivery_attempts+?
                WHERE id=?
                """,
                (status, error, delivered_at, attempts_delta, run_id),
            )
            conn.commit()

    def reconcile_orphan_runs(
        self,
        cwd: str | None = None,
        *,
        finished_at: str,
        status: str = "failed",
        error: str = "daemon restart",
    ) -> int:
        """Mark every still-running TaskRun as finished.

        Used on daemon startup to clear records left over from a previous
        daemon kill that never got to call ``finish_run``.  Without this the
        runs table accumulates "running" rows that never resolve.

        If ``cwd`` is given, only runs from that working directory are
        reconciled; otherwise all are.  Returns the number of rows updated.
        """
        params: list[Any] = [status, finished_at, error]
        sql = (
            "UPDATE scheduled_task_runs SET "
            "  status=?, finished_at=?, "
            "  error=COALESCE(error, ?) "
            "WHERE status='running' AND finished_at IS NULL"
        )
        if cwd is not None:
            sql += " AND cwd=?"
            params.append(cwd)
        with _connect(self._db_path) as conn:
            cur = conn.execute(sql, tuple(params))
            # Also bring scheduled_tasks.last_status out of 'running' so the
            # list view doesn't show stuck entries.  We don't bump
            # failure_count — restart isn't a real agent failure.
            task_sql = (
                "UPDATE scheduled_tasks SET last_status=? "
                "WHERE last_status='running'"
            )
            task_params: list[Any] = [status]
            if cwd is not None:
                task_sql += " AND cwd=?"
                task_params.append(cwd)
            conn.execute(task_sql, tuple(task_params))
            conn.commit()
            return cur.rowcount


# ------------------------------------------------------------------
# Serialisation helpers
# ------------------------------------------------------------------


def _task_to_row(task: Any) -> dict:
    from invincat_cli.scheduler.models import DeliverySpec, ReportSpec

    delivery = task.delivery
    report = task.report
    return {
        "id": task.id,
        "title": task.title,
        "enabled": int(task.enabled),
        "prompt": task.prompt,
        "cron": task.cron,
        "timezone": task.timezone,
        "cwd": task.cwd,
        "delivery": json.dumps(
            delivery.__dict__ if isinstance(delivery, DeliverySpec) else delivery,
            ensure_ascii=False,
        ),
        "report": json.dumps(
            report.__dict__ if isinstance(report, ReportSpec) else report,
            ensure_ascii=False,
        ),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "next_run_at": task.next_run_at,
        "last_run_at": task.last_run_at,
        "last_status": task.last_status,
        "last_error": task.last_error,
        "run_count": task.run_count,
        "failure_count": task.failure_count,
        "misfire_policy": task.misfire_policy,
        "schedule_type": task.schedule_type,
        "run_at": task.run_at,
        "delete_after_run": int(task.delete_after_run),
        "timeout_seconds": task.timeout_seconds,
    }


def _row_to_task(row: sqlite3.Row) -> "ScheduledTask":  # noqa: F821
    from invincat_cli.scheduler.models import DeliverySpec, ReportSpec, ScheduledTask

    delivery_d = json.loads(row["delivery"] or "{}")
    report_d = json.loads(row["report"] or "{}")
    return ScheduledTask(
        id=row["id"],
        title=row["title"],
        enabled=bool(row["enabled"]),
        prompt=row["prompt"],
        cron=row["cron"],
        timezone=row["timezone"],
        cwd=row["cwd"],
        delivery=DeliverySpec(**delivery_d) if delivery_d else DeliverySpec(),
        report=ReportSpec(**report_d) if report_d else ReportSpec(),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        next_run_at=row["next_run_at"],
        last_run_at=row["last_run_at"],
        last_status=row["last_status"],
        last_error=row["last_error"],
        run_count=row["run_count"],
        failure_count=row["failure_count"],
        misfire_policy=row["misfire_policy"],
        schedule_type=row["schedule_type"],
        run_at=row["run_at"],
        delete_after_run=bool(row["delete_after_run"]),
        timeout_seconds=row["timeout_seconds"],
    )


def _row_to_run(row: sqlite3.Row) -> "TaskRun":  # noqa: F821
    from invincat_cli.scheduler.models import TaskRun

    return TaskRun(
        id=row["id"],
        task_id=row["task_id"],
        scheduled_for=row["scheduled_for"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        report_path=row["report_path"],
        error=row["error"],
        thread_id=row["thread_id"],
        cwd=row["cwd"],
        delivery_status=row["delivery_status"],
        delivery_error=row["delivery_error"],
        delivered_at=row["delivered_at"],
        delivery_attempts=row["delivery_attempts"],
    )
