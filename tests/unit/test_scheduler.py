"""Unit tests for the scheduler subsystem."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from invincat_cli.scheduler.models import (
    DeliverySpec,
    ReportSpec,
    ScheduledTask,
    TaskRun,
)
from invincat_cli.scheduler.parser import describe_schedule, parse_schedule
from invincat_cli.scheduler.runner import SchedulerRunner, _parse_dt, compute_next_run
from invincat_cli.scheduler.store import SchedulerStore
from invincat_cli.scheduler.tool import (
    SCHEDULE_CREATE_TYPE,
    ScheduleMiddleware,
    parse_schedule_tool_result,
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
) -> ScheduledTask:
    now = datetime.now(timezone.utc).isoformat()
    return ScheduledTask(
        id=task_id,
        title=title,
        enabled=enabled,
        prompt="Do something",
        cron=cron,
        timezone=tz,
        cwd="/tmp",
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


def test_monthly_dom_over_28_raises() -> None:
    with pytest.raises(ValueError):
        parse_schedule("monthly 30 08:00")


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
    now_utc = now_local.astimezone(timezone.utc)
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
    now_utc = now_local.astimezone(timezone.utc)
    nxt = compute_next_run("0 8 * * *", now_utc, "Asia/Shanghai")
    assert nxt is not None
    nxt_local = nxt.astimezone(tz)
    assert nxt_local.hour == 8
    assert nxt_local.date() > now_local.date()


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


def test_store_save_and_load(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    task = _make_task()
    store.save_task(task)
    loaded = store.load_task("task-1")
    assert loaded is not None
    assert loaded.title == "Test Task"
    assert loaded.cron == "0 8 * * *"


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
    now = datetime.now(timezone.utc).isoformat()
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


def test_store_persists_after_reload(tmp_path: Path) -> None:
    """Task survives a store re-instantiation (simulates TUI restart)."""
    db = tmp_path / "scheduler.db"
    store1 = SchedulerStore(db_path=db)
    store1.save_task(_make_task(title="Persistent"))
    store2 = SchedulerStore(db_path=db)
    loaded = store2.load_task("task-1")
    assert loaded is not None
    assert loaded.title == "Persistent"


# ---------------------------------------------------------------------------
# runner: misfire policy
# ---------------------------------------------------------------------------


def test_runner_skips_task_when_policy_skip(tmp_path: Path) -> None:
    """run_once=skip: a missed task is not queued."""
    store = _make_store(tmp_path)
    past = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    task = _make_task(next_run_at=past, misfire_policy="skip")
    store.save_task(task)

    injected: list[str] = []

    async def inject(task_id: str, prompt: str) -> None:
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
    past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    task = _make_task(next_run_at=past, misfire_policy="run_once")
    store.save_task(task)

    injected: list[str] = []

    async def inject(task_id: str, prompt: str) -> None:
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
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    task = _make_task(next_run_at=past)
    store.save_task(task)

    injected: list[str] = []

    async def inject(task_id: str, prompt: str) -> None:
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
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    task = _make_task(next_run_at=past)
    store.save_task(task)

    injected: list[str] = []

    async def inject(task_id: str, prompt: str) -> None:
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


def test_runner_disabled_task_not_triggered(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    task = _make_task(next_run_at=past, enabled=False)
    store.save_task(task)

    injected: list[str] = []

    async def inject(task_id: str, prompt: str) -> None:
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
    payload = json.dumps({
        "type": SCHEDULE_CREATE_TYPE,
        "title": "Daily report",
        "cron": "0 8 * * *",
        "tool_call_id": "abc",
    })
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
    result = _invoke_tool(create_tool, {
        "title": "Daily analysis",
        "schedule": "daily 08:00",
        "prompt": "Analyse the project",
    })
    data = json.loads(result)
    assert data["type"] == SCHEDULE_CREATE_TYPE
    assert data["cron"] == "0 8 * * *"
    assert data["title"] == "Daily analysis"


def test_schedule_middleware_create_tool_invalid_schedule(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    mw = ScheduleMiddleware(store=store)
    create_tool = next(t for t in mw.tools if t.name == "create_scheduled_task")
    result = _invoke_tool(create_tool, {
        "title": "Bad",
        "schedule": "whenever I feel like it",
        "prompt": "test",
    })
    data = json.loads(result)
    assert "error" in data


def test_schedule_middleware_hides_tools_during_scheduled_run(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from invincat_cli.scheduler.tool import SCHEDULE_CONTEXT_FLAG

    store = _make_store(tmp_path)
    mw = ScheduleMiddleware(store=store)

    runtime = SimpleNamespace(context={SCHEDULE_CONTEXT_FLAG: True})

    class FakeTool:
        name = "create_scheduled_task"

    filtered = mw._filter_tools([FakeTool()], runtime)
    assert len(filtered) == 0


def test_schedule_middleware_shows_tools_during_normal_run(tmp_path: Path) -> None:
    from types import SimpleNamespace

    store = _make_store(tmp_path)
    mw = ScheduleMiddleware(store=store)

    runtime = SimpleNamespace(context={})

    class FakeTool:
        name = "create_scheduled_task"

    filtered = mw._filter_tools([FakeTool()], runtime)
    assert len(filtered) == 1
