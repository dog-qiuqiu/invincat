"""Unit tests for the scheduler subsystem."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from invincat_cli.scheduler.delivery import (
    is_wecom_deliverable_task,
    report_display_path,
    resolve_report_path,
    save_fallback_report,
)
from invincat_cli.scheduler.models import (
    DeliverySpec,
    ReportSpec,
    ScheduledTask,
    TaskRun,
)
from invincat_cli.scheduler.parser import describe_schedule, parse_schedule
from invincat_cli.scheduler.payloads import (
    apply_schedule_update_payload,
    build_schedule_create_payload_result,
    format_schedule_list_item,
)
from invincat_cli.scheduler.runner import (
    SchedulerRunner,
    _build_scheduled_prompt,
    compute_next_run,
    task_next_run,
)
from invincat_cli.scheduler.store import (
    CwdScopedSchedulerStore,
    FilteredSchedulerStore,
    SchedulerStore,
)
from invincat_cli.scheduler.tool import (
    SCHEDULE_CANCEL_TYPE,
    SCHEDULE_CREATE_TYPE,
    SCHEDULE_LIST_TYPE,
    ScheduleMiddleware,
    parse_once_at,
    parse_schedule_tool_result,
    validate_schedule_create_options,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    *,
    task_id: str = "task-1",
    title: str = "Test Task",
    cron: str = "0 8 * * *",
    tz: str = "Asia/Shanghai",
    enabled: bool = True,
    next_run_at: str | None = None,
    misfire_policy: str = "run_once",
    cwd: str = "/tmp",
) -> ScheduledTask:
    now = datetime.now(UTC).isoformat()
    return ScheduledTask(
        id=task_id,
        title=title,
        enabled=enabled,
        prompt="Do something",
        cron=cron,
        timezone=tz,
        cwd=cwd,
        delivery=DeliverySpec(),
        report=ReportSpec(),
        created_at=now,
        updated_at=now,
        next_run_at=next_run_at,
        last_run_at=None,
        last_status="never",
        last_error=None,
        run_count=0,
        failure_count=0,
        misfire_policy=misfire_policy,
    )


def _make_store(tmp_path: Path) -> SchedulerStore:
    return SchedulerStore(db_path=tmp_path / "scheduler.db")


# ---------------------------------------------------------------------------
# parser tests
# ---------------------------------------------------------------------------


def test_daily_parses_to_cron() -> None:
    assert parse_schedule("daily 08:00") == "0 8 * * *"


def test_daily_default_time() -> None:
    assert parse_schedule("daily") == "0 8 * * *"


def test_weekly_mon() -> None:
    assert parse_schedule("weekly mon 09:30") == "30 9 * * 1"


def test_monthly_first() -> None:
    assert parse_schedule("monthly 1 08:00") == "0 8 1 * *"


def test_interval_6h() -> None:
    assert parse_schedule("interval 6h") == "0 */6 * * *"


def test_interval_30m() -> None:
    assert parse_schedule("interval 30m") == "*/30 * * * *"


def test_cron_keyword() -> None:
    assert parse_schedule("cron 0 8 * * *") == "0 8 * * *"


def test_bare_cron() -> None:
    assert parse_schedule("0 8 * * *") == "0 8 * * *"


def test_invalid_schedule_raises() -> None:
    with pytest.raises(ValueError):
        parse_schedule("every monday at noon")


def test_schedule_rejects_extra_arguments() -> None:
    with pytest.raises(ValueError):
        parse_schedule("daily 08:00 extra")
    with pytest.raises(ValueError):
        parse_schedule("weekly mon 08:00 extra")
    with pytest.raises(ValueError):
        parse_schedule("monthly 1 08:00 extra")


def test_monthly_dom_over_31_raises() -> None:
    with pytest.raises(ValueError):
        parse_schedule("monthly 32 08:00")


def test_monthly_dom_29_allowed() -> None:
    assert parse_schedule("monthly 29 08:00") == "0 8 29 * *"


def test_describe_daily() -> None:
    assert describe_schedule("0 8 * * *") == "daily 08:00"


def test_describe_interval_hours() -> None:
    assert describe_schedule("0 */6 * * *") == "every 6 hours"


def test_describe_interval_minutes() -> None:
    assert describe_schedule("*/30 * * * *") == "every 30 minutes"


def test_describe_weekly() -> None:
    result = describe_schedule("0 9 * * 1")
    assert "mon" in result or "09:00" in result


# ---------------------------------------------------------------------------
# runner: compute_next_run
# ---------------------------------------------------------------------------


def test_next_run_future_when_before_fire_time() -> None:
    """If now is 07:00 Shanghai, next run at 08:00 same day."""
    import zoneinfo

    tz = zoneinfo.ZoneInfo("Asia/Shanghai")
    # 07:00 Shanghai
    now_local = datetime(2026, 5, 8, 7, 0, tzinfo=tz)
    now_utc = now_local.astimezone(UTC)
    nxt = compute_next_run("0 8 * * *", now_utc, "Asia/Shanghai")
    assert nxt is not None
    nxt_local = nxt.astimezone(tz)
    assert nxt_local.hour == 8
    assert nxt_local.date() == now_local.date()


def test_next_run_tomorrow_when_after_fire_time() -> None:
    """If now is 09:00 Shanghai, next run at 08:00 tomorrow."""
    import zoneinfo

    tz = zoneinfo.ZoneInfo("Asia/Shanghai")
    now_local = datetime(2026, 5, 8, 9, 0, tzinfo=tz)
    now_utc = now_local.astimezone(UTC)
    nxt = compute_next_run("0 8 * * *", now_utc, "Asia/Shanghai")
    assert nxt is not None
    nxt_local = nxt.astimezone(tz)
    assert nxt_local.hour == 8
    assert nxt_local.date() > now_local.date()


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


def test_store_creates_parent_directory_for_explicit_db_path(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "scheduler.db"

    store = SchedulerStore(db_path=db_path)

    assert db_path.exists()
    store.list_tasks()


def test_store_save_and_load(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    task = _make_task()
    store.save_task(task)
    loaded = store.load_task("task-1")
    assert loaded is not None
    assert loaded.title == "Test Task"
    assert loaded.cron == "0 8 * * *"


def test_build_schedule_create_payload_result(tmp_path: Path) -> None:
    now = datetime(2026, 5, 13, 0, 0, tzinfo=UTC)

    result = build_schedule_create_payload_result(
        {
            "type": SCHEDULE_CREATE_TYPE,
            "task_id": "task-1",
            "title": "Daily Report",
            "cron": "0 8 * * *",
            "timezone": "Asia/Shanghai",
            "prompt": "summarize",
            "output_mode": "report",
            "report_format": "markdown",
        },
        cwd=tmp_path,
        now=now,
    )

    assert result.task.id == "task-1"
    assert result.task.cwd == str(tmp_path)
    assert result.task.report.filename_template == "daily-report-{date}.md"
    assert result.report_path_display == "reports/daily-report-{date}.md"
    assert result.schedule_description == "daily 08:00"


def test_format_schedule_list_item_uses_payload_display_time() -> None:
    line = format_schedule_list_item(
        {
            "id": "abcdef123456",
            "title": "Daily Report",
            "enabled": True,
            "cron": "0 8 * * *",
            "timezone": "Asia/Shanghai",
            "schedule_type": "recurring",
            "next_run_display": "2026-05-14 08:00 +08:00",
        }
    )

    assert "✓ Daily Report" in line
    assert "daily 08:00" in line
    assert "2026-05-14 08:00 +08:00" in line
    assert "[id: abcdef12]" in line


def test_apply_schedule_update_payload_recomputes_next_run() -> None:
    now = datetime(2026, 5, 13, 0, 0, tzinfo=UTC)
    task = _make_task(cron="0 8 * * *", tz="Asia/Shanghai")

    updated = apply_schedule_update_payload(
        task,
        {
            "title": "Updated",
            "cron": "30 9 * * *",
            "timezone": "Asia/Shanghai",
            "enabled": False,
        },
        now=now,
    )

    assert updated is task
    assert task.title == "Updated"
    assert task.cron == "30 9 * * *"
    assert task.enabled is False
    assert task.next_run_at == "2026-05-13T01:30:00+00:00"
    assert task.updated_at == now.isoformat()


def test_apply_schedule_update_payload_rejects_invalid_timezone() -> None:
    task = _make_task()

    with pytest.raises(ValueError):
        apply_schedule_update_payload(task, {"timezone": "Not/AZone"})


def test_store_migrates_timeout_seconds_column(tmp_path: Path) -> None:
    db = tmp_path / "scheduler.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(
            """
            CREATE TABLE scheduled_tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                prompt TEXT NOT NULL,
                cron TEXT NOT NULL,
                timezone TEXT NOT NULL DEFAULT 'UTC',
                cwd TEXT NOT NULL,
                delivery TEXT NOT NULL DEFAULT '{}',
                report TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                next_run_at TEXT,
                last_run_at TEXT,
                last_status TEXT NOT NULL DEFAULT 'never',
                last_error TEXT,
                run_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                misfire_policy TEXT NOT NULL DEFAULT 'run_once',
                schedule_type TEXT NOT NULL DEFAULT 'recurring',
                run_at TEXT,
                delete_after_run INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE scheduled_task_runs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                report_path TEXT,
                error TEXT,
                thread_id TEXT,
                cwd TEXT NOT NULL
            );
            """
        )

    store = SchedulerStore(db_path=db)
    task = _make_task()
    store.save_task(task)
    loaded = store.load_task(task.id)

    assert loaded is not None
    assert loaded.timeout_seconds == 600


def test_store_list_tasks(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.save_task(_make_task(task_id="a"))
    store.save_task(_make_task(task_id="b"))
    tasks = store.list_tasks()
    assert len(tasks) == 2


def test_store_enabled_only(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.save_task(_make_task(task_id="a", enabled=True))
    store.save_task(_make_task(task_id="b", enabled=False))
    tasks = store.list_tasks(enabled_only=True)
    assert len(tasks) == 1
    assert tasks[0].id == "a"


def test_store_delete(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.save_task(_make_task())
    assert store.delete_task("task-1")
    assert store.load_task("task-1") is None


def test_store_set_enabled(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.save_task(_make_task(enabled=True))
    store.set_task_enabled("task-1", False)
    loaded = store.load_task("task-1")
    assert loaded is not None
    assert not loaded.enabled


def test_store_update_status(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.save_task(_make_task())
    store.update_task_status(
        "task-1",
        last_status="success",
        run_count_delta=1,
    )
    loaded = store.load_task("task-1")
    assert loaded is not None
    assert loaded.last_status == "success"
    assert loaded.run_count == 1


def test_store_save_and_list_runs(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.save_task(_make_task())
    now = datetime.now(UTC).isoformat()
    run = TaskRun(
        id="run-1",
        task_id="task-1",
        scheduled_for=now,
        started_at=now,
        finished_at=None,
        status="running",
        report_path=None,
        error=None,
        thread_id=None,
        cwd="/tmp",
    )
    store.save_run(run)
    runs = store.list_runs("task-1")
    assert len(runs) == 1
    assert runs[0].status == "running"
    assert runs[0].delivery_status == "none"


def test_store_updates_run_delivery_status(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.save_task(_make_task())
    now = datetime.now(UTC).isoformat()
    store.save_run(
        TaskRun(
            id="run-1",
            task_id="task-1",
            scheduled_for=now,
            started_at=now,
            finished_at=None,
            status="running",
            report_path=None,
            error=None,
            thread_id=None,
            cwd="/tmp",
        )
    )

    delivered_at = datetime.now(UTC).isoformat()
    store.update_run_delivery(
        "run-1",
        status="success",
        delivered_at=delivered_at,
    )

    loaded = store.load_run("run-1")
    assert loaded is not None
    assert loaded.delivery_status == "success"
    assert loaded.delivered_at == delivered_at
    assert loaded.delivery_attempts == 1


def test_store_persists_after_reload(tmp_path: Path) -> None:
    """Task survives a store re-instantiation (simulates TUI restart)."""
    db = tmp_path / "scheduler.db"
    store1 = SchedulerStore(db_path=db)
    store1.save_task(_make_task(title="Persistent"))
    store2 = SchedulerStore(db_path=db)
    loaded = store2.load_task("task-1")
    assert loaded is not None
    assert loaded.title == "Persistent"


def test_reconcile_orphan_runs_marks_running_runs_failed(tmp_path: Path) -> None:
    """Daemon kill leaves runs stuck at status='running'; reconcile cleans them up."""
    store = _make_store(tmp_path)
    store.save_task(_make_task())
    now = datetime.now(UTC).isoformat()
    store.save_run(
        TaskRun(
            id="run-orphan",
            task_id="task-1",
            scheduled_for=now,
            started_at=now,
            finished_at=None,
            status="running",
            report_path=None,
            error=None,
            thread_id=None,
            cwd="/tmp",
            runner_pid=999999999,
        )
    )

    finished_at = datetime.now(UTC).isoformat()
    count = store.reconcile_orphan_runs("/tmp", finished_at=finished_at)
    assert count == 1

    loaded = store.load_run("run-orphan")
    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.finished_at == finished_at
    assert loaded.error == "daemon restart"


def test_reconcile_orphan_runs_filters_by_cwd(tmp_path: Path) -> None:
    """Only runs from the current daemon's cwd are reconciled."""
    store = _make_store(tmp_path)
    store.save_task(_make_task())
    now = datetime.now(UTC).isoformat()
    store.save_run(
        TaskRun(
            id="run-mine",
            task_id="task-1",
            scheduled_for=now,
            started_at=now,
            finished_at=None,
            status="running",
            report_path=None,
            error=None,
            thread_id=None,
            cwd="/tmp/project-a",
            runner_pid=999999999,
        )
    )
    store.save_run(
        TaskRun(
            id="run-other",
            task_id="task-1",
            scheduled_for=now,
            started_at=now,
            finished_at=None,
            status="running",
            report_path=None,
            error=None,
            thread_id=None,
            cwd="/tmp/project-b",
            runner_pid=999999999,
        )
    )

    count = store.reconcile_orphan_runs(
        "/tmp/project-a",
        finished_at=datetime.now(UTC).isoformat(),
    )
    assert count == 1
    assert store.load_run("run-mine").status == "failed"
    assert store.load_run("run-other").status == "running"


def test_reconcile_orphan_runs_skips_already_finished(tmp_path: Path) -> None:
    """Runs that finished cleanly are not touched."""
    store = _make_store(tmp_path)
    store.save_task(_make_task())
    now = datetime.now(UTC).isoformat()
    store.save_run(
        TaskRun(
            id="run-done",
            task_id="task-1",
            scheduled_for=now,
            started_at=now,
            finished_at=now,
            status="success",
            report_path=None,
            error=None,
            thread_id=None,
            cwd="/tmp",
        )
    )

    count = store.reconcile_orphan_runs(
        "/tmp",
        finished_at=datetime.now(UTC).isoformat(),
    )
    assert count == 0
    assert store.load_run("run-done").status == "success"


def test_reconcile_orphan_runs_skips_live_runner(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.save_task(_make_task())
    now = datetime.now(UTC).isoformat()
    store.save_run(
        TaskRun(
            id="run-live",
            task_id="task-1",
            scheduled_for=now,
            started_at=now,
            finished_at=None,
            status="running",
            report_path=None,
            error=None,
            thread_id=None,
            cwd="/tmp",
            runner_id="tui-live",
            runner_kind="tui",
            runner_pid=os.getpid(),
        )
    )

    count = store.reconcile_orphan_runs(
        "/tmp",
        finished_at=datetime.now(UTC).isoformat(),
    )

    loaded = store.load_run("run-live")
    assert count == 0
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.finished_at is None


def test_runner_startup_recovers_stale_running_row(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    now = datetime.now(UTC).isoformat()
    store.save_task(_make_task())
    store.save_run(
        TaskRun(
            id="run-stale",
            task_id="task-1",
            scheduled_for=now,
            started_at=now,
            finished_at=None,
            status="running",
            report_path=None,
            error=None,
            thread_id=None,
            cwd="/tmp",
            runner_id="dead-runner",
            runner_kind="tui",
            runner_pid=999999999,
        )
    )

    SchedulerRunner(
        store,
        inject_message=MagicMock(),
        notify=MagicMock(),
        is_busy=lambda: False,
        cwd="/tmp",
    )

    loaded = store.load_run("run-stale")
    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.finished_at is not None


def test_store_preserves_one_shot_fields(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    run_at = datetime.now(UTC).isoformat()
    task = _make_task()
    task.schedule_type = "once"
    task.run_at = run_at
    task.delete_after_run = True

    store.save_task(task)

    loaded = store.load_task(task.id)
    assert loaded is not None
    assert loaded.schedule_type == "once"
    assert loaded.run_at == run_at
    assert loaded.delete_after_run is True


# ---------------------------------------------------------------------------
# runner: misfire policy
# ---------------------------------------------------------------------------


def test_runner_skips_task_when_policy_skip(tmp_path: Path) -> None:
    """run_once=skip: a missed task is not queued."""
    store = _make_store(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    task = _make_task(next_run_at=past, misfire_policy="skip")
    store.save_task(task)

    injected: list[str] = []

    async def inject(task_id: str, run_id: str, prompt: str) -> None:
        injected.append(task_id)

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: False,
    )
    asyncio.run(runner.tick())
    assert len(injected) == 0


def test_runner_runs_once_for_missed_run_once(tmp_path: Path) -> None:
    """run_once: a recently-missed task fires exactly once."""
    store = _make_store(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    task = _make_task(next_run_at=past, misfire_policy="run_once")
    store.save_task(task)

    injected: list[str] = []

    async def inject(task_id: str, run_id: str, prompt: str) -> None:
        injected.append(task_id)

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: False,
    )
    asyncio.run(runner.tick())
    assert len(injected) == 1
    assert injected[0] == "task-1"


def test_runner_does_not_double_trigger_running_task(tmp_path: Path) -> None:
    """A task already in _running_task_ids must not be re-triggered."""
    store = _make_store(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    task = _make_task(next_run_at=past)
    store.save_task(task)

    injected: list[str] = []

    async def inject(task_id: str, run_id: str, prompt: str) -> None:
        injected.append(task_id)

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: False,
    )
    runner._running_task_ids.add("task-1")
    asyncio.run(runner.tick())
    assert len(injected) == 0


def test_runner_queues_when_busy(tmp_path: Path) -> None:
    """When is_busy() returns True, the task is put into _pending_runs."""
    store = _make_store(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    task = _make_task(next_run_at=past)
    store.save_task(task)

    injected: list[str] = []

    async def inject(task_id: str, run_id: str, prompt: str) -> None:
        injected.append(task_id)

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: True,
    )
    asyncio.run(runner.tick())
    assert len(injected) == 0
    assert len(runner._pending_runs) == 1


def test_runner_dedupes_pending_runs_while_busy(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    task = _make_task(next_run_at=past)
    store.save_task(task)

    async def inject(task_id: str, run_id: str, prompt: str) -> None:
        raise AssertionError("should not fire while busy")

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: True,
    )
    asyncio.run(runner.tick())
    asyncio.run(runner.tick())

    assert len(runner._pending_runs) == 1
    assert runner._pending_task_ids == {"task-1"}


def test_runner_drain_pending_now_fires_when_idle(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    task = _make_task(next_run_at=past)
    store.save_task(task)
    busy = True
    fired: list[str] = []

    async def inject(task_id: str, run_id: str, prompt: str) -> None:
        fired.append(task_id)

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: busy,
    )
    asyncio.run(runner.tick())
    assert len(fired) == 0

    busy = False
    asyncio.run(runner.drain_pending_now())

    assert fired == ["task-1"]
    assert not runner._pending_runs
    assert not runner._pending_task_ids


def test_runner_drain_pending_skips_disabled_task(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    task = _make_task(next_run_at=past)
    store.save_task(task)
    busy = True
    fired: list[str] = []

    async def inject(task_id: str, run_id: str, prompt: str) -> None:
        fired.append(task_id)

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: busy,
    )
    asyncio.run(runner.tick())
    store.set_task_enabled("task-1", False)

    busy = False
    asyncio.run(runner.drain_pending_now())

    assert fired == []
    assert not runner._pending_runs
    assert not runner._pending_task_ids


def test_one_shot_task_next_run_uses_run_at() -> None:
    run_at = datetime.now(UTC).replace(microsecond=0)
    task = _make_task()
    task.schedule_type = "once"
    task.run_at = run_at.isoformat()

    assert task_next_run(task, datetime.now(UTC)) == run_at


def test_one_shot_task_is_disabled_after_finish(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    task = _make_task(next_run_at=past)
    task.schedule_type = "once"
    task.run_at = past
    store.save_task(task)

    fired: list[tuple[str, str]] = []

    async def inject(task_id: str, run_id: str, prompt: str) -> None:
        fired.append((task_id, run_id))

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: False,
    )
    asyncio.run(runner.tick())
    task_id, run_id = fired[0]
    runner.finish_run(run_id, task_id, status="success")

    loaded = store.load_task(task_id)
    assert loaded is not None
    assert loaded.enabled is False
    assert loaded.next_run_at is None


def test_one_shot_task_can_delete_after_finish(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    task = _make_task(next_run_at=past)
    task.schedule_type = "once"
    task.run_at = past
    task.delete_after_run = True
    store.save_task(task)

    fired: list[tuple[str, str]] = []

    async def inject(task_id: str, run_id: str, prompt: str) -> None:
        fired.append((task_id, run_id))

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: False,
    )
    asyncio.run(runner.tick())
    task_id, run_id = fired[0]
    runner.finish_run(run_id, task_id, status="success")

    assert store.load_task(task_id) is None


def test_runner_disabled_task_not_triggered(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    task = _make_task(next_run_at=past, enabled=False)
    store.save_task(task)

    injected: list[str] = []

    async def inject(task_id: str, run_id: str, prompt: str) -> None:
        injected.append(task_id)

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: False,
    )
    asyncio.run(runner.tick())
    assert len(injected) == 0


# ---------------------------------------------------------------------------
# tool: parse_schedule_tool_result
# ---------------------------------------------------------------------------


def test_parse_schedule_create_payload() -> None:
    payload = json.dumps(
        {
            "type": SCHEDULE_CREATE_TYPE,
            "title": "Daily report",
            "cron": "0 8 * * *",
            "tool_call_id": "abc",
        }
    )
    result = parse_schedule_tool_result(payload)
    assert result is not None
    assert result["type"] == SCHEDULE_CREATE_TYPE


def test_parse_schedule_tool_returns_none_for_garbage() -> None:
    assert parse_schedule_tool_result("not json") is None
    assert parse_schedule_tool_result("{}") is None
    assert parse_schedule_tool_result(None) is None


def test_parse_schedule_tool_returns_none_for_unknown_type() -> None:
    payload = json.dumps({"type": "something_else"})
    assert parse_schedule_tool_result(payload) is None


def test_scheduled_prompt_defaults_to_message_mode() -> None:
    task = _make_task()
    prompt = _build_scheduled_prompt(task, datetime.now(UTC))

    assert "reply with a concise result" in prompt
    assert "Save the report to" not in prompt


def test_scheduled_prompt_report_mode_requires_report_file() -> None:
    task = _make_task()
    task.report = ReportSpec(mode="report")
    prompt = _build_scheduled_prompt(task, datetime.now(UTC))

    assert "Save the report to: reports/test-task-" in prompt
    assert "brief summary" in prompt


def test_store_preserves_wecom_delivery_channel(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    task = _make_task()
    task.delivery = DeliverySpec(channels=[{"type": "wecom", "chatid": "chat-1"}])

    store.save_task(task)

    loaded = store.load_task(task.id)
    assert loaded is not None
    assert loaded.delivery.channels == [{"type": "wecom", "chatid": "chat-1"}]


# ---------------------------------------------------------------------------
# tool: ScheduleMiddleware create tool
# ---------------------------------------------------------------------------


def _invoke_tool(tool_obj, args: dict, tool_call_id: str = "test-id") -> str:
    """Invoke a LangChain tool that requires InjectedToolCallId."""
    result = tool_obj.invoke(
        {
            "args": args,
            "name": tool_obj.name,
            "type": "tool_call",
            "id": tool_call_id,
        }
    )
    # tool.invoke with a full ToolCall dict returns a ToolMessage; extract content.
    if hasattr(result, "content"):
        return str(result.content)
    return str(result)


def test_schedule_middleware_create_tool_returns_valid_json(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    mw = ScheduleMiddleware(store=store)
    create_tool = next(t for t in mw.tools if t.name == "create_scheduled_task")
    result = _invoke_tool(
        create_tool,
        {
            "title": "Daily analysis",
            "schedule": "daily 08:00",
            "prompt": "Analyse the project",
        },
    )
    data = json.loads(result)
    assert data["type"] == SCHEDULE_CREATE_TYPE
    assert data["cron"] == "0 8 * * *"
    assert data["title"] == "Daily analysis"
    assert data["output_mode"] == "message"
    assert data["timeout_seconds"] == 600


def test_schedule_middleware_create_tool_accepts_timeout(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    mw = ScheduleMiddleware(store=store)
    create_tool = next(t for t in mw.tools if t.name == "create_scheduled_task")
    result = _invoke_tool(
        create_tool,
        {
            "title": "Daily analysis",
            "schedule": "daily 08:00",
            "prompt": "Analyse the project",
            "timeout_seconds": 30,
        },
    )
    data = json.loads(result)
    assert data["timeout_seconds"] == 30


def test_schedule_middleware_create_tool_rejects_invalid_options(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    mw = ScheduleMiddleware(store=store)
    create_tool = next(t for t in mw.tools if t.name == "create_scheduled_task")

    result = _invoke_tool(
        create_tool,
        {
            "title": "Bad",
            "schedule": "daily 08:00",
            "prompt": "test",
            "misfire_policy": "later",
        },
    )
    assert "misfire_policy" in json.loads(result)["error"]

    result = _invoke_tool(
        create_tool,
        {
            "title": "Bad",
            "schedule": "daily 08:00",
            "prompt": "test",
            "report_format": "pdf",
        },
    )
    assert "report_format" in json.loads(result)["error"]

    result = _invoke_tool(
        create_tool,
        {
            "title": "Bad",
            "schedule": "daily 08:00",
            "prompt": "test",
            "timeout_seconds": -1,
        },
    )
    assert "timeout_seconds" in json.loads(result)["error"]


def test_validate_schedule_create_options_rejects_malformed_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        validate_schedule_create_options(
            output_mode="message",
            report_format="markdown",
            misfire_policy="run_once",
            timeout_seconds="not-an-int",
        )


def test_schedule_middleware_create_tool_rejects_invalid_timezone(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    mw = ScheduleMiddleware(store=store)
    create_tool = next(t for t in mw.tools if t.name == "create_scheduled_task")

    result = _invoke_tool(
        create_tool,
        {
            "title": "Bad timezone",
            "schedule": "daily 08:00",
            "prompt": "test",
            "timezone": "Bad/Zone",
        },
    )

    assert "Invalid timezone" in json.loads(result)["error"]


def test_parse_once_at_rejects_invalid_timezone_even_with_offset() -> None:
    with pytest.raises(ValueError, match="Invalid timezone"):
        parse_once_at("2026-05-10T20:00:00+08:00", "Bad/Zone")


def test_parse_once_at_rejects_invalid_timezone_for_naive_time() -> None:
    with pytest.raises(ValueError, match="Invalid timezone"):
        parse_once_at("2026-05-10T20:00:00", "Bad/Zone")


def test_schedule_middleware_create_tool_accepts_report_mode(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    mw = ScheduleMiddleware(store=store)
    create_tool = next(t for t in mw.tools if t.name == "create_scheduled_task")
    result = _invoke_tool(
        create_tool,
        {
            "title": "Daily analysis",
            "schedule": "daily 08:00",
            "prompt": "Analyse the project",
            "output_mode": "report",
        },
    )
    data = json.loads(result)
    assert data["type"] == SCHEDULE_CREATE_TYPE
    assert data["output_mode"] == "report"


def test_schedule_middleware_create_tool_accepts_once_at(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    mw = ScheduleMiddleware(store=store)
    create_tool = next(t for t in mw.tools if t.name == "create_scheduled_task")
    result = _invoke_tool(
        create_tool,
        {
            "title": "One shot",
            "schedule": "once",
            "prompt": "Remind me",
            "once_at": "2026-05-10T20:00:00+08:00",
            "delete_after_run": True,
        },
    )
    data = json.loads(result)
    assert data["type"] == SCHEDULE_CREATE_TYPE
    assert data["schedule_type"] == "once"
    assert data["run_at"] == "2026-05-10T12:00:00+00:00"
    assert data["delete_after_run"] is True


def test_schedule_middleware_rejects_once_at_with_recurring_schedule(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    mw = ScheduleMiddleware(store=store)
    create_tool = next(t for t in mw.tools if t.name == "create_scheduled_task")
    result = _invoke_tool(
        create_tool,
        {
            "title": "Conflicting",
            "schedule": "daily 00:00",
            "prompt": "Run daily",
            "once_at": "2026-05-17T22:09:00+08:00",
        },
    )
    data = json.loads(result)
    assert "error" in data
    assert "once_at is only valid for one-shot tasks" in data["error"]


def test_schedule_create_display_uses_once_for_one_shot_placeholder_cron() -> None:
    from invincat_cli.scheduler.display import describe_schedule_for_display

    assert describe_schedule_for_display("0 0 * * *", "Asia/Shanghai", "once") == "once"
    assert (
        describe_schedule_for_display("0 0 * * *", "Asia/Shanghai", "recurring")
        == "daily 00:00"
    )


def test_schedule_time_display_uses_explicit_offset() -> None:
    from invincat_cli.scheduler.display import format_schedule_time_for_display

    value = datetime(2026, 5, 17, 14, 9, tzinfo=UTC)

    assert (
        format_schedule_time_for_display(value, "Asia/Shanghai")
        == "2026-05-17T22:09+08:00"
    )
    assert (
        format_schedule_time_for_display(
            "2026-05-17T14:09:00+00:00",
            "Asia/Shanghai",
        )
        == "2026-05-17T22:09+08:00"
    )


def test_schedule_time_display_falls_back_to_utc_for_invalid_timezone() -> None:
    from invincat_cli.scheduler.display import format_schedule_time_for_display

    assert (
        format_schedule_time_for_display(
            "2026-05-17T14:09:00+00:00",
            "Bad/Zone",
        )
        == "2026-05-17T14:09+00:00"
    )


def test_tui_delegates_wecom_delivery_tasks_to_running_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.app_runtime.scheduler import wecom_daemon_claims_scheduled_task

    monkeypatch.setattr(
        "invincat_cli.wecom.daemon.is_daemon_running",
        lambda cwd: str(cwd) == str(tmp_path),
    )
    tui_task = _make_task(cwd=str(tmp_path))
    wecom_task = _make_task(cwd=str(tmp_path))
    wecom_task.delivery = DeliverySpec(channels=[{"type": "wecom", "chatid": "chat-1"}])
    other_cwd_task = _make_task(cwd=str(tmp_path / "other"))
    other_cwd_task.delivery = DeliverySpec(
        channels=[{"type": "wecom", "chatid": "chat-1"}]
    )

    assert wecom_daemon_claims_scheduled_task(tui_task, tmp_path) is False
    assert wecom_daemon_claims_scheduled_task(wecom_task, tmp_path) is True
    assert wecom_daemon_claims_scheduled_task(other_cwd_task, tmp_path) is False


def test_schedule_middleware_delete_tool_alias_returns_cancel_payload(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    store.save_task(_make_task(task_id="task-1"))
    mw = ScheduleMiddleware(store=store)
    delete_tool = next(t for t in mw.tools if t.name == "delete_scheduled_task")

    result = _invoke_tool(delete_tool, {"task_id": "task-1"})
    data = json.loads(result)

    assert data["type"] == SCHEDULE_CANCEL_TYPE
    assert data["task_id"] == "task-1"


def test_schedule_middleware_scoped_store_hides_cross_cwd_tasks(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduler.db"
    base_store = SchedulerStore(db_path=db_path)
    base_store.save_task(_make_task(task_id="a", title="Project A", cwd="/tmp/a"))
    task_b = _make_task(task_id="b", title="Project B", cwd="/tmp/b")
    task_b.delivery = DeliverySpec(channels=[{"type": "wecom", "chatid": "secret-b"}])
    base_store.save_task(task_b)

    scoped_store = CwdScopedSchedulerStore("/tmp/a", db_path=db_path)
    mw = ScheduleMiddleware(store=scoped_store)
    list_tool = next(t for t in mw.tools if t.name == "list_scheduled_tasks")
    update_tool = next(t for t in mw.tools if t.name == "update_scheduled_task")
    delete_tool = next(t for t in mw.tools if t.name == "delete_scheduled_task")
    run_now_tool = next(t for t in mw.tools if t.name == "run_scheduled_task_now")

    list_data = json.loads(_invoke_tool(list_tool, {}))
    assert [task["id"] for task in list_data["tasks"]] == ["a"]
    assert "secret-b" not in json.dumps(list_data, ensure_ascii=False)

    update_data = json.loads(_invoke_tool(update_tool, {"task_id": "b", "title": "x"}))
    delete_data = json.loads(_invoke_tool(delete_tool, {"task_id": "b"}))
    run_now_data = json.loads(_invoke_tool(run_now_tool, {"task_id": "b"}))

    assert "not found" in update_data["error"]
    assert "not found" in delete_data["error"]
    assert "not found" in run_now_data["error"]


def test_schedule_middleware_list_includes_delivery_and_output_mode(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    task = _make_task()
    task.delivery = DeliverySpec(channels=[{"type": "wecom", "chatid": "chat-1"}])
    task.report = ReportSpec(mode="report")
    task.next_run_at = "2026-05-17T14:09:00+00:00"
    store.save_task(task)

    mw = ScheduleMiddleware(store=store)
    list_tool = next(t for t in mw.tools if t.name == "list_scheduled_tasks")
    result = _invoke_tool(list_tool, {})
    data = json.loads(result)

    assert data["type"] == SCHEDULE_LIST_TYPE
    assert data["tasks"][0]["delivery"] == [{"type": "wecom", "chatid": "chat-1"}]
    assert data["tasks"][0]["output_mode"] == "report"
    assert data["tasks"][0]["schedule_type"] == "recurring"
    assert data["tasks"][0]["next_run_at"] == "2026-05-17T14:09:00+00:00"
    assert data["tasks"][0]["next_run_display"] == "2026-05-17T22:09+08:00"


def test_report_path_stays_under_task_cwd(tmp_path: Path) -> None:
    task = _make_task(cwd=str(tmp_path))
    task.report = ReportSpec(
        mode="report",
        output_dir="reports",
        filename_template="{task_slug}-{date}.txt",
        format="text",
    )

    path = resolve_report_path(task, "2026-05-17")

    assert path == tmp_path / "reports" / "test-task-2026-05-17.txt"
    assert report_display_path(task, "2026-05-17") == "reports/test-task-2026-05-17.txt"


def test_report_path_rejects_escape(tmp_path: Path) -> None:
    task = _make_task(cwd=str(tmp_path))
    task.report = ReportSpec(mode="report", output_dir="../outside")

    with pytest.raises(ValueError, match="escapes"):
        resolve_report_path(task, "2026-05-17")


def test_save_fallback_report_rejects_escape(tmp_path: Path) -> None:
    task = _make_task(cwd=str(tmp_path))
    task.report = ReportSpec(mode="report", output_dir="../outside")

    assert save_fallback_report(task, "content", "2026-05-17") is None
    assert not (tmp_path.parent / "outside").exists()


def test_schedule_middleware_create_tool_invalid_schedule(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    mw = ScheduleMiddleware(store=store)
    create_tool = next(t for t in mw.tools if t.name == "create_scheduled_task")
    result = _invoke_tool(
        create_tool,
        {
            "title": "Bad",
            "schedule": "whenever I feel like it",
            "prompt": "test",
        },
    )
    data = json.loads(result)
    assert "error" in data


def test_schedule_middleware_rejects_schedule_update_for_one_shot(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    task = _make_task()
    task.schedule_type = "once"
    task.run_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    store.save_task(task)
    mw = ScheduleMiddleware(store=store)
    update_tool = next(t for t in mw.tools if t.name == "update_scheduled_task")

    result = _invoke_tool(
        update_tool,
        {
            "task_id": task.id,
            "schedule": "daily 08:00",
        },
    )
    data = json.loads(result)

    assert "one-shot" in data["error"]


def test_schedule_middleware_update_rejects_invalid_timezone(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    task = _make_task()
    store.save_task(task)
    mw = ScheduleMiddleware(store=store)
    update_tool = next(t for t in mw.tools if t.name == "update_scheduled_task")

    result = _invoke_tool(
        update_tool,
        {
            "task_id": task.id,
            "timezone": "Bad/Zone",
        },
    )
    data = json.loads(result)

    assert "Invalid timezone" in data["error"]


def test_schedule_middleware_hides_tools_during_scheduled_run(tmp_path: Path) -> None:
    from invincat_cli.scheduler.tool import SCHEDULE_CONTEXT_FLAG

    store = _make_store(tmp_path)
    mw = ScheduleMiddleware(store=store)

    runtime = SimpleNamespace(context={SCHEDULE_CONTEXT_FLAG: True})

    class FakeTool:
        name = "create_scheduled_task"

    filtered = mw._filter_tools([FakeTool()], runtime)
    assert len(filtered) == 0


def test_schedule_middleware_shows_tools_during_normal_run(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    mw = ScheduleMiddleware(store=store)

    runtime = SimpleNamespace(context={})

    class FakeTool:
        name = "create_scheduled_task"

    filtered = mw._filter_tools([FakeTool()], runtime)
    assert len(filtered) == 1


def test_schedule_middleware_rejects_management_tool_call_during_scheduled_run(
    tmp_path: Path,
) -> None:
    from invincat_cli.scheduler.tool import SCHEDULE_CONTEXT_FLAG

    store = _make_store(tmp_path)
    mw = ScheduleMiddleware(store=store)
    request = SimpleNamespace(
        tool_call={"name": "create_scheduled_task", "id": "call-1"},
        runtime=SimpleNamespace(context={SCHEDULE_CONTEXT_FLAG: True}),
    )

    def handler(_request):  # noqa: ANN001
        raise AssertionError("handler should not be called")

    result = mw.wrap_tool_call(request, handler)

    assert result.status == "error"
    assert "not available during scheduled runs" in result.content


# ---------------------------------------------------------------------------
# runner: finish_run updates task status and run count
# ---------------------------------------------------------------------------


def test_runner_finish_run_updates_status(tmp_path: Path) -> None:
    """finish_run marks the task as success and increments run_count."""
    store = _make_store(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    task = _make_task(next_run_at=past)
    store.save_task(task)

    fired_run_ids: list[tuple[str, str]] = []

    async def inject(task_id: str, run_id: str, prompt: str) -> None:
        fired_run_ids.append((task_id, run_id))

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: False,
    )
    asyncio.run(runner.tick())
    assert len(fired_run_ids) == 1
    task_id, run_id = fired_run_ids[0]

    # Simulate TUI calling finish_run after the agent turn completes
    runner.finish_run(run_id, task_id, status="success")

    loaded = store.load_task("task-1")
    assert loaded is not None
    assert loaded.last_status == "success"
    assert loaded.run_count == 1
    assert task_id not in runner._running_task_ids


def test_runner_finish_run_counts_failures(tmp_path: Path) -> None:
    """finish_run with status='failed' increments failure_count."""
    store = _make_store(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    task = _make_task(next_run_at=past)
    store.save_task(task)

    fired: list[tuple[str, str]] = []

    async def inject(task_id: str, run_id: str, prompt: str) -> None:
        fired.append((task_id, run_id))

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: False,
    )
    asyncio.run(runner.tick())
    task_id, run_id = fired[0]
    runner.finish_run(run_id, task_id, status="failed", error="something broke")

    loaded = store.load_task("task-1")
    assert loaded is not None
    assert loaded.last_status == "failed"
    assert loaded.failure_count == 1


def test_runner_running_task_ids_released_after_finish(tmp_path: Path) -> None:
    """_running_task_ids is cleared only after finish_run, not after inject."""
    store = _make_store(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    task = _make_task(next_run_at=past)
    store.save_task(task)

    fired: list[tuple[str, str]] = []

    async def inject(task_id: str, run_id: str, prompt: str) -> None:
        fired.append((task_id, run_id))

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: False,
    )
    asyncio.run(runner.tick())
    assert len(fired) == 1
    # Task should still be "running" — inject returned but finish_run not yet called
    assert "task-1" in runner._running_task_ids

    task_id, run_id = fired[0]
    runner.finish_run(run_id, task_id, status="success")
    assert "task-1" not in runner._running_task_ids


def test_runner_claim_prevents_second_runner_duplicate_fire(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store.save_task(_make_task(next_run_at=past))
    first_fired: list[tuple[str, str]] = []
    second_fired: list[tuple[str, str]] = []

    async def inject_first(task_id: str, run_id: str, _prompt: str) -> None:
        first_fired.append((task_id, run_id))

    async def inject_second(task_id: str, run_id: str, _prompt: str) -> None:
        second_fired.append((task_id, run_id))

    runner1 = SchedulerRunner(
        store,
        inject_message=inject_first,
        notify=MagicMock(),
        is_busy=lambda: False,
    )
    runner2 = SchedulerRunner(
        store,
        inject_message=inject_second,
        notify=MagicMock(),
        is_busy=lambda: False,
    )

    asyncio.run(runner1.tick())
    asyncio.run(runner2.tick())

    assert len(first_fired) == 1
    assert second_fired == []


def test_runner_fire_now_preserves_recurring_next_run(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    original_next = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    task = _make_task(next_run_at=original_next)
    store.save_task(task)
    fired: list[tuple[str, str]] = []

    async def inject(task_id: str, run_id: str, _prompt: str) -> None:
        fired.append((task_id, run_id))

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: False,
    )

    asyncio.run(runner.fire_now(task))
    assert len(fired) == 1
    loaded = store.load_task("task-1")
    assert loaded is not None
    assert loaded.next_run_at == original_next

    task_id, run_id = fired[0]
    runner.finish_run(run_id, task_id, status="success")
    loaded = store.load_task("task-1")
    assert loaded is not None
    assert loaded.next_run_at == original_next


def test_runner_fire_now_preserves_one_shot_task(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    run_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    task = _make_task(next_run_at=run_at)
    task.schedule_type = "once"
    task.run_at = run_at
    task.delete_after_run = True
    store.save_task(task)
    fired: list[tuple[str, str]] = []

    async def inject(task_id: str, run_id: str, _prompt: str) -> None:
        fired.append((task_id, run_id))

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: False,
    )

    asyncio.run(runner.fire_now(task))
    task_id, run_id = fired[0]
    runner.finish_run(run_id, task_id, status="success")

    loaded = store.load_task("task-1")
    assert loaded is not None
    assert loaded.enabled is True
    assert loaded.next_run_at == run_at
    assert loaded.run_at == run_at


def test_runner_queued_fire_now_preserves_next_run(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    original_next = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    task = _make_task(next_run_at=original_next)
    store.save_task(task)
    busy = True
    fired: list[tuple[str, str]] = []

    async def inject(task_id: str, run_id: str, _prompt: str) -> None:
        fired.append((task_id, run_id))

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: busy,
    )

    asyncio.run(runner.fire_now(task))
    assert fired == []
    busy = False
    asyncio.run(runner.drain_pending_now())

    assert len(fired) == 1
    loaded = store.load_task("task-1")
    assert loaded is not None
    assert loaded.next_run_at == original_next


def test_try_start_run_recovers_stale_running_row(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    now = datetime.now(UTC).isoformat()
    store.save_task(_make_task(next_run_at=now))
    store.save_run(
        TaskRun(
            id="stale-run",
            task_id="task-1",
            scheduled_for=now,
            started_at=now,
            finished_at=None,
            status="running",
            report_path=None,
            error=None,
            thread_id=None,
            cwd="/tmp",
            runner_id="dead-runner",
            runner_kind="tui",
            runner_pid=999999999,
        )
    )
    new_run = TaskRun(
        id="new-run",
        task_id="task-1",
        scheduled_for=now,
        started_at=now,
        finished_at=None,
        status="running",
        report_path=None,
        error=None,
        thread_id=None,
        cwd="/tmp",
        runner_id="new-runner",
        runner_kind="tui",
        runner_pid=os.getpid(),
    )

    claimed = store.try_start_run("task-1", new_run)

    stale = store.load_run("stale-run")
    loaded_new = store.load_run("new-run")
    assert claimed is True
    assert stale is not None
    assert stale.status == "failed"
    assert stale.finished_at is not None
    assert loaded_new is not None
    assert loaded_new.status == "running"


def test_try_start_run_preserves_live_running_row(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    now = datetime.now(UTC).isoformat()
    store.save_task(_make_task(next_run_at=now))
    store.save_run(
        TaskRun(
            id="live-run",
            task_id="task-1",
            scheduled_for=now,
            started_at=now,
            finished_at=None,
            status="running",
            report_path=None,
            error=None,
            thread_id=None,
            cwd="/tmp",
            runner_id="live-runner",
            runner_kind="tui",
            runner_pid=os.getpid(),
        )
    )
    new_run = TaskRun(
        id="new-run",
        task_id="task-1",
        scheduled_for=now,
        started_at=now,
        finished_at=None,
        status="running",
        report_path=None,
        error=None,
        thread_id=None,
        cwd="/tmp",
        runner_id="new-runner",
        runner_kind="wecom-daemon",
        runner_pid=os.getpid(),
    )

    claimed = store.try_start_run("task-1", new_run)

    live = store.load_run("live-run")
    assert claimed is False
    assert store.load_run("new-run") is None
    assert live is not None
    assert live.status == "running"
    assert live.finished_at is None


def test_runner_filters_tasks_by_cwd(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    cwd_a = str(tmp_path / "a")
    cwd_b = str(tmp_path / "b")
    store.save_task(_make_task(task_id="a", next_run_at=past, cwd=cwd_a))
    store.save_task(_make_task(task_id="b", next_run_at=past, cwd=cwd_b))
    fired: list[str] = []

    async def inject(task_id: str, _run_id: str, _prompt: str) -> None:
        fired.append(task_id)

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: False,
        cwd=cwd_a,
    )

    asyncio.run(runner.tick())

    assert fired == ["a"]
    loaded_b = store.load_task("b")
    assert loaded_b is not None
    assert loaded_b.last_status == "never"


def test_filtered_store_excludes_wecom_tasks_from_tui_runner(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduler.db"
    base_store = SchedulerStore(db_path=db_path)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    base_store.save_task(_make_task(task_id="tui", next_run_at=past, cwd="/tmp"))
    wecom_task = _make_task(task_id="wecom", next_run_at=past, cwd="/tmp")
    wecom_task.delivery = DeliverySpec(channels=[{"type": "wecom", "chatid": "chat-1"}])
    base_store.save_task(wecom_task)

    store = FilteredSchedulerStore(
        db_path=db_path,
        exclude_task=is_wecom_deliverable_task,
    )
    fired: list[str] = []

    async def inject(task_id: str, _run_id: str, _prompt: str) -> None:
        fired.append(task_id)

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: False,
        cwd="/tmp",
    )

    asyncio.run(runner.tick())

    assert fired == ["tui"]
    loaded_wecom = base_store.load_task("wecom")
    assert loaded_wecom is not None
    assert loaded_wecom.last_status == "never"

    run = TaskRun(
        id="manual-wecom-run",
        task_id="wecom",
        scheduled_for=datetime.now(UTC).isoformat(),
        started_at=datetime.now(UTC).isoformat(),
        finished_at=None,
        status="running",
        report_path=None,
        error=None,
        thread_id=None,
        cwd="/tmp",
    )
    assert store.try_start_run("wecom", run) is False
    assert base_store.load_run("manual-wecom-run") is None


def test_runner_timeout_invokes_callback(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    now = datetime.now(UTC).isoformat()
    store.save_task(_make_task())
    store.save_run(
        TaskRun(
            id="run-1",
            task_id="task-1",
            scheduled_for=now,
            started_at=now,
            finished_at=None,
            status="running",
            report_path=None,
            error=None,
            thread_id=None,
            cwd="/tmp",
            runner_id="current-runner",
            runner_kind="tui",
            runner_pid=os.getpid(),
        )
    )
    timed_out: list[tuple[str, str]] = []

    async def on_timeout(run_id: str, task_id: str) -> None:
        timed_out.append((run_id, task_id))

    async def inject(task_id: str, run_id: str, prompt: str) -> None:
        pass

    runner = SchedulerRunner(
        store,
        inject_message=inject,
        notify=MagicMock(),
        is_busy=lambda: False,
        on_timeout=on_timeout,
    )
    runner._running_task_ids.add("task-1")

    asyncio.run(runner._timeout_watcher("run-1", "task-1", 0))

    loaded = store.load_run("run-1")
    assert loaded is not None
    assert loaded.status == "timeout"
    assert timed_out == [("run-1", "task-1")]


def test_app_scheduled_timeout_removes_pending_message() -> None:
    from invincat_cli.app import DeepAgentsApp, QueuedMessage

    app = DeepAgentsApp.__new__(DeepAgentsApp)
    app._pending_messages = deque(
        [
            QueuedMessage(
                text="timed out",
                mode="normal",
                scheduled_run_id="run-1",
                scheduled_task_id="task-1",
            ),
            QueuedMessage(text="keep", mode="normal"),
        ]
    )
    app._active_scheduled_run = None

    app._cancel_timed_out_scheduled_turn("run-1", "task-1")

    assert [msg.text for msg in app._pending_messages] == ["keep"]


def test_app_finish_active_scheduled_run_as_failed() -> None:
    from invincat_cli.app import DeepAgentsApp

    finishes: list[tuple[str, str, str, str | None]] = []

    class Runner:
        def finish_run(
            self,
            run_id: str,
            task_id: str,
            *,
            status: str,
            error: str | None = None,
        ) -> None:
            finishes.append((run_id, task_id, status, error))

    app = DeepAgentsApp.__new__(DeepAgentsApp)
    app._active_scheduled_run = ("run-1", "task-1")
    app._scheduler_runner = Runner()

    app._finish_active_scheduled_run_as_failed("boom")

    assert app._active_scheduled_run is None
    assert finishes == [("run-1", "task-1", "failed", "boom")]


def test_app_complete_active_scheduled_run_delivers_and_finishes() -> None:
    from invincat_cli.app import DeepAgentsApp
    from invincat_cli.app_runtime.scheduled_delivery import (
        complete_active_scheduled_run,
    )

    delivered: list[tuple[str, str, str, str | None]] = []
    finishes: list[tuple[str, str, str, str | None]] = []

    class Store:
        def load_run(self, _run_id: str):
            return SimpleNamespace(finished_at=None)

    class Runner:
        def finish_run(
            self,
            run_id: str,
            task_id: str,
            *,
            status: str,
            error: str | None = None,
        ) -> None:
            finishes.append((run_id, task_id, status, error))

    async def deliver(
        _app,
        *,
        task_id: str,
        run_id: str,
        status: str,
        error: str | None,
    ) -> None:
        delivered.append((run_id, task_id, status, error))

    app = DeepAgentsApp.__new__(DeepAgentsApp)
    app._active_scheduled_run = ("run-1", "task-1")
    app._scheduled_turn_status = "failed"
    app._scheduled_turn_error = "boom"
    app._scheduled_turn_retry_used = True
    app._scheduler_store = Store()
    app._scheduler_runner = Runner()

    asyncio.run(complete_active_scheduled_run(app, deliver_result=deliver))

    assert app._active_scheduled_run is None
    assert app._scheduled_turn_error is None
    assert app._scheduled_turn_retry_used is False
    assert delivered == [("run-1", "task-1", "failed", "boom")]
    assert finishes == [("run-1", "task-1", "failed", "boom")]


def test_app_complete_active_scheduled_run_skips_finished_delivery() -> None:
    from invincat_cli.app import DeepAgentsApp
    from invincat_cli.app_runtime.scheduled_delivery import (
        complete_active_scheduled_run,
    )

    delivered: list[str] = []
    finishes: list[tuple[str, str, str, str | None]] = []

    class Store:
        def load_run(self, _run_id: str):
            return SimpleNamespace(finished_at="2026-05-13T00:00:00+00:00")

    class Runner:
        def finish_run(
            self,
            run_id: str,
            task_id: str,
            *,
            status: str,
            error: str | None = None,
        ) -> None:
            finishes.append((run_id, task_id, status, error))

    async def deliver(_app, **_kwargs) -> None:  # noqa: ANN001, ANN003
        delivered.append("called")

    app = DeepAgentsApp.__new__(DeepAgentsApp)
    app._active_scheduled_run = ("run-1", "task-1")
    app._scheduled_turn_status = "success"
    app._scheduled_turn_error = None
    app._scheduled_turn_retry_used = False
    app._scheduler_store = Store()
    app._scheduler_runner = Runner()

    asyncio.run(complete_active_scheduled_run(app, deliver_result=deliver))

    assert delivered == []
    assert finishes == [("run-1", "task-1", "success", None)]


def test_app_resolves_active_scheduled_wecom_chat_id(tmp_path: Path) -> None:
    from invincat_cli.app import DeepAgentsApp
    from invincat_cli.app_runtime.scheduled_delivery import (
        active_scheduled_wecom_chat_id,
    )

    store = _make_store(tmp_path)
    task = _make_task(task_id="task-1")
    task.delivery = DeliverySpec(channels=[{"type": "wecom", "chatid": "chat-1"}])
    store.save_task(task)

    app = DeepAgentsApp.__new__(DeepAgentsApp)
    app._active_scheduled_run = ("run-1", "task-1")
    app._scheduler_store = store

    assert active_scheduled_wecom_chat_id(app) == "chat-1"


def test_app_schedule_payload_rejects_invalid_create_options() -> None:
    from invincat_cli.app import DeepAgentsApp

    saved: list[ScheduledTask] = []
    mounted: list[object] = []

    class FakeStore:
        def save_task(self, task: ScheduledTask) -> None:
            saved.append(task)

    async def mount_message(message: object) -> None:
        mounted.append(message)

    app = DeepAgentsApp.__new__(DeepAgentsApp)
    app._scheduler_store = FakeStore()
    app._cwd = "/tmp"
    app._current_wecom_inbound_frame = None
    app._mount_message = mount_message

    asyncio.run(
        app._handle_schedule_tool_payload(
            {
                "type": SCHEDULE_CREATE_TYPE,
                "task_id": "bad-timeout",
                "title": "Bad timeout",
                "cron": "0 8 * * *",
                "timezone": "Asia/Shanghai",
                "prompt": "test",
                "timeout_seconds": "not-an-int",
            }
        )
    )

    assert saved == []
    assert mounted


def test_scheduled_wecom_file_request_sends_to_task_chat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.app import DeepAgentsApp
    from invincat_cli.app_runtime.scheduled_delivery import (
        send_scheduled_wecom_file_request,
    )

    store = _make_store(tmp_path)
    task = _make_task(task_id="task-1")
    task.delivery = DeliverySpec(channels=[{"type": "wecom", "chatid": "chat-1"}])
    store.save_task(task)

    sent_payloads: list[dict] = []

    async def fake_upload(path: Path, *, send_request) -> str:  # noqa: ANN001
        assert path == (tmp_path / "report.md").resolve()
        return "media-1"

    async def fake_send_request(payload: dict) -> dict:
        sent_payloads.append(payload)
        return {"errcode": 0}

    monkeypatch.setattr(
        "invincat_cli.wecom.media.upload_wecom_outbound_media",
        fake_upload,
    )

    report = tmp_path / "report.md"
    report.write_text("hello", encoding="utf-8")

    app = DeepAgentsApp.__new__(DeepAgentsApp)
    app._active_scheduled_run = ("run-1", "task-1")
    app._scheduler_store = store
    app._wecom_bridge = object()
    app._cwd = str(tmp_path)
    app._wecom_send_request = fake_send_request

    asyncio.run(
        send_scheduled_wecom_file_request(
            app,
            {"path": str(report), "filename": "report.md"},
        )
    )

    assert sent_payloads
    assert sent_payloads[0]["body"]["chatid"] == "chat-1"
    assert sent_payloads[0]["body"]["file"]["media_id"] == "media-1"
