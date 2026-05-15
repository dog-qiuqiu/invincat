"""Scheduler runner — checks tasks every minute and injects due runs into the TUI."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from invincat_cli.scheduler.models import ScheduledTask, TaskStatus
    from invincat_cli.scheduler.store import SchedulerStore

logger = logging.getLogger(__name__)

_MISFIRE_TOLERANCE_SECONDS = 300  # 5 minutes — tasks within tolerance fire immediately
_MISFIRE_MAX_SECONDS = 86400  # 24 hours — older misfires are not recovered


@dataclass
class _PendingRun:
    task: ScheduledTask
    scheduled_for: datetime
    expected_next_run_at: str | None
    manual: bool = False
    require_enabled: bool = True


def compute_next_run(cron: str, after: datetime, tz_name: str) -> datetime | None:
    """Return the next fire time for *cron* after *after* in *tz_name*."""
    try:
        import zoneinfo

        from croniter import croniter  # type: ignore[import-untyped]

        tz = zoneinfo.ZoneInfo(tz_name)
        local_after = after.astimezone(tz)
        it = croniter(cron, local_after)
        return it.get_next(datetime).replace(tzinfo=tz).astimezone(UTC)
    except Exception:
        logger.warning(
            "Failed to compute next_run for cron %r tz %r", cron, tz_name, exc_info=True
        )
        return None


def task_next_run(task: ScheduledTask, after: datetime) -> datetime | None:
    """Return the next run time for recurring or one-shot tasks."""
    if getattr(task, "schedule_type", "recurring") == "once":
        return _parse_dt(getattr(task, "run_at", None))
    return compute_next_run(task.cron, after, task.timezone)


def _slug(title: str) -> str:
    return re.sub(r"[^\w\-]", "-", title.lower())[:40].strip("-")


def _build_scheduled_prompt(task: ScheduledTask, scheduled_for: datetime) -> str:
    """Wrap the user prompt in a structured header that prevents recursive task creation."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(task.timezone)
    local_time = scheduled_for.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
    date_str = scheduled_for.astimezone(tz).strftime("%Y-%m-%d")

    from invincat_cli.scheduler.models import ReportSpec

    report: ReportSpec = task.report
    if report.mode == "report":
        from invincat_cli.scheduler.delivery import report_display_path

        try:
            report_path = report_display_path(task, date_str)
        except ValueError:
            logger.warning(
                "Invalid scheduled report path for task %r", task.id, exc_info=True
            )
            report_path = (
                f"{report.output_dir}/{_slug(task.title)}-{date_str}.{report.format}"
            )
        requirements = (
            f"Requirements:\n"
            f"1. Save the report to: {report_path}\n"
            f"2. The report must be in {report.format} format.\n"
            f"3. After saving, reply with the report path and a brief summary.\n"
            f"4. Do not create new scheduled tasks."
        )
    else:
        requirements = (
            "Requirements:\n"
            "1. Execute the task and reply with a concise result for notification.\n"
            "2. Do not create a report file unless the task explicitly asks for one.\n"
            "3. Do not create new scheduled tasks."
        )

    return (
        f"[Scheduled task – DO NOT create another scheduled task]\n"
        f"Task: {task.title}\n"
        f"Task ID: {task.id}\n"
        f"Triggered at: {local_time}\n"
        f"Working directory: {task.cwd}\n\n"
        f"Please execute the following scheduled task:\n"
        f"{task.prompt}\n\n"
        f"{requirements}"
    )


class SchedulerRunner:
    """Manages due-task detection and injection into the TUI message queue.

    The runner is started from `DeepAgentsApp._post_paint_init` via
    `set_interval` so it runs on the Textual event loop — no threads needed.

    ``inject_message(task_id, run_id, prompt)`` is called when a task fires.
    ``finish_run(run_id, task_id, status=...)`` must be called by the TUI after
    the agent turn completes so that run counts and statuses are recorded.

    ``_running_task_ids`` prevents the same task from being triggered twice
    while an earlier run is still in progress.
    """

    def __init__(
        self,
        store: SchedulerStore,
        *,
        inject_message: Callable[[str, str, str], Awaitable[None]],
        notify: Callable[[str], None],
        is_busy: Callable[[], bool],
        on_timeout: Callable[[str, str], Awaitable[None]] | None = None,
        cwd: str | None = None,
        runner_kind: str = "tui",
    ) -> None:
        self._store = store
        self._inject_message = inject_message
        self._notify = notify
        self._is_busy = is_busy
        self._on_timeout = on_timeout
        self._cwd = cwd
        self._runner_kind = runner_kind
        self._runner_pid = os.getpid()
        self._runner_id = f"{runner_kind}:{self._runner_pid}:{uuid.uuid4().hex}"
        self._running_task_ids: set[str] = set()
        self._pending_runs: list[_PendingRun] = []
        self._pending_task_ids: set[str] = set()
        self._manual_run_ids: set[str] = set()
        self._timeout_tasks: dict[str, asyncio.Task] = {}
        self._reconcile_stale_runs()

    def _reconcile_stale_runs(self) -> None:
        """Recover stale persisted runs left by dead scheduler processes."""
        try:
            reconciled = self._store.reconcile_orphan_runs(
                self._cwd,
                finished_at=datetime.now(UTC).isoformat(),
                status="failed",
                error=f"{self._runner_kind} startup recovered stale scheduled run",
            )
            if reconciled:
                logger.warning(
                    "Scheduler runner recovered %d stale running run(s)",
                    reconciled,
                )
        except Exception:
            logger.exception("Failed to reconcile stale scheduled runs")

    # ------------------------------------------------------------------
    # Called by Textual set_interval every 60 s
    # ------------------------------------------------------------------

    async def tick(self) -> None:
        """Check for due or missed tasks and enqueue them."""
        now = datetime.now(UTC)
        try:
            tasks = self._store.list_tasks(enabled_only=True, cwd=self._cwd)
        except Exception:
            logger.exception("Failed to load tasks during scheduler tick")
            return

        for task in tasks:
            if task.id in self._running_task_ids or task.id in self._pending_task_ids:
                continue
            await self._evaluate_task(task, now)

        # Drain pending runs if TUI is now idle
        await self._drain_pending(now)

    async def _evaluate_task(self, task: ScheduledTask, now: datetime) -> None:
        next_run = _parse_dt(task.next_run_at)
        if next_run is None:
            # First-ever tick: compute and save next_run_at
            next_run = task_next_run(task, now)
            if next_run:
                self._store.update_task_status(
                    task.id,
                    last_status=task.last_status,
                    next_run_at=next_run.isoformat(),
                )
            return

        if now < next_run:
            return  # not due yet

        # Task is due (or overdue)
        lag_seconds = (now - next_run).total_seconds()

        if lag_seconds > _MISFIRE_MAX_SECONDS:
            # Too old — skip silently.
            logger.info(
                "Skipping very old misfire for task %r (%ds ago)",
                task.title,
                lag_seconds,
            )
            self._mark_task_missed(task, now)
            return

        was_missed = lag_seconds > _MISFIRE_TOLERANCE_SECONDS
        if was_missed and task.misfire_policy == "skip":
            logger.info("Skipping missed task %r per policy", task.title)
            self._mark_task_missed(task, now)
            return

        if was_missed:
            self._notify(f"Missed scheduled task: {task.title!r} — running now")

        self._pending_runs.append(
            _PendingRun(
                task=task,
                scheduled_for=next_run,
                expected_next_run_at=task.next_run_at,
            )
        )
        self._pending_task_ids.add(task.id)
        await self._drain_pending(now)

    def _mark_task_missed(self, task: ScheduledTask, now: datetime) -> None:
        """Persist a missed task and advance or disable it as appropriate."""
        if task.schedule_type == "once":
            self._store.update_task_status(
                task.id, last_status="missed", clear_next_run_at=True
            )
            self._store.set_task_enabled(task.id, False)
            return

        next_run = task_next_run(task, now)
        self._store.update_task_status(
            task.id,
            last_status="missed",
            next_run_at=next_run.isoformat() if next_run else None,
        )

    async def _drain_pending(self, now: datetime) -> None:
        while self._pending_runs and not self._is_busy():
            pending = self._pending_runs.pop(0)
            task = pending.task
            self._pending_task_ids.discard(task.id)
            current_task = self._store.load_task(task.id)
            if current_task is None:
                continue
            if pending.require_enabled and not current_task.enabled:
                continue
            if self._cwd is not None and current_task.cwd != self._cwd:
                continue
            await self._fire(
                current_task,
                pending.scheduled_for,
                now,
                expected_next_run_at=pending.expected_next_run_at,
                require_enabled=pending.require_enabled,
                manual=pending.manual,
            )

    async def drain_pending_now(self) -> None:
        """Attempt to fire queued runs immediately if the TUI is idle."""
        await self._drain_pending(datetime.now(UTC))

    async def fire_now(self, task: ScheduledTask) -> None:
        """Trigger a task to run immediately, bypassing the cron schedule."""
        now = datetime.now(UTC)
        if task.id in self._running_task_ids or task.id in self._pending_task_ids:
            return
        if self._is_busy():
            self._pending_runs.append(
                _PendingRun(
                    task=task,
                    scheduled_for=now,
                    expected_next_run_at=None,
                    manual=True,
                    require_enabled=False,
                )
            )
            self._pending_task_ids.add(task.id)
        else:
            await self._fire(task, now, now, require_enabled=False, manual=True)

    async def _fire(
        self,
        task: ScheduledTask,
        scheduled_for: datetime,
        now: datetime,
        *,
        expected_next_run_at: str | None = None,
        require_enabled: bool = True,
        manual: bool = False,
    ) -> None:
        from invincat_cli.scheduler.models import TaskRun

        run_id = str(uuid.uuid4())
        run = TaskRun(
            id=run_id,
            task_id=task.id,
            scheduled_for=scheduled_for.isoformat(),
            started_at=now.isoformat(),
            finished_at=None,
            status="running",
            report_path=None,
            error=None,
            thread_id=None,
            cwd=task.cwd,
            runner_id=self._runner_id,
            runner_kind=self._runner_kind,
            runner_pid=self._runner_pid,
        )
        is_once = task.schedule_type == "once"
        if manual:
            next_run = _parse_dt(task.next_run_at)
        else:
            next_run = (
                None if is_once else compute_next_run(task.cron, now, task.timezone)
            )
        claimed = self._store.try_start_run(
            task.id,
            run,
            expected_next_run_at=expected_next_run_at,
            next_run_at=next_run.isoformat() if next_run else None,
            clear_next_run_at=is_once and not manual,
            require_enabled=require_enabled,
        )
        if not claimed:
            logger.info(
                "Scheduled task %r was already claimed or is no longer runnable",
                task.id,
            )
            return
        self._running_task_ids.add(task.id)
        if manual:
            self._manual_run_ids.add(run_id)

        prompt = _build_scheduled_prompt(task, scheduled_for)
        try:
            await self._inject_message(task.id, run_id, prompt)
            # Start timeout watcher — finish_run() will cancel it on completion.
            # Use None-coalesce only; explicit 0 means "no timeout".
            raw_timeout = getattr(task, "timeout_seconds", None)
            timeout_secs = raw_timeout if raw_timeout is not None else 600
            if timeout_secs > 0:
                self._timeout_tasks[run_id] = asyncio.ensure_future(
                    self._timeout_watcher(run_id, task.id, timeout_secs)
                )
        except Exception:
            logger.exception("Failed to inject scheduled task %r", task.id)
            self._finish_run(run_id, task.id, status="failed", error="inject failed")

    async def _timeout_watcher(
        self, run_id: str, task_id: str, timeout_seconds: int
    ) -> None:
        """Fires finish_run with status='timeout' if the task hasn't finished in time."""
        try:
            await asyncio.sleep(timeout_seconds)
        except asyncio.CancelledError:
            return
        if task_id in self._running_task_ids:
            logger.warning(
                "Scheduled task %r timed out after %ds", task_id, timeout_seconds
            )
            # Remove ourselves from the registry BEFORE calling _finish_run so that
            # _finish_run does not cancel this coroutine from within itself — which
            # would inject CancelledError at the next await and prevent _on_timeout
            # from ever running.
            self._timeout_tasks.pop(run_id, None)
            self._finish_run(
                run_id,
                task_id,
                status="timeout",
                error=f"Timed out after {timeout_seconds}s",
            )
            if self._on_timeout is not None:
                await self._on_timeout(run_id, task_id)

    def finish_run(
        self,
        run_id: str,
        task_id: str,
        *,
        status: TaskStatus,
        report_path: str | None = None,
        error: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        """Called by the TUI after a scheduled agent turn completes."""
        self._finish_run(
            run_id,
            task_id,
            status=status,
            report_path=report_path,
            error=error,
            thread_id=thread_id,
        )

    def _finish_run(
        self,
        run_id: str,
        task_id: str,
        *,
        status: TaskStatus,
        report_path: str | None = None,
        error: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        """Internal: update run record and task stats, cancel timeout, release the running lock."""
        # Cancel any pending timeout watcher for this run
        timeout_task = self._timeout_tasks.pop(run_id, None)
        if timeout_task is not None:
            timeout_task.cancel()

        now = datetime.now(UTC).isoformat()
        run = self._store.load_run(run_id)
        if run is None:
            # Run record missing — release lock without touching stats to avoid
            # corrupting counters based on a state we cannot verify.
            logger.warning(
                "Run %r not found in store; releasing lock without updating stats",
                run_id,
            )
            self._running_task_ids.discard(task_id)
            self._manual_run_ids.discard(run_id)
            return
        if run.finished_at is not None:
            # Already completed (e.g. timeout fired before agent finished).
            # Release the lock but do not double-count stats or overwrite status.
            self._running_task_ids.discard(task_id)
            self._manual_run_ids.discard(run_id)
            return
        manual = run_id in self._manual_run_ids
        run.finished_at = now
        run.status = status
        run.report_path = report_path
        run.error = error
        run.thread_id = thread_id
        self._store.save_run(run)

        self._store.update_task_status(
            task_id,
            last_status=status,
            last_error=error,
            run_count_delta=1,
            failure_count_delta=1 if status in ("failed", "timeout") else 0,
        )
        task = self._store.load_task(task_id)
        if task is not None and task.schedule_type == "once" and not manual:
            if task.delete_after_run:
                self._store.delete_task(task_id)
            else:
                self._store.disable_task_after_run(task_id)
        self._running_task_ids.discard(task_id)
        self._manual_run_ids.discard(run_id)


def _parse_dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None
