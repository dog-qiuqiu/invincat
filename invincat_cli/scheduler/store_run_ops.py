"""Run-history operations for SchedulerStore."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from invincat_cli.scheduler.models import TaskRun


class SchedulerRunOpsMixin:
    """Persistence operations for scheduled task run rows."""

    _db_path: Any

    def save_run(self, run: TaskRun) -> None:
        from invincat_cli.scheduler import store as _store
        from invincat_cli.scheduler.models import TaskRun

        assert isinstance(run, TaskRun)
        with _store._connect(self._db_path) as conn:
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

    def load_run(self, run_id: str) -> TaskRun | None:
        from invincat_cli.scheduler import store as _store

        with _store._connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_task_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return _store._row_to_run(row) if row else None

    def list_runs(self, task_id: str, limit: int = 20) -> list[TaskRun]:
        from invincat_cli.scheduler import store as _store

        with _store._connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM scheduled_task_runs
                WHERE task_id=?
                ORDER BY scheduled_for DESC
                LIMIT ?
                """,
                (task_id, limit),
            ).fetchall()
        return [_store._row_to_run(r) for r in rows]

    def update_run_delivery(
        self,
        run_id: str,
        *,
        status: str,
        error: str | None = None,
        delivered_at: str | None = None,
        attempts_delta: int = 1,
    ) -> None:
        from invincat_cli.scheduler import store as _store

        with _store._connect(self._db_path) as conn:
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
        """Mark stale still-running TaskRuns as finished."""
        from invincat_cli.scheduler import store as _store

        select_params: list[Any] = []
        select_sql = """
            SELECT r.*, t.timeout_seconds
            FROM scheduled_task_runs r
            LEFT JOIN scheduled_tasks t ON t.id = r.task_id
            WHERE r.status='running' AND r.finished_at IS NULL
            """
        if cwd is not None:
            select_sql += " AND r.cwd=?"
            select_params.append(cwd)
        with _store._connect(self._db_path) as conn:
            rows = conn.execute(select_sql, tuple(select_params)).fetchall()
            finished_dt = _store._parse_iso_datetime(finished_at) or datetime.now(UTC)
            stale_rows = [
                row
                for row in rows
                if _store._running_row_is_stale(
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
