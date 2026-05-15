from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from invincat_cli.app_runtime import schedule_handlers
from invincat_cli.scheduler import payloads
from invincat_cli.widgets.messages import AppMessage, ErrorMessage


class DummyStore:
    def __init__(self, tasks: dict[str, object] | None = None) -> None:
        self.tasks = tasks or {}
        self.saved: list[object] = []
        self.deleted: list[str] = []
        self.enabled: list[tuple[str, bool]] = []

    def save_task(self, task: object) -> None:
        self.saved.append(task)
        self.tasks[getattr(task, "id")] = task

    def load_task(self, task_id: str) -> object | None:
        return self.tasks.get(task_id)

    def delete_task(self, task_id: str) -> None:
        self.deleted.append(task_id)
        self.tasks.pop(task_id, None)

    def set_task_enabled(self, task_id: str, enabled: bool) -> None:
        self.enabled.append((task_id, enabled))


class DummyRunner:
    def __init__(self) -> None:
        self.fired: list[object] = []

    async def fire_now(self, task: object) -> None:
        self.fired.append(task)


class ScheduleApp:
    def __init__(self, store: DummyStore | None = None) -> None:
        self._scheduler_store = store or DummyStore()
        self._scheduler_runner = DummyRunner()
        self._cwd = "/repo"
        self._current_wecom_inbound_frame = None
        self._chat_input = SimpleNamespace(
            focused=0,
            focus_input=lambda: setattr(
                self._chat_input,
                "focused",
                self._chat_input.focused + 1,
            ),
        )
        self.messages: list[object] = []
        self.screens: list[object] = []
        self.callbacks: list[object] = []
        self.later: list[tuple[object, tuple[object, ...]]] = []

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    def push_screen(self, screen: object, callback: object) -> None:
        self.screens.append(screen)
        self.callbacks.append(callback)

    def call_later(self, callback: object, *args: object) -> None:
        self.later.append((callback, args))


def task(task_id: str = "task-1", title: str = "Task") -> SimpleNamespace:
    return SimpleNamespace(id=task_id, title=title, timezone="UTC")


def message_contents(app: ScheduleApp) -> list[object]:
    return [getattr(message, "_content", None) for message in app.messages]


def test_handle_schedule_create_saves_task(monkeypatch) -> None:
    app = ScheduleApp()
    created = task(title="Created")

    monkeypatch.setattr(
        payloads,
        "build_schedule_create_payload_result",
        lambda *_args, **_kwargs: SimpleNamespace(
            task=created,
            schedule_description="daily",
            next_run_display="tomorrow",
            report_path_display="report.md",
        ),
    )

    asyncio.run(
        schedule_handlers.handle_schedule_tool_payload(
            app,
            {"type": "schedule_create"},
        )
    )

    assert app._scheduler_store.saved == [created]
    assert isinstance(app.messages[-1], AppMessage)


def test_handle_schedule_create_mounts_payload_error(monkeypatch) -> None:
    app = ScheduleApp()

    def fail(*_args, **_kwargs):
        raise ValueError("bad schedule")

    monkeypatch.setattr(payloads, "build_schedule_create_payload_result", fail)

    asyncio.run(
        schedule_handlers.handle_schedule_tool_payload(
            app,
            {"type": "schedule_create"},
        )
    )

    assert isinstance(app.messages[-1], ErrorMessage)
    assert "bad schedule" in str(message_contents(app)[-1])


def test_handle_schedule_update_saves_updated_task(monkeypatch) -> None:
    original = task(title="Old")
    updated = task(title="New")
    app = ScheduleApp(DummyStore({"task-1": original}))
    monkeypatch.setattr(
        payloads, "apply_schedule_update_payload", lambda *_args: updated
    )

    asyncio.run(
        schedule_handlers.handle_schedule_tool_payload(
            app,
            {"type": "schedule_update", "task_id": "task-1", "updates": {}},
        )
    )

    assert app._scheduler_store.saved == [updated]
    assert isinstance(app.messages[-1], AppMessage)


def test_handle_schedule_update_reports_missing_or_invalid(monkeypatch) -> None:
    missing_app = ScheduleApp()

    asyncio.run(
        schedule_handlers.handle_schedule_tool_payload(
            missing_app,
            {"type": "schedule_update", "task_id": "missing"},
        )
    )

    assert isinstance(missing_app.messages[-1], AppMessage)

    invalid_app = ScheduleApp(DummyStore({"task-1": task()}))

    def fail(*_args):
        raise ValueError("invalid update")

    monkeypatch.setattr(payloads, "apply_schedule_update_payload", fail)

    asyncio.run(
        schedule_handlers.handle_schedule_tool_payload(
            invalid_app,
            {"type": "schedule_update", "task_id": "task-1", "updates": {}},
        )
    )

    assert isinstance(invalid_app.messages[-1], ErrorMessage)


def test_handle_schedule_cancel_deletes_existing_or_missing_task() -> None:
    existing = task(title="Existing")
    app = ScheduleApp(DummyStore({"task-1": existing}))

    asyncio.run(
        schedule_handlers.handle_schedule_tool_payload(
            app,
            {"type": "schedule_cancel", "task_id": "task-1"},
        )
    )

    assert app._scheduler_store.deleted == ["task-1"]
    assert isinstance(app.messages[-1], AppMessage)

    asyncio.run(
        schedule_handlers.handle_schedule_tool_payload(
            app,
            {"type": "schedule_cancel", "task_id": "missing"},
        )
    )

    assert app._scheduler_store.deleted[-1] == "missing"


def test_handle_schedule_run_now_respects_missing_daemon_and_runner(
    monkeypatch,
) -> None:
    app = ScheduleApp()

    asyncio.run(
        schedule_handlers.handle_schedule_tool_payload(
            app,
            {"type": "schedule_run_now", "task_id": "missing"},
        )
    )

    assert isinstance(app.messages[-1], AppMessage)

    scheduled = task(title="Runnable")
    app = ScheduleApp(DummyStore({"task-1": scheduled}))
    monkeypatch.setattr(
        schedule_handlers,
        "wecom_daemon_claims_scheduled_task",
        lambda *_args: True,
    )

    asyncio.run(
        schedule_handlers.handle_schedule_tool_payload(
            app,
            {"type": "schedule_run_now", "task_id": "task-1", "title": "Runnable"},
        )
    )

    assert app._scheduler_runner.fired == []

    monkeypatch.setattr(
        schedule_handlers,
        "wecom_daemon_claims_scheduled_task",
        lambda *_args: False,
    )
    asyncio.run(
        schedule_handlers.handle_schedule_tool_payload(
            app,
            {"type": "schedule_run_now", "task_id": "task-1", "title": "Runnable"},
        )
    )

    assert app._scheduler_runner.fired == [scheduled]


def test_handle_schedule_list_formats_empty_and_non_empty(monkeypatch) -> None:
    app = ScheduleApp()
    monkeypatch.setattr(
        payloads,
        "format_schedule_list_item",
        lambda item: f"- {item['title']}",
    )

    asyncio.run(
        schedule_handlers.handle_schedule_tool_payload(
            app,
            {"type": "schedule_list", "tasks": []},
        )
    )
    asyncio.run(
        schedule_handlers.handle_schedule_tool_payload(
            app,
            {"type": "schedule_list", "tasks": [{"title": "A"}]},
        )
    )

    assert isinstance(app.messages[0], AppMessage)
    assert "- A" in str(message_contents(app)[1])


def test_show_schedule_manager_focuses_and_schedules_action(monkeypatch) -> None:
    class FakeScheduleManagerScreen:
        def __init__(self, *, store: object) -> None:
            self.store = store

    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.widgets.schedule_manager",
        SimpleNamespace(
            ScheduleAction=object,
            ScheduleManagerScreen=FakeScheduleManagerScreen,
        ),
    )
    app = ScheduleApp()
    action = SimpleNamespace(kind="run_now", task_id="task-1")
    app._execute_schedule_action = object()

    asyncio.run(schedule_handlers.show_schedule_manager(app))

    assert isinstance(app.screens[-1], FakeScheduleManagerScreen)
    assert app.screens[-1].store is app._scheduler_store

    app.callbacks[-1](None)
    assert app._chat_input.focused == 1
    assert app.later == []

    app.callbacks[-1](action)
    assert app._chat_input.focused == 2
    assert app.later == [(app._execute_schedule_action, (action,))]


def test_execute_schedule_action_runs_pause_resume_and_delete(monkeypatch) -> None:
    scheduled = task(title="Managed")
    app = ScheduleApp(DummyStore({"task-1": scheduled}))
    monkeypatch.setattr(
        schedule_handlers,
        "wecom_daemon_claims_scheduled_task",
        lambda *_args: False,
    )

    asyncio.run(
        schedule_handlers.execute_schedule_action(
            app,
            SimpleNamespace(kind="run_now", task_id="task-1"),
        )
    )
    asyncio.run(
        schedule_handlers.execute_schedule_action(
            app,
            SimpleNamespace(kind="pause", task_id="task-1"),
        )
    )
    asyncio.run(
        schedule_handlers.execute_schedule_action(
            app,
            SimpleNamespace(kind="resume", task_id="task-1"),
        )
    )
    asyncio.run(
        schedule_handlers.execute_schedule_action(
            app,
            SimpleNamespace(kind="delete", task_id="task-1"),
        )
    )

    assert app._scheduler_runner.fired == [scheduled]
    assert app._scheduler_store.enabled == [("task-1", False), ("task-1", True)]
    assert app._scheduler_store.deleted == ["task-1"]


def test_execute_schedule_action_reports_missing_and_daemon_claim(monkeypatch) -> None:
    missing_app = ScheduleApp()

    asyncio.run(
        schedule_handlers.execute_schedule_action(
            missing_app,
            SimpleNamespace(kind="run_now", task_id="missing"),
        )
    )

    assert isinstance(missing_app.messages[-1], AppMessage)

    scheduled = task(title="Managed")
    daemon_app = ScheduleApp(DummyStore({"task-1": scheduled}))
    monkeypatch.setattr(
        schedule_handlers,
        "wecom_daemon_claims_scheduled_task",
        lambda *_args: True,
    )

    asyncio.run(
        schedule_handlers.execute_schedule_action(
            daemon_app,
            SimpleNamespace(kind="run_now", task_id="task-1"),
        )
    )

    assert daemon_app._scheduler_runner.fired == []
