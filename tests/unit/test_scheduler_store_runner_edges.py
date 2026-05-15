"""Edge-case coverage for scheduler store and runner internals."""

from __future__ import annotations

import asyncio
import errno
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import invincat_cli.scheduler.runner as runner_mod
import invincat_cli.scheduler.store as store_mod
from invincat_cli.scheduler.models import (
    DeliverySpec,
    ReportSpec,
    ScheduledTask,
    TaskRun,
)
from invincat_cli.scheduler.runner import (
    SchedulerRunner,
    _build_scheduled_prompt,
    _parse_dt,
    _PendingRun,
    compute_next_run,
)
from invincat_cli.scheduler.store import (
    CwdScopedSchedulerStore,
    FilteredSchedulerStore,
    SchedulerStore,
    _connect,
    _parse_iso_datetime,
    _pid_is_alive,
    _running_row_is_stale,
)


def _task(
    *,
    task_id: str = "task-1",
    cwd: str = "/tmp/project",
    enabled: bool = True,
    next_run_at: str | None = None,
    schedule_type: str = "recurring",
    misfire_policy: str = "run_once",
    title: str = "Task One",
) -> ScheduledTask:
    now = datetime.now(UTC).isoformat()
    return ScheduledTask(
        id=task_id,
        title=title,
        enabled=enabled,
        prompt="Do it",
        cron="0 8 * * *",
        timezone="Asia/Shanghai",
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
        schedule_type=schedule_type,  # type: ignore[arg-type]
        run_at=next_run_at if schedule_type == "once" else None,
        timeout_seconds=0,
    )


def _run(
    *,
    run_id: str = "run-1",
    task_id: str = "task-1",
    finished_at: str | None = None,
) -> TaskRun:
    now = datetime.now(UTC).isoformat()
    return TaskRun(
        id=run_id,
        task_id=task_id,
        scheduled_for=now,
        started_at=now,
        finished_at=finished_at,
        status="success" if finished_at else "running",
        report_path=None,
        error=None,
        thread_id=None,
        cwd="/tmp/project",
    )


class FakeRunnerStore:
    def __init__(
        self,
        tasks: list[ScheduledTask] | None = None,
        *,
        claim_result: bool = True,
        list_error: Exception | None = None,
        reconcile_error: Exception | None = None,
    ) -> None:
        self.tasks = {task.id: task for task in tasks or []}
        self.runs: dict[str, TaskRun] = {}
        self.claim_result = claim_result
        self.list_error = list_error
        self.reconcile_error = reconcile_error
        self.update_calls: list[tuple[str, dict[str, Any]]] = []
        self.enabled_calls: list[tuple[str, bool]] = []
        self.try_start_calls: list[tuple[str, TaskRun, dict[str, Any]]] = []
        self.saved_runs: list[TaskRun] = []
        self.deleted: list[str] = []
        self.disabled_after_run: list[str] = []

    def reconcile_orphan_runs(self, *_args: Any, **_kwargs: Any) -> int:
        if self.reconcile_error is not None:
            raise self.reconcile_error
        return 0

    def list_tasks(self, *, enabled_only: bool = False, cwd: str | None = None):
        if self.list_error is not None:
            raise self.list_error
        tasks = list(self.tasks.values())
        if enabled_only:
            tasks = [task for task in tasks if task.enabled]
        if cwd is not None:
            tasks = [task for task in tasks if task.cwd == cwd]
        return tasks

    def update_task_status(self, task_id: str, **kwargs: Any) -> None:
        self.update_calls.append((task_id, kwargs))
        task = self.tasks.get(task_id)
        if task is None:
            return
        if "last_status" in kwargs:
            task.last_status = kwargs["last_status"]
        if kwargs.get("clear_next_run_at"):
            task.next_run_at = None
        elif kwargs.get("next_run_at") is not None:
            task.next_run_at = kwargs["next_run_at"]

    def set_task_enabled(self, task_id: str, enabled: bool) -> None:
        self.enabled_calls.append((task_id, enabled))
        if task_id in self.tasks:
            self.tasks[task_id].enabled = enabled

    def load_task(self, task_id: str) -> ScheduledTask | None:
        return self.tasks.get(task_id)

    def try_start_run(self, task_id: str, run: TaskRun, **kwargs: Any) -> bool:
        self.try_start_calls.append((task_id, run, kwargs))
        if not self.claim_result:
            return False
        self.runs[run.id] = run
        return True

    def load_run(self, run_id: str) -> TaskRun | None:
        return self.runs.get(run_id)

    def save_run(self, run: TaskRun) -> None:
        self.saved_runs.append(run)
        self.runs[run.id] = run

    def delete_task(self, task_id: str) -> None:
        self.deleted.append(task_id)
        self.tasks.pop(task_id, None)

    def disable_task_after_run(self, task_id: str) -> None:
        self.disabled_after_run.append(task_id)
        if task_id in self.tasks:
            self.tasks[task_id].enabled = False


def _runner(
    store: FakeRunnerStore,
    *,
    fired: list[tuple[str, str, str]] | None = None,
    busy: bool = False,
    cwd: str | None = None,
    inject_error: Exception | None = None,
) -> SchedulerRunner:
    async def inject(task_id: str, run_id: str, prompt: str) -> None:
        if inject_error is not None:
            raise inject_error
        if fired is not None:
            fired.append((task_id, run_id, prompt))

    return SchedulerRunner(
        store,  # type: ignore[arg-type]
        inject_message=inject,
        notify=lambda _msg: None,
        is_busy=lambda: busy,
        cwd=cwd,
    )


def test_default_scheduler_db_path_uses_invincat_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(store_mod, "_DB_PATH", None)
    monkeypatch.setattr(store_mod.Path, "home", lambda: tmp_path)

    path = store_mod.get_scheduler_db_path()

    assert path == tmp_path / ".invincat" / "scheduler.db"
    assert path.parent.exists()


def test_connect_wraps_directory_creation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_mkdir(self: Path, *_args: Any, **_kwargs: Any) -> None:
        raise OSError(f"cannot create {self}")

    monkeypatch.setattr(store_mod.Path, "mkdir", fail_mkdir)

    with pytest.raises(sqlite3.OperationalError, match="unable to create"):
        _connect(tmp_path / "nested" / "scheduler.db")


def test_connect_wraps_sqlite_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_connect(_path: str) -> sqlite3.Connection:
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(store_mod.sqlite3, "connect", fail_connect)

    with pytest.raises(sqlite3.OperationalError, match="unable to open"):
        _connect(tmp_path / "scheduler.db")


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (None, True),
        (ProcessLookupError(), False),
        (PermissionError(), True),
        (OSError(errno.ESRCH, "no process"), False),
        (OSError(errno.EPERM, "not permitted"), True),
    ],
)
def test_pid_is_alive_interprets_signal_results(
    monkeypatch: pytest.MonkeyPatch,
    raised: BaseException | None,
    expected: bool,
) -> None:
    def fake_kill(_pid: int, _sig: int) -> None:
        if raised is not None:
            raise raised

    monkeypatch.setattr(store_mod.os, "kill", fake_kill)

    assert _pid_is_alive(1234) is expected


@pytest.mark.parametrize(
    ("err_no", "expected"), [(errno.ESRCH, False), (errno.EPERM, True)]
)
def test_pid_is_alive_interprets_plain_oserror_errno(
    monkeypatch: pytest.MonkeyPatch,
    err_no: int,
    expected: bool,
) -> None:
    def fake_kill(_pid: int, _sig: int) -> None:
        exc = OSError("signal failed")
        exc.errno = err_no
        raise exc

    monkeypatch.setattr(store_mod.os, "kill", fake_kill)

    assert _pid_is_alive(1234) is expected


def test_pid_is_alive_rejects_empty_and_reraises_unexpected_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _pid_is_alive(None) is False
    assert _pid_is_alive(0) is False

    def fail_kill(_pid: int, _sig: int) -> None:
        raise OSError(errno.EIO, "io")

    monkeypatch.setattr(store_mod.os, "kill", fail_kill)
    with pytest.raises(OSError):
        _pid_is_alive(1234)


def test_parse_iso_datetime_handles_empty_bad_naive_and_aware_values() -> None:
    assert _parse_iso_datetime(None) is None
    assert _parse_iso_datetime("not-a-date") is None
    assert _parse_iso_datetime("2026-05-14T08:00:00") == datetime(
        2026, 5, 14, 8, 0, tzinfo=UTC
    )
    assert _parse_iso_datetime("2026-05-14T16:00:00+08:00") == datetime(
        2026, 5, 14, 8, 0, tzinfo=UTC
    )


def test_running_row_staleness_depends_on_owner_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 5, 14, 8, 0, tzinfo=UTC)
    old_started = (now - timedelta(seconds=700)).isoformat()
    recent_started = (now - timedelta(seconds=120)).isoformat()

    monkeypatch.setattr(store_mod, "_pid_is_alive", lambda pid: pid == 123)

    assert _running_row_is_stale({}, task_timeout_seconds=600, now=now) is True
    assert (
        _running_row_is_stale(
            {"runner_pid": 123, "started_at": old_started},
            task_timeout_seconds=600,
            now=now,
        )
        is True
    )
    assert (
        _running_row_is_stale(
            {"runner_pid": 123, "started_at": recent_started},
            task_timeout_seconds=600,
            now=now,
        )
        is False
    )
    assert (
        _running_row_is_stale(
            {"runner_pid": 123, "started_at": old_started},
            task_timeout_seconds=0,
            now=now,
        )
        is False
    )
    assert (
        _running_row_is_stale(
            {"runner_pid": 123, "started_at": "bad"},
            task_timeout_seconds=600,
            now=now,
        )
        is False
    )


def test_scoped_store_list_and_load_hide_other_directories(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduler.db"
    base = SchedulerStore(db_path=db_path)
    base.save_task(_task(task_id="mine", cwd="/tmp/a"))
    base.save_task(_task(task_id="other", cwd="/tmp/b"))
    scoped = CwdScopedSchedulerStore("/tmp/a", db_path=db_path)

    assert scoped.list_tasks(cwd="/tmp/b") == []
    assert [task.id for task in scoped.list_tasks()] == ["mine"]
    assert scoped.load_task("mine") is not None
    assert scoped.load_task("other") is None


def test_filtered_store_treats_filter_errors_as_not_excluded(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduler.db"
    base = SchedulerStore(db_path=db_path)
    task = _task()
    base.save_task(task)

    def broken_filter(_task_obj: Any) -> bool:
        raise RuntimeError("filter failed")

    store = FilteredSchedulerStore(db_path=db_path, exclude_task=broken_filter)
    run = _run()

    assert [loaded.id for loaded in store.list_tasks()] == [task.id]
    assert store.try_start_run(task.id, run) is True


def test_try_start_run_returns_false_for_unclaimable_task_states(
    tmp_path: Path,
) -> None:
    store = SchedulerStore(db_path=tmp_path / "scheduler.db")
    store.save_task(_task(task_id="disabled", enabled=False))
    next_run_at = datetime.now(UTC).isoformat()
    store.save_task(_task(task_id="mismatch", next_run_at=next_run_at))

    assert store.try_start_run("missing", _run(task_id="missing")) is False
    assert store.try_start_run("disabled", _run(task_id="disabled")) is False
    assert (
        store.try_start_run(
            "mismatch",
            _run(task_id="mismatch"),
            expected_next_run_at=(datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        )
        is False
    )


def test_compute_next_run_returns_none_for_invalid_inputs() -> None:
    assert compute_next_run("not cron", datetime.now(UTC), "Bad/Zone") is None


def test_scheduled_prompt_uses_safe_fallback_for_invalid_report_path(
    tmp_path: Path,
) -> None:
    task = _task(cwd=str(tmp_path), title="Quarterly: ROI?!")
    task.report = ReportSpec(mode="report", output_dir="../outside")
    scheduled_for = datetime(2026, 5, 17, 14, 9, tzinfo=UTC)

    prompt = _build_scheduled_prompt(task, scheduled_for)

    assert "Save the report to: ../outside/quarterly--roi-2026-05-17.markdown" in prompt


def test_runner_ignores_startup_reconcile_and_tick_list_failures() -> None:
    _runner(FakeRunnerStore(reconcile_error=RuntimeError("boom")))
    runner = _runner(FakeRunnerStore(list_error=RuntimeError("boom")))

    asyncio.run(runner.tick())


def test_evaluate_task_initializes_missing_next_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 5, 14, 8, 0, tzinfo=UTC)
    next_run = now + timedelta(hours=1)
    task = _task(next_run_at=None)
    store = FakeRunnerStore([task])
    runner = _runner(store)
    monkeypatch.setattr(runner_mod, "task_next_run", lambda _task_obj, _now: next_run)

    asyncio.run(runner._evaluate_task(task, now))

    assert store.update_calls == [
        (
            task.id,
            {"last_status": "never", "next_run_at": next_run.isoformat()},
        )
    ]


@pytest.mark.parametrize("policy", ["very_old", "skip"])
def test_evaluate_task_disables_missed_one_shot_tasks(policy: str) -> None:
    now = datetime(2026, 5, 14, 8, 0, tzinfo=UTC)
    if policy == "very_old":
        next_run_at = now - timedelta(days=2)
        misfire_policy = "run_once"
    else:
        next_run_at = now - timedelta(minutes=10)
        misfire_policy = "skip"
    task = _task(
        next_run_at=next_run_at.isoformat(),
        schedule_type="once",
        misfire_policy=misfire_policy,
    )
    store = FakeRunnerStore([task])
    runner = _runner(store)

    asyncio.run(runner._evaluate_task(task, now))

    assert store.update_calls[-1][1] == {
        "last_status": "missed",
        "clear_next_run_at": True,
    }
    assert store.enabled_calls == [(task.id, False)]


def test_evaluate_task_advances_very_old_recurring_misfire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 5, 14, 8, 0, tzinfo=UTC)
    advanced = now + timedelta(hours=1)
    task = _task(next_run_at=(now - timedelta(days=2)).isoformat())
    store = FakeRunnerStore([task])
    runner = _runner(store)
    monkeypatch.setattr(runner_mod, "task_next_run", lambda _task_obj, _now: advanced)

    asyncio.run(runner._evaluate_task(task, now))

    assert store.update_calls == [
        (
            task.id,
            {"last_status": "missed", "next_run_at": advanced.isoformat()},
        )
    ]


@pytest.mark.parametrize("loaded_task", [None, _task(cwd="/tmp/other")])
def test_drain_pending_skips_missing_and_cross_cwd_tasks(
    loaded_task: ScheduledTask | None,
) -> None:
    queued = _task(cwd="/tmp/project")
    store = FakeRunnerStore([loaded_task] if loaded_task is not None else [])
    fired: list[tuple[str, str, str]] = []
    runner = _runner(store, fired=fired, cwd="/tmp/project")
    runner._pending_runs.append(
        _PendingRun(
            task=queued,
            scheduled_for=datetime.now(UTC),
            expected_next_run_at=queued.next_run_at,
        )
    )
    runner._pending_task_ids.add(queued.id)

    asyncio.run(runner._drain_pending(datetime.now(UTC)))

    assert fired == []
    assert runner._pending_task_ids == set()


def test_fire_now_returns_when_task_is_already_running() -> None:
    task = _task()
    store = FakeRunnerStore([task])
    runner = _runner(store)
    runner._running_task_ids.add(task.id)

    asyncio.run(runner.fire_now(task))

    assert store.try_start_calls == []


def test_fire_now_returns_when_task_is_already_pending() -> None:
    task = _task()
    store = FakeRunnerStore([task])
    fired: list[tuple[str, str, str]] = []
    runner = _runner(store, fired=fired)
    runner._pending_task_ids.add(task.id)

    asyncio.run(runner.fire_now(task))

    assert fired == []
    assert store.try_start_calls == []
    assert runner._pending_runs == []


def test_fire_returns_without_injecting_when_claim_fails() -> None:
    task = _task()
    store = FakeRunnerStore([task], claim_result=False)
    runner = _runner(store, inject_error=AssertionError("should not inject"))

    asyncio.run(runner._fire(task, datetime.now(UTC), datetime.now(UTC)))

    assert len(store.try_start_calls) == 1
    assert runner._running_task_ids == set()


def test_fire_marks_run_failed_when_injection_fails() -> None:
    task = _task()
    store = FakeRunnerStore([task])
    runner = _runner(store, inject_error=RuntimeError("inject failed"))

    asyncio.run(runner._fire(task, datetime.now(UTC), datetime.now(UTC)))

    [run] = store.saved_runs
    assert run.status == "failed"
    assert run.error == "inject failed"
    assert runner._running_task_ids == set()


def test_finish_run_releases_locks_when_run_is_missing() -> None:
    store = FakeRunnerStore([_task()])
    runner = _runner(store)
    runner._running_task_ids.add("task-1")
    runner._manual_run_ids.add("missing-run")

    runner.finish_run("missing-run", "task-1", status="success")

    assert runner._running_task_ids == set()
    assert runner._manual_run_ids == set()
    assert store.saved_runs == []


def test_finish_run_does_not_double_count_already_finished_run() -> None:
    finished_at = datetime.now(UTC).isoformat()
    store = FakeRunnerStore([_task()])
    store.runs["run-1"] = _run(finished_at=finished_at)
    runner = _runner(store)
    runner._running_task_ids.add("task-1")
    runner._manual_run_ids.add("run-1")

    runner.finish_run("run-1", "task-1", status="success")

    assert runner._running_task_ids == set()
    assert runner._manual_run_ids == set()
    assert store.saved_runs == []
    assert store.update_calls == []


def test_parse_dt_handles_empty_bad_naive_and_aware_values() -> None:
    assert _parse_dt(None) is None
    assert _parse_dt("bad") is None
    assert _parse_dt("2026-05-14T08:00:00") == datetime(2026, 5, 14, 8, 0, tzinfo=UTC)
    aware = _parse_dt("2026-05-14T16:00:00+08:00")
    assert aware is not None
    assert aware.utcoffset() == timedelta(hours=8)
