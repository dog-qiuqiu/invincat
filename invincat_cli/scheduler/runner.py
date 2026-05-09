"""Scheduler runner — checks tasks every minute and injects due runs into the TUI."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from invincat_cli.scheduler.models import ScheduledTask
    from invincat_cli.scheduler.store import SchedulerStore

logger = logging.getLogger(__name__)

_MISFIRE_TOLERANCE_SECONDS = 300  # 5 minutes — tasks within tolerance fire immediately
_MISFIRE_MAX_SECONDS = 86400  # 24 hours — older misfires are not recovered


def compute_next_run(cron: str, after: datetime, tz_name: str) -> datetime | None:
    """Return the next fire time for *cron* after *after* in *tz_name*."""
    try:
        from croniter import croniter
        import zoneinfo

        tz = zoneinfo.ZoneInfo(tz_name)
        local_after = after.astimezone(tz)
        it = croniter(cron, local_after)
        return it.get_next(datetime).replace(tzinfo=tz).astimezone(timezone.utc)
    except Exception:
        logger.warning("Failed to compute next_run for cron %r tz %r", cron, tz_name, exc_info=True)
        return None


def _slug(title: str) -> str:
    return re.sub(r"[^\w\-]", "-", title.lower())[:40].strip("-")


def _build_scheduled_prompt(task: "ScheduledTask", scheduled_for: datetime) -> str:
    """Wrap the user prompt in a structured header that prevents recursive task creation."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(task.timezone)
    local_time = scheduled_for.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
    date_str = scheduled_for.astimezone(tz).strftime("%Y-%m-%d")

    from invincat_cli.scheduler.models import ReportSpec

    report: ReportSpec = task.report
    if report.mode == "report":
        filename = report.filename_template.format(
            task_slug=_slug(task.title),
            date=date_str,
        )
        report_path = f"{report.output_dir}/{filename}"
        requirements = (
            f"Requirements:\n"
            f"1. Save the report to: {report_path}\n"
            f"2. The report must be in {report.format} format.\n"
            f"3. After saving, reply with the report path and a brief summary.\n"
            f"4. Do not create new scheduled tasks."
        )
    else:
        requirements = (
            f"Requirements:\n"
            f"1. Execute the task and reply with a concise result for notification.\n"
            f"2. Do not create a report file unless the task explicitly asks for one.\n"
            f"3. Do not create new scheduled tasks."
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
        store: "SchedulerStore",
        *,
        inject_message: Callable[[str, str, str], Awaitable[None]],
        notify: Callable[[str], None],
        is_busy: Callable[[], bool],
    ) -> None:
        self._store = store
        self._inject_message = inject_message
        self._notify = notify
        self._is_busy = is_busy
        self._running_task_ids: set[str] = set()
        self._pending_runs: list[tuple["ScheduledTask", datetime]] = []
        self._timeout_tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Called by Textual set_interval every 60 s
    # ------------------------------------------------------------------

    async def tick(self) -> None:
        """Check for due or missed tasks and enqueue them."""
        now = datetime.now(timezone.utc)
        try:
            tasks = self._store.list_tasks(enabled_only=True)
        except Exception:
            logger.exception("Failed to load tasks during scheduler tick")
            return

        for task in tasks:
            if task.id in self._running_task_ids:
                continue
            await self._evaluate_task(task, now)

        # Drain pending runs if TUI is now idle
        await self._drain_pending(now)

    async def _evaluate_task(self, task: "ScheduledTask", now: datetime) -> None:
        next_run = _parse_dt(task.next_run_at)
        if next_run is None:
            # First-ever tick: compute and save next_run_at
            next_run = compute_next_run(task.cron, now, task.timezone)
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
            # Too old — skip silently and advance next_run
            logger.info("Skipping very old misfire for task %r (%ds ago)", task.title, lag_seconds)
            next_run2 = compute_next_run(task.cron, now, task.timezone)
            self._store.update_task_status(
                task.id,
                last_status="missed",
                next_run_at=next_run2.isoformat() if next_run2 else None,
            )
            return

        was_missed = lag_seconds > _MISFIRE_TOLERANCE_SECONDS
        if was_missed and task.misfire_policy == "skip":
            logger.info("Skipping missed task %r per policy", task.title)
            next_run2 = compute_next_run(task.cron, now, task.timezone)
            self._store.update_task_status(
                task.id,
                last_status="missed",
                next_run_at=next_run2.isoformat() if next_run2 else None,
            )
            return

        if was_missed:
            self._notify(f"Missed scheduled task: {task.title!r} — running now")

        self._pending_runs.append((task, next_run))
        await self._drain_pending(now)

    async def _drain_pending(self, now: datetime) -> None:
        if not self._pending_runs:
            return
        if self._is_busy():
            return
        task, scheduled_for = self._pending_runs.pop(0)
        await self._fire(task, scheduled_for, now)

    async def fire_now(self, task: "ScheduledTask") -> None:
        """Trigger a task to run immediately, bypassing the cron schedule."""
        now = datetime.now(timezone.utc)
        if task.id in self._running_task_ids:
            return
        if self._is_busy():
            self._pending_runs.append((task, now))
        else:
            await self._fire(task, now, now)

    async def _fire(self, task: "ScheduledTask", scheduled_for: datetime, now: datetime) -> None:
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
        )
        self._store.save_run(run)
        next_run = compute_next_run(task.cron, now, task.timezone)
        self._store.update_task_status(
            task.id,
            last_status="running",
            last_run_at=now.isoformat(),
            next_run_at=next_run.isoformat() if next_run else None,
        )
        self._running_task_ids.add(task.id)

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

    async def _timeout_watcher(self, run_id: str, task_id: str, timeout_seconds: int) -> None:
        """Fires finish_run with status='timeout' if the task hasn't finished in time."""
        try:
            await asyncio.sleep(timeout_seconds)
        except asyncio.CancelledError:
            return
        if task_id in self._running_task_ids:
            logger.warning("Scheduled task %r timed out after %ds", task_id, timeout_seconds)
            self._finish_run(
                run_id,
                task_id,
                status="timeout",
                error=f"Timed out after {timeout_seconds}s",
            )

    def finish_run(
        self,
        run_id: str,
        task_id: str,
        *,
        status: str,
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
        status: str,
        report_path: str | None = None,
        error: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        """Internal: update run record and task stats, cancel timeout, release the running lock."""
        # Cancel any pending timeout watcher for this run
        timeout_task = self._timeout_tasks.pop(run_id, None)
        if timeout_task is not None:
            timeout_task.cancel()

        now = datetime.now(timezone.utc).isoformat()
        run = self._store.load_run(run_id)
        if run is not None:
            if run.finished_at is not None:
                # Already completed (e.g. timeout fired before agent finished).
                # Release the lock but do not double-count stats or overwrite status.
                self._running_task_ids.discard(task_id)
                return
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
        self._running_task_ids.discard(task_id)


def _parse_dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
