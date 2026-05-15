"""Tests for app exit cleanup handlers."""

from __future__ import annotations

from types import SimpleNamespace

from invincat_cli.app_runtime.exit_handlers import prepare_exit
from invincat_cli.core.session_stats import SessionStats


class _Worker:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _Task(_Worker):
    def __init__(self, *, done: bool = False) -> None:
        super().__init__()
        self._done = done

    def done(self) -> bool:
        return self._done


class _Bridge:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def test_prepare_exit_merges_inflight_stats_and_cleans_workers(monkeypatch) -> None:
    shell_worker = _Worker()
    agent_worker = _Worker()
    wecom_task = _Task()
    bridge = _Bridge()
    restored: list[str] = []
    queue_discarded: list[str] = []
    dispatched: list[tuple[str, bytes, list[str]]] = []

    monkeypatch.setattr("invincat_cli.hooks._load_hooks", lambda: ["hook"])
    monkeypatch.setattr(
        "invincat_cli.hooks._dispatch_hook_sync",
        lambda event, payload, hooks: dispatched.append((event, payload, hooks)),
    )
    inflight = SessionStats(input_tokens=10, output_tokens=5)
    app = SimpleNamespace(
        _inflight_turn_stats=inflight,
        _inflight_turn_start=100.0,
        _session_stats=SessionStats(request_count=1),
        _discard_queue=lambda: queue_discarded.append("discarded"),
        _shell_running=True,
        _shell_worker=shell_worker,
        _agent_running=True,
        _agent_worker=agent_worker,
        _wecom_task=wecom_task,
        _wecom_bridge=bridge,
        _lc_thread_id="thread-1",
    )
    monkeypatch.setattr(
        "invincat_cli.app_runtime.exit_handlers.time.monotonic", lambda: 112.5
    )

    prepare_exit(app, restore_cursor_guide=lambda: restored.append("restored"))

    assert app._inflight_turn_stats is None
    assert app._session_stats.input_tokens == 10
    assert app._session_stats.output_tokens == 5
    assert app._session_stats.wall_time_seconds == 12.5
    assert queue_discarded == ["discarded"]
    assert shell_worker.cancelled is True
    assert agent_worker.cancelled is True
    assert bridge.stopped is True
    assert wecom_task.cancelled is True
    assert restored == ["restored"]
    assert dispatched[0][0] == "session.end"
    assert b'"thread_id": "thread-1"' in dispatched[0][1]


def test_prepare_exit_skips_done_wecom_task_and_missing_hooks(monkeypatch) -> None:
    task = _Task(done=True)
    bridge = _Bridge()
    monkeypatch.setattr("invincat_cli.hooks._load_hooks", lambda: [])
    app = SimpleNamespace(
        _inflight_turn_stats=None,
        _session_stats=SessionStats(),
        _discard_queue=lambda: None,
        _shell_running=False,
        _shell_worker=None,
        _agent_running=False,
        _agent_worker=None,
        _wecom_task=task,
        _wecom_bridge=bridge,
        _lc_thread_id=None,
    )

    prepare_exit(app, restore_cursor_guide=lambda: None)

    assert task.cancelled is False
    assert bridge.stopped is False
