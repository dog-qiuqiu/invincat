from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from invincat_cli.app_runtime import scheduled_delivery
from invincat_cli.app_runtime.state import QueuedMessage
from invincat_cli.widgets.message_store import MessageData, MessageType
from invincat_cli.widgets.messages import AppMessage, ErrorMessage


class FakeRunner:
    def __init__(self) -> None:
        self.ticks = 0
        self.finished: list[tuple[str, str, str, str | None]] = []

    async def tick(self) -> None:
        self.ticks += 1

    def finish_run(
        self,
        run_id: str,
        task_id: str,
        *,
        status: str,
        error: str | None,
    ) -> None:
        self.finished.append((run_id, task_id, status, error))


class FakePrompt:
    def __init__(self) -> None:
        self.rejected = 0
        self.cancelled = 0

    def action_select_reject(self) -> None:
        self.rejected += 1

    def action_cancel(self) -> None:
        self.cancelled += 1


class FakeWorker:
    def __init__(self) -> None:
        self.cancelled = 0

    def cancel(self) -> None:
        self.cancelled += 1


class FakeMessageStore:
    def __init__(self) -> None:
        self.messages = [
            MessageData(type=MessageType.USER, content="run"),
            MessageData(type=MessageType.ASSISTANT, content="summary"),
        ]

    def get_all_messages(self) -> list[MessageData]:
        return self.messages


class FakeSchedulerStore:
    def __init__(self) -> None:
        self.tasks: dict[str, object] = {}
        self.runs: dict[str, object] = {}
        self.delivery_updates: list[tuple[str, dict[str, object]]] = []

    def load_task(self, task_id: str) -> object | None:
        return self.tasks.get(task_id)

    def load_run(self, run_id: str) -> object | None:
        return self.runs.get(run_id)

    def update_run_delivery(self, run_id: str, **kwargs: object) -> None:
        self.delivery_updates.append((run_id, kwargs))


class ScheduledApp:
    def __init__(self) -> None:
        self._cwd = "/repo"
        self._scheduler_store = FakeSchedulerStore()
        self._scheduler_runner: FakeRunner | None = FakeRunner()
        self._scheduler_interval_handle = None
        self._active_scheduled_run: tuple[str, str] | None = ("run-1", "task-1")
        self._scheduled_turn_status = "success"
        self._scheduled_turn_error: str | None = None
        self._scheduled_turn_retry_used = True
        self._scheduled_run_message_offset = 0
        self._pending_messages = deque(
            [
                QueuedMessage(
                    text="drop",
                    mode="normal",
                    scheduled_run_id="run-1",
                    scheduled_task_id="task-1",
                ),
                QueuedMessage(text="keep", mode="normal"),
            ]
        )
        self._pending_approval_widget: FakePrompt | None = FakePrompt()
        self._pending_ask_user_widget: FakePrompt | None = FakePrompt()
        self._shell_worker: FakeWorker | None = FakeWorker()
        self._agent_worker: FakeWorker | None = FakeWorker()
        self._shell_running = True
        self._agent_running = True
        self._active_turn_is_planner = True
        self._message_store = FakeMessageStore()
        self._wecom_bridge: object | None = object()
        self.messages: list[object] = []
        self.notifications: list[tuple[str, int | float | None]] = []
        self.intervals: list[tuple[int, object, bool]] = []
        self.timers: list[tuple[int, object]] = []
        self.wecom_payloads: list[dict] = []
        self.flush_result = True
        self.processed_queue = 0

    def notify(self, message: str, *, timeout: int | float | None = None) -> None:
        self.notifications.append((message, timeout))

    def set_interval(self, seconds: int, callback: object, *, pause: bool) -> str:
        self.intervals.append((seconds, callback, pause))
        return "interval-1"

    def set_timer(self, seconds: int, callback: object) -> None:
        self.timers.append((seconds, callback))

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    async def _handle_scheduled_timeout(self, run_id: str, task_id: str) -> None:
        await scheduled_delivery.handle_scheduled_timeout(self, run_id, task_id)

    async def _scheduler_tick(self) -> None:
        await scheduled_delivery.scheduler_tick(self)

    def _wecom_enqueue(self, payload: dict) -> None:
        self.wecom_payloads.append(payload)

    async def _wecom_flush_outbox(self) -> bool:
        return self.flush_result

    async def _wecom_send_request(self, payload: dict) -> dict:
        self.wecom_payloads.append(payload)
        return {"errcode": 0}

    async def _process_next_from_queue(self) -> None:
        self.processed_queue += 1


def task(
    *,
    title: str = "Daily report",
    channels: list[dict[str, str]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        title=title,
        delivery=SimpleNamespace(channels=channels or []),
        report=SimpleNamespace(mode="none"),
        timezone="UTC",
    )


def run(*, finished_at: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        finished_at=finished_at,
        scheduled_for="2026-05-14T08:00:00+00:00",
    )


def message_contents(app: ScheduledApp) -> list[str]:
    return [str(getattr(message, "_content", "")) for message in app.messages]


def test_start_scheduler_configures_runner_and_timers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}

    class FakeFilteredStore:
        def __init__(self, *, db_path: str | None, exclude_task: object) -> None:
            created["db_path"] = db_path
            created["exclude_task"] = exclude_task

    class FakeSchedulerRunner:
        def __init__(self, store: object, **kwargs: object) -> None:
            created["store"] = store
            created["kwargs"] = kwargs

    monkeypatch.setattr(
        "invincat_cli.scheduler.store.FilteredSchedulerStore",
        FakeFilteredStore,
    )
    monkeypatch.setattr(
        "invincat_cli.scheduler.runner.SchedulerRunner",
        FakeSchedulerRunner,
    )
    app = ScheduledApp()
    app._scheduler_store._db_path = "scheduler.db"

    scheduled_delivery.start_scheduler(app)

    assert isinstance(app._scheduler_runner, FakeSchedulerRunner)
    assert created["db_path"] == "scheduler.db"
    assert app.intervals == [(60, app._scheduler_tick, False)]
    assert app.timers == [(3, app._scheduler_tick)]


def test_scheduler_tick_runs_existing_runner() -> None:
    app = ScheduledApp()

    asyncio.run(scheduled_delivery.scheduler_tick(app))

    assert app._scheduler_runner is not None
    assert app._scheduler_runner.ticks == 1


def test_scheduler_tick_noops_without_runner() -> None:
    app = ScheduledApp()
    app._scheduler_runner = None

    asyncio.run(scheduled_delivery.scheduler_tick(app))


def test_handle_scheduled_timeout_cancels_and_delivers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ScheduledApp()
    delivered: list[tuple[str, str, str, str | None]] = []

    async def deliver(
        _app: ScheduledApp,
        *,
        task_id: str,
        run_id: str,
        status: str,
        error: str | None,
    ) -> None:
        delivered.append((task_id, run_id, status, error))

    monkeypatch.setattr(
        scheduled_delivery, "deliver_scheduled_result_to_wecom", deliver
    )

    asyncio.run(scheduled_delivery.handle_scheduled_timeout(app, "run-1", "task-1"))

    assert app._active_scheduled_run is None
    assert delivered == [("task-1", "run-1", "timeout", "Scheduled task timed out")]


def test_cancel_timed_out_scheduled_turn_rejects_prompts_and_workers() -> None:
    app = ScheduledApp()
    approval = app._pending_approval_widget
    ask = app._pending_ask_user_widget
    shell_worker = app._shell_worker
    agent_worker = app._agent_worker

    scheduled_delivery.cancel_timed_out_scheduled_turn(app, "run-1", "task-1")

    assert [message.text for message in app._pending_messages] == ["keep"]
    assert approval is not None and approval.rejected == 1
    assert ask is not None and ask.cancelled == 1
    assert shell_worker is not None and shell_worker.cancelled == 1
    assert agent_worker is not None and agent_worker.cancelled == 1
    assert app._shell_running is False
    assert app._agent_running is False
    assert app._active_turn_is_planner is False
    assert app._active_scheduled_run is None
    assert app._scheduled_turn_status == "timeout"
    assert app._scheduled_turn_error == "Scheduled task timed out"


def test_cancel_timed_out_scheduled_turn_only_dequeues_when_run_does_not_match() -> (
    None
):
    app = ScheduledApp()

    scheduled_delivery.cancel_timed_out_scheduled_turn(app, "other-run", "task-1")

    assert app._active_scheduled_run == ("run-1", "task-1")
    assert app._shell_running is True
    assert [message.text for message in app._pending_messages] == ["drop", "keep"]


def test_complete_active_scheduled_run_resets_state_without_runner() -> None:
    app = ScheduledApp()
    app._scheduler_runner = None

    asyncio.run(scheduled_delivery.complete_active_scheduled_run(app))

    assert app._active_scheduled_run is None
    assert app._scheduled_turn_error is None
    assert app._scheduled_turn_retry_used is False


def test_complete_active_scheduled_run_noops_without_active_run() -> None:
    app = ScheduledApp()
    app._active_scheduled_run = None

    asyncio.run(scheduled_delivery.complete_active_scheduled_run(app))

    assert app._scheduled_turn_retry_used is True


def test_complete_active_scheduled_run_delivers_finishes_and_resets() -> None:
    app = ScheduledApp()
    app._scheduler_store.runs["run-1"] = run()
    delivered: list[tuple[str, str, str, str | None]] = []

    async def deliver(
        _app: ScheduledApp,
        *,
        task_id: str,
        run_id: str,
        status: str,
        error: str | None,
    ) -> None:
        delivered.append((task_id, run_id, status, error))

    asyncio.run(
        scheduled_delivery.complete_active_scheduled_run(app, deliver_result=deliver)
    )

    assert delivered == [("task-1", "run-1", "success", None)]
    assert app._scheduler_runner is not None
    assert app._scheduler_runner.finished == [("run-1", "task-1", "success", None)]
    assert app._scheduled_turn_error is None
    assert app._scheduled_turn_retry_used is False


def test_deliver_active_scheduled_result_skips_finished_run() -> None:
    app = ScheduledApp()
    app._scheduler_store.runs["run-1"] = run(finished_at="done")
    delivered: list[str] = []

    async def deliver(**_kwargs: object) -> None:
        delivered.append("called")

    asyncio.run(
        scheduled_delivery.deliver_active_scheduled_result_if_needed(
            app,
            run_id="run-1",
            task_id="task-1",
            deliver_result=deliver,
        )
    )

    assert delivered == []


def test_deliver_active_scheduled_result_swallows_delivery_error() -> None:
    app = ScheduledApp()
    app._scheduler_store.runs["run-1"] = run()

    async def fail_delivery(**_kwargs: object) -> None:
        raise RuntimeError("delivery failed")

    asyncio.run(
        scheduled_delivery.deliver_active_scheduled_result_if_needed(
            app,
            run_id="run-1",
            task_id="task-1",
            deliver_result=fail_delivery,
        )
    )


def test_finish_scheduled_run_swallows_runner_error() -> None:
    class BrokenRunner:
        def finish_run(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("finish failed")

    app = ScheduledApp()
    app._scheduler_runner = BrokenRunner()  # type: ignore[assignment]

    scheduled_delivery.finish_scheduled_run(app, run_id="run-1", task_id="task-1")


def test_deliver_scheduled_result_skips_missing_task_or_run() -> None:
    app = ScheduledApp()

    asyncio.run(
        scheduled_delivery.deliver_scheduled_result_to_wecom(
            app,
            task_id="missing",
            run_id="missing",
            status="success",
            error=None,
        )
    )

    assert app._scheduler_store.delivery_updates == []


def test_deliver_scheduled_result_records_none_without_wecom_channel() -> None:
    app = ScheduledApp()
    app._scheduler_store.tasks["task-1"] = task(channels=[])
    app._scheduler_store.runs["run-1"] = run()

    asyncio.run(
        scheduled_delivery.deliver_scheduled_result_to_wecom(
            app,
            task_id="task-1",
            run_id="run-1",
            status="success",
            error=None,
        )
    )

    assert app._scheduler_store.delivery_updates == [
        ("run-1", {"status": "none", "error": None, "attempts_delta": 0})
    ]


def test_deliver_scheduled_result_reports_missing_chatid() -> None:
    app = ScheduledApp()
    app._scheduler_store.tasks["task-1"] = task(channels=[{"type": "wecom"}])
    app._scheduler_store.runs["run-1"] = run()

    asyncio.run(
        scheduled_delivery.deliver_scheduled_result_to_wecom(
            app,
            task_id="task-1",
            run_id="run-1",
            status="failed",
            error="boom",
        )
    )

    assert app._scheduler_store.delivery_updates == [
        ("run-1", {"status": "failed", "error": "missing chatid"})
    ]
    assert isinstance(app.messages[-1], ErrorMessage)


def test_send_scheduled_wecom_text_handles_offline_and_queued() -> None:
    app = ScheduledApp()
    app._wecom_bridge = None

    sent = asyncio.run(
        scheduled_delivery.send_scheduled_wecom_text(
            app,
            chatid="chat-1",
            content="hello",
            run_id="run-1",
        )
    )

    assert sent is False
    assert app._scheduler_store.delivery_updates[-1][1]["status"] == "failed"
    assert isinstance(app.messages[-1], ErrorMessage)

    app._wecom_bridge = object()
    app.flush_result = False

    sent = asyncio.run(
        scheduled_delivery.send_scheduled_wecom_text(
            app,
            chatid="chat-1",
            content="hello",
            run_id="run-1",
        )
    )

    assert sent is False
    assert app._scheduler_store.delivery_updates[-1][1]["status"] == "queued"
    assert isinstance(app.messages[-1], AppMessage)


def test_send_scheduled_wecom_text_marks_success() -> None:
    app = ScheduledApp()

    sent = asyncio.run(
        scheduled_delivery.send_scheduled_wecom_text(
            app,
            chatid="chat-1",
            content="hello",
            run_id="run-1",
        )
    )

    assert sent is True
    assert app.wecom_payloads[-1]["body"]["chatid"] == "chat-1"
    update = app._scheduler_store.delivery_updates[-1][1]
    assert update["status"] == "success"
    assert update["error"] is None
    assert "delivered_at" in update


def test_deliver_scheduled_result_sends_text_for_wecom_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ScheduledApp()
    app._scheduler_store.tasks["task-1"] = task(
        channels=[{"type": "wecom", "chatid": "chat-1"}]
    )
    app._scheduler_store.runs["run-1"] = run()
    texts: list[tuple[str, str, str]] = []

    async def send_text(
        _app: ScheduledApp,
        *,
        chatid: str,
        content: str,
        run_id: str,
    ) -> bool:
        texts.append((chatid, content, run_id))
        return True

    monkeypatch.setattr(scheduled_delivery, "send_scheduled_wecom_text", send_text)

    asyncio.run(
        scheduled_delivery.deliver_scheduled_result_to_wecom(
            app,
            task_id="task-1",
            run_id="run-1",
            status="success",
            error=None,
        )
    )

    assert texts
    assert texts[0][0] == "chat-1"
    assert "Daily report" in texts[0][1]
    assert "summary" in texts[0][1]


def test_deliver_scheduled_result_stops_when_text_not_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ScheduledApp()
    app._scheduler_store.tasks["task-1"] = task(
        channels=[{"type": "wecom", "chatid": "chat-1"}]
    )
    app._scheduler_store.runs["run-1"] = run()
    reports: list[str | None] = []

    async def send_text(*_args: object, **_kwargs: object) -> bool:
        return False

    async def send_report(*_args: object, **kwargs: object) -> None:
        reports.append(kwargs["report_path"])  # type: ignore[index]

    monkeypatch.setattr(scheduled_delivery, "send_scheduled_wecom_text", send_text)
    monkeypatch.setattr(
        scheduled_delivery,
        "send_scheduled_wecom_report_file",
        send_report,
    )

    asyncio.run(
        scheduled_delivery.deliver_scheduled_result_to_wecom(
            app,
            task_id="task-1",
            run_id="run-1",
            status="success",
            error=None,
        )
    )

    assert reports == []


def test_deliver_scheduled_result_sends_report_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.scheduler import wecom_delivery

    app = ScheduledApp()
    app._scheduler_store.tasks["task-1"] = task(
        channels=[{"type": "wecom", "chatid": "chat-1"}]
    )
    app._scheduler_store.runs["run-1"] = run()
    reports: list[tuple[str, str]] = []

    async def send_text(*_args: object, **_kwargs: object) -> bool:
        return True

    async def send_report(
        _app: ScheduledApp,
        *,
        chatid: str,
        report_path: str | None,
    ) -> None:
        assert report_path is not None
        reports.append((chatid, report_path))

    monkeypatch.setattr(scheduled_delivery, "send_scheduled_wecom_text", send_text)
    monkeypatch.setattr(
        scheduled_delivery,
        "send_scheduled_wecom_report_file",
        send_report,
    )
    monkeypatch.setattr(
        wecom_delivery,
        "scheduled_report_path_for_wecom",
        lambda _task, _run: "/tmp/report.md",
    )
    monkeypatch.setattr(
        wecom_delivery,
        "should_send_scheduled_report_file",
        lambda *, status, report_path: True,
    )

    asyncio.run(
        scheduled_delivery.deliver_scheduled_result_to_wecom(
            app,
            task_id="task-1",
            run_id="run-1",
            status="success",
            error=None,
        )
    )

    assert reports == [("chat-1", "/tmp/report.md")]


def test_deliver_scheduled_result_marks_failed_when_report_send_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.scheduler import wecom_delivery

    app = ScheduledApp()
    app._scheduler_store.tasks["task-1"] = task(
        channels=[{"type": "wecom", "chatid": "chat-1"}]
    )
    app._scheduler_store.runs["run-1"] = run()

    async def send_text(*_args: object, **_kwargs: object) -> bool:
        return True

    async def fail_report(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("upload failed")

    monkeypatch.setattr(scheduled_delivery, "send_scheduled_wecom_text", send_text)
    monkeypatch.setattr(
        scheduled_delivery,
        "send_scheduled_wecom_report_file",
        fail_report,
    )
    monkeypatch.setattr(
        wecom_delivery,
        "scheduled_report_path_for_wecom",
        lambda _task, _run: "/tmp/report.md",
    )
    monkeypatch.setattr(
        wecom_delivery,
        "should_send_scheduled_report_file",
        lambda *, status, report_path: True,
    )

    asyncio.run(
        scheduled_delivery.deliver_scheduled_result_to_wecom(
            app,
            task_id="task-1",
            run_id="run-1",
            status="success",
            error=None,
        )
    )

    assert app._scheduler_store.delivery_updates[-1] == (
        "run-1",
        {"status": "failed", "error": "upload failed"},
    )
    assert isinstance(app.messages[-1], ErrorMessage)


def test_send_scheduled_wecom_report_file_handles_none_offline_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.wecom import media, protocol

    app = ScheduledApp()

    asyncio.run(
        scheduled_delivery.send_scheduled_wecom_report_file(
            app,
            chatid="chat-1",
            report_path=None,
        )
    )
    assert app.wecom_payloads == []

    app._wecom_bridge = None
    asyncio.run(
        scheduled_delivery.send_scheduled_wecom_report_file(
            app,
            chatid="chat-1",
            report_path="/tmp/report.md",
        )
    )
    assert isinstance(app.messages[-1], ErrorMessage)

    uploaded: list[Path] = []

    async def upload(path: Path, *, send_request: object) -> str:
        uploaded.append(path)
        assert send_request == app._wecom_send_request
        return "media-1"

    monkeypatch.setattr(media, "upload_wecom_outbound_media", upload)
    monkeypatch.setattr(
        protocol,
        "build_wecom_file_frame_for_chat",
        lambda chatid, media_id: {"chatid": chatid, "media_id": media_id},
    )
    app._wecom_bridge = object()

    asyncio.run(
        scheduled_delivery.send_scheduled_wecom_report_file(
            app,
            chatid="chat-1",
            report_path="/tmp/report.md",
        )
    )

    assert uploaded == [Path("/tmp/report.md")]
    assert app.wecom_payloads[-1] == {"chatid": "chat-1", "media_id": "media-1"}


def test_active_scheduled_wecom_chat_id_handles_absent_and_present_task() -> None:
    app = ScheduledApp()
    app._active_scheduled_run = None
    assert scheduled_delivery.active_scheduled_wecom_chat_id(app) is None

    app._active_scheduled_run = ("run-1", "missing")
    assert scheduled_delivery.active_scheduled_wecom_chat_id(app) is None

    app._scheduler_store.tasks["task-1"] = task(
        channels=[{"type": "wecom", "chatid": "chat-1"}]
    )
    app._active_scheduled_run = ("run-1", "task-1")
    assert scheduled_delivery.active_scheduled_wecom_chat_id(app) == "chat-1"


def test_send_scheduled_wecom_file_request_handles_target_offline_and_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from invincat_cli.wecom import media, protocol

    app = ScheduledApp()
    app._active_scheduled_run = None
    with pytest.raises(RuntimeError, match="no WeCom delivery target"):
        asyncio.run(
            scheduled_delivery.send_scheduled_wecom_file_request(
                app,
                {"path": "/tmp/report.md"},
            )
        )

    app._scheduler_store.tasks["task-1"] = task(
        channels=[{"type": "wecom", "chatid": "chat-1"}]
    )
    app._active_scheduled_run = ("run-1", "task-1")
    app._wecom_bridge = None
    with pytest.raises(RuntimeError, match="offline"):
        asyncio.run(
            scheduled_delivery.send_scheduled_wecom_file_request(
                app,
                {"path": "/tmp/report.md"},
            )
        )

    uploaded: list[Path] = []

    async def upload(path: Path, *, send_request: object) -> str:
        uploaded.append(path)
        assert send_request == app._wecom_send_request
        return "media-1"

    monkeypatch.setattr(media, "upload_wecom_outbound_media", upload)
    monkeypatch.setattr(
        protocol,
        "build_wecom_file_frame_for_chat",
        lambda chatid, media_id: {"chatid": chatid, "media_id": media_id},
    )
    app._wecom_bridge = object()
    report_path = tmp_path / "report.md"
    report_path.write_text("report", encoding="utf-8")
    app._cwd = str(tmp_path)

    asyncio.run(
        scheduled_delivery.send_scheduled_wecom_file_request(
            app,
            {"path": str(report_path)},
        )
    )

    assert uploaded == [report_path]
    assert app.wecom_payloads[-1] == {"chatid": "chat-1", "media_id": "media-1"}


def test_inject_scheduled_message_processes_queue_when_idle() -> None:
    app = ScheduledApp()
    app._agent_running = False
    app._shell_running = False
    app._scheduler_store.tasks["task-1"] = task(title="Sync")

    asyncio.run(
        scheduled_delivery.inject_scheduled_message(
            app,
            task_id="task-1",
            run_id="run-1",
            prompt="do it",
        )
    )

    assert isinstance(app.messages[-1], AppMessage)
    assert app._pending_messages[-1].text == "do it"
    assert app._pending_messages[-1].scheduled_run_id == "run-1"
    assert app.processed_queue == 1
