"""SQLite persistence for scheduled tasks and run history."""

from __future__ import annotations

import errno
import json
import logging
import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from invincat_cli.scheduler.schema import DDL, migrate

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
    db_path = path or get_scheduler_db_path()
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        msg = f"unable to create scheduler database directory for {db_path}: {exc}"
        raise sqlite3.OperationalError(msg) from exc
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.OperationalError as exc:
        msg = f"unable to open scheduler database at {db_path}: {exc}"
        raise sqlite3.OperationalError(msg) from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(DDL)
    migrate(conn)
    return conn


def _pid_is_alive(pid: int | None) -> bool:
    """Return True if *pid* currently exists and can be signalled."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        raise
    return True


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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
    try:
        pid = row["runner_pid"]
    except (IndexError, KeyError):
        pid = None
    if not _pid_is_alive(pid):
        return True

    if task_timeout_seconds <= 0:
        return False

    started_at = _parse_iso_datetime(row["started_at"])
    if started_at is None:
        return False
    stale_after = task_timeout_seconds + _RUNNING_STALE_GRACE_SECONDS
    return (now - started_at).total_seconds() > stale_after


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
            cur = conn.execute(
                "DELETE FROM scheduled_tasks WHERE id=?", (task_id,)
            )
            conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def save_run(self, run: TaskRun) -> None:  # noqa: F821
        from invincat_cli.scheduler.models import TaskRun

        assert isinstance(run, TaskRun)
        with _connect(self._db_path) as conn:
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
                    delivery_attempts=excluded.delivery_attempts,
                    runner_id=excluded.runner_id,
                    runner_kind=excluded.runner_kind,
                    runner_pid=excluded.runner_pid
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
            conn.commit()

    def load_run(self, run_id: str) -> TaskRun | None:  # noqa: F821
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_task_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return _row_to_run(row) if row else None

    def list_runs(self, task_id: str, limit: int = 20) -> list[TaskRun]:  # noqa: F821
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
        """Mark stale still-running TaskRuns as finished.

        Used on runner startup to clear records left over from a previous
        process kill that never got to call ``finish_run``.  Live runs owned by
        another active runner are left untouched.

        If ``cwd`` is given, only runs from that working directory are
        reconciled; otherwise all are.  Returns the number of rows updated.
        """
        select_params: list[Any] = []
        select_sql = (
            """
            SELECT r.*, t.timeout_seconds
            FROM scheduled_task_runs r
            LEFT JOIN scheduled_tasks t ON t.id = r.task_id
            WHERE r.status='running' AND r.finished_at IS NULL
            """
        )
        if cwd is not None:
            select_sql += " AND r.cwd=?"
            select_params.append(cwd)
        with _connect(self._db_path) as conn:
            rows = conn.execute(select_sql, tuple(select_params)).fetchall()
            finished_dt = _parse_iso_datetime(finished_at) or datetime.now(UTC)
            stale_rows = [
                row
                for row in rows
                if _running_row_is_stale(
                    row,
                    task_timeout_seconds=int(row["timeout_seconds"] or 600),
                    now=finished_dt,
                )
            ]
            if not stale_rows:
                conn.commit()
                return 0

            stale_ids = [row["id"] for row in stale_rows]
            task_ids = sorted({row["task_id"] for row in stale_rows})
            run_placeholders = ",".join("?" for _ in stale_ids)
            cur = conn.execute(
                f"""
                UPDATE scheduled_task_runs SET
                    status=?,
                    finished_at=?,
                    error=COALESCE(error, ?)
                WHERE id IN ({run_placeholders})
                    AND status='running'
                    AND finished_at IS NULL
                """,
                (status, finished_at, error, *stale_ids),
            )
            task_placeholders = ",".join("?" for _ in task_ids)
            conn.execute(
                f"""
                UPDATE scheduled_tasks SET last_status=?
                WHERE id IN ({task_placeholders})
                    AND last_status='running'
                    AND NOT EXISTS (
                        SELECT 1 FROM scheduled_task_runs r
                        WHERE r.task_id=scheduled_tasks.id
                            AND r.status='running'
                            AND r.finished_at IS NULL
                    )
                """,
                (status, *task_ids),
            )
            conn.commit()
            return cur.rowcount


class CwdScopedSchedulerStore(SchedulerStore):
    """SchedulerStore view that hides tasks outside one working directory."""

    def __init__(self, cwd: str | Path, db_path: Path | None = None) -> None:
        self._scope_cwd = str(cwd)
        super().__init__(db_path=db_path)

    def list_tasks(
        self,
        *,
        enabled_only: bool = False,
        cwd: str | None = None,
    ) -> list[ScheduledTask]:  # noqa: F821
        if cwd is not None and cwd != self._scope_cwd:
            return []
        return super().list_tasks(enabled_only=enabled_only, cwd=self._scope_cwd)

    def load_task(self, task_id: str) -> ScheduledTask | None:  # noqa: F821
        task = super().load_task(task_id)
        if task is None or task.cwd != self._scope_cwd:
            return None
        return task


class FilteredSchedulerStore(SchedulerStore):
    """SchedulerStore view that excludes matching tasks from runner claims."""

    def __init__(
        self,
        *,
        exclude_task: Callable[[Any], bool],
        db_path: Path | None = None,
    ) -> None:
        self._exclude_task = exclude_task
        super().__init__(db_path=db_path)

    def _is_excluded(self, task: Any) -> bool:
        try:
            return bool(self._exclude_task(task))
        except Exception:
            logger.warning("Scheduler task filter failed", exc_info=True)
            return False

    def list_tasks(
        self,
        *,
        enabled_only: bool = False,
        cwd: str | None = None,
    ) -> list[ScheduledTask]:  # noqa: F821
        return [
            task for task in super().list_tasks(enabled_only=enabled_only, cwd=cwd)
            if not self._is_excluded(task)
        ]

    def try_start_run(self, task_id: str, run: Any, **kwargs: Any) -> bool:
        task = super().load_task(task_id)
        if task is not None and self._is_excluded(task):
            return False
        return super().try_start_run(task_id, run, **kwargs)


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


def _row_to_task(row: sqlite3.Row) -> ScheduledTask:  # noqa: F821
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


def _row_to_run(row: sqlite3.Row) -> TaskRun:  # noqa: F821
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
        runner_id=row["runner_id"],
        runner_kind=row["runner_kind"],
        runner_pid=row["runner_pid"],
    )
