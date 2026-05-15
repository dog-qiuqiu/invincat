from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from invincat_cli.app_runtime import wecom_handlers
from invincat_cli.widgets.messages import AppMessage, ErrorMessage, UserMessage


class FakeTask:
    def __init__(self, *, done: bool = False) -> None:
        self._done = done
        self.cancelled = False

    def done(self) -> bool:
        return self._done

    def cancel(self) -> None:
        self.cancelled = True
        self._done = True

    def __await__(self):
        async def _wait() -> None:
            return None

        return _wait().__await__()


class FakeBridge:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, object]] = []
        self.flushed = False
        self.requests: list[tuple[dict[str, object], float]] = []
        self.stopped = False

    def enqueue(self, payload: dict[str, object]) -> None:
        self.enqueued.append(payload)

    async def flush_outbox(self) -> bool:
        self.flushed = True
        return True

    async def send_request(
        self,
        payload: dict[str, object],
        *,
        timeout: float,
    ) -> dict[str, object]:
        self.requests.append((payload, timeout))
        return {"ok": True}

    def stop(self) -> None:
        self.stopped = True


class WeComApp:
    def __init__(self) -> None:
        self._wecom_task: object | None = None
        self._wecom_bridge: object | None = None
        self._auto_approve = False
        self._exit = False
        self._cwd = Path("/tmp/project")
        self._wecom_lock = asyncio.Lock()
        self._current_wecom_inbound_frame = None
        self._connecting = False
        self._thread_switching = False
        self._model_switching = False
        self._agent_running = False
        self._shell_running = False
        self._shell_worker = None
        self._agent_worker = None
        self._active_turn_is_planner = True
        self._message_store = SimpleNamespace(get_all_messages=lambda: [])
        self.messages: list[object] = []
        self.auto_approve_enabled = False
        self.handled_frames: list[dict[str, object]] = []
        self.sent_messages: list[tuple[str, object, object]] = []

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    def _on_auto_approve_enabled(self) -> None:
        self.auto_approve_enabled = True
        self._auto_approve = True

    async def _run_wecombot_bridge(self) -> None:
        return None

    async def _wecom_handle_inbound_message(self, *, frame: dict[str, object]) -> None:
        self.handled_frames.append(frame)

    def _wecom_enqueue(self, payload: dict[str, object]) -> None:
        wecom_handlers.wecom_enqueue(self, payload)

    async def _wecom_flush_outbox(self) -> bool:
        return await wecom_handlers.wecom_flush_outbox(self)

    async def _wecom_send_request(
        self,
        payload: dict[str, object],
        *,
        timeout: float = 30.0,
    ) -> dict[str, object]:
        return await wecom_handlers.wecom_send_request(self, payload, timeout=timeout)


def test_handle_wecombot_command_starts_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    app = WeComApp()
    created: list[object] = []

    def create_task(coro: object) -> FakeTask:
        created.append(coro)
        if inspect_close := getattr(coro, "close", None):
            inspect_close()
        return FakeTask(done=False)

    monkeypatch.setattr(wecom_handlers.asyncio, "create_task", create_task)

    asyncio.run(
        wecom_handlers.handle_wecombot_command(
            app,
            "/wecombot-start",
            action="start",
        )
    )

    assert isinstance(app.messages[0], UserMessage)
    assert isinstance(app.messages[-1], AppMessage)
    assert app.auto_approve_enabled is True
    assert isinstance(app._wecom_task, FakeTask)
    assert created


def test_handle_wecombot_command_stops_running_bridge() -> None:
    app = WeComApp()
    bridge = FakeBridge()
    task = FakeTask(done=False)
    app._wecom_bridge = bridge
    app._wecom_task = task

    asyncio.run(
        wecom_handlers.handle_wecombot_command(
            app,
            "/wecombot-stop",
            action="stop",
        )
    )

    assert bridge.stopped is True
    assert task.cancelled is True
    assert app._wecom_bridge is None
    assert app._wecom_task is None
    assert isinstance(app.messages[-1], AppMessage)


def test_run_wecombot_bridge_reports_missing_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = WeComApp()
    monkeypatch.delenv("WECOM_BOT_ID", raising=False)
    monkeypatch.delenv("WECOM_BOT_SECRET", raising=False)

    asyncio.run(wecom_handlers.run_wecombot_bridge(app))

    assert isinstance(app.messages[-1], ErrorMessage)
    assert "WECOM_BOT_ID" in app.messages[-1]._content


def test_run_wecombot_bridge_wires_callbacks_and_clears_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = WeComApp()
    monkeypatch.setenv("WECOM_BOT_ID", "bot")
    monkeypatch.setenv("WECOM_BOT_SECRET", "secret")

    class Bridge:
        def __init__(self, *, on_status, on_error, on_message, should_exit):  # noqa: ANN001
            self.on_status = on_status
            self.on_error = on_error
            self.on_message = on_message
            self.should_exit = should_exit

        async def run(self, **kwargs: object) -> None:
            assert kwargs["bot_id"] == "bot"
            assert kwargs["secret"] == "secret"
            assert self.should_exit() is False
            await self.on_status("connected")
            await self.on_error("warning")
            await self.on_message({"id": "frame-1"})

    monkeypatch.setattr(wecom_handlers, "WeComBridge", Bridge)

    asyncio.run(wecom_handlers.run_wecombot_bridge(app))

    assert isinstance(app.messages[0], AppMessage)
    assert isinstance(app.messages[1], ErrorMessage)
    assert app.handled_frames == [{"id": "frame-1"}]
    assert app._wecom_bridge is None


def test_wecom_handle_inbound_message_delegates_to_responder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = WeComApp()
    handled: list[dict[str, object]] = []
    built_inputs: list[tuple[dict[str, object], Path]] = []
    turns: list[tuple[str, dict[str, object]]] = []

    async def build_input(frame: dict[str, object], *, cwd: Path) -> str:
        built_inputs.append((frame, cwd))
        return "agent input"

    async def run_turn(
        app_arg: object,
        text: str,
        *,
        inbound_frame: dict[str, object],
        on_content,
    ) -> str:
        assert app_arg is app
        await on_content("delta")
        turns.append((text, inbound_frame))
        return "answer"

    class Responder:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def handle(self, frame: dict[str, object]) -> None:
            text = await self.kwargs["build_agent_input"](frame)
            assert text == "agent input"
            await self.kwargs["run_turn"](
                text,
                frame,
                lambda _chunk: asyncio.sleep(0),
            )
            handled.append(frame)

    monkeypatch.setattr(
        wecom_handlers,
        "build_wecom_agent_input_with_media_downloads",
        build_input,
    )
    monkeypatch.setattr(wecom_handlers, "process_wecom_message_via_cli", run_turn)
    monkeypatch.setattr(
        wecom_handlers,
        "create_wecom_message_responder",
        lambda **kwargs: Responder(**kwargs),
    )

    asyncio.run(
        wecom_handlers.wecom_handle_inbound_message(app, frame={"id": "inbound"})
    )

    assert handled == [{"id": "inbound"}]
    assert built_inputs == [({"id": "inbound"}, Path("/tmp/project"))]
    assert turns == [("agent input", {"id": "inbound"})]


def test_wecom_enqueue_flush_and_send_request_respect_bridge_availability() -> None:
    app = WeComApp()

    wecom_handlers.wecom_enqueue(app, {"type": "text"})
    assert asyncio.run(wecom_handlers.wecom_flush_outbox(app)) is False
    with pytest.raises(RuntimeError, match="offline"):
        asyncio.run(wecom_handlers.wecom_send_request(app, {"type": "ping"}))

    bridge = FakeBridge()
    app._wecom_bridge = bridge

    wecom_handlers.wecom_enqueue(app, {"type": "text"})
    flushed = asyncio.run(wecom_handlers.wecom_flush_outbox(app))
    response = asyncio.run(
        wecom_handlers.wecom_send_request(
            app,
            {"type": "ping"},
            timeout=3.0,
        )
    )

    assert bridge.enqueued == [{"type": "text"}]
    assert flushed is True
    assert response == {"ok": True}
    assert bridge.requests == [({"type": "ping"}, 3.0)]


def test_process_wecom_message_via_cli_uses_turn_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = WeComApp()
    calls: list[tuple[str, dict[str, object]]] = []
    handled_messages: list[tuple[str, object, object]] = []

    async def handle_message(
        app_arg: object,
        message: str,
        *,
        on_text_delta,
        on_wecom_file_request,
    ) -> None:
        handled_messages.append((message, on_text_delta, on_wecom_file_request))
        assert app_arg is app

    class Runner:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def run(
            self,
            text: str,
            *,
            inbound_frame: dict[str, object],
        ) -> str:
            calls.append((text, inbound_frame))
            self.kwargs["enter_turn_context"]()
            assert app._current_wecom_inbound_frame == inbound_frame
            await self.kwargs["handle_user_message"](
                "from wecom",
                lambda _kind, _chunk: asyncio.sleep(0),
                lambda _payload: asyncio.sleep(0),
            )
            self.kwargs["exit_turn_context"]()
            return "answer"

    monkeypatch.setattr(wecom_handlers, "handle_user_message", handle_message)
    monkeypatch.setattr(wecom_handlers, "WeComTurnRunner", Runner)

    result = asyncio.run(
        wecom_handlers.process_wecom_message_via_cli(
            app,
            "hello",
            inbound_frame={"id": "frame"},
        )
    )

    assert result == "answer"
    assert calls == [("hello", {"id": "frame"})]
    assert handled_messages[0][0] == "from wecom"
    assert app._current_wecom_inbound_frame is None


def test_cancel_timed_out_turn_cancels_active_workers() -> None:
    app = WeComApp()
    shell_worker = FakeTask(done=False)
    agent_worker = FakeTask(done=False)
    app._shell_worker = shell_worker
    app._agent_worker = agent_worker
    app._shell_running = True
    app._agent_running = True

    wecom_handlers.cancel_timed_out_turn(app)

    assert shell_worker.cancelled is True
    assert agent_worker.cancelled is True
    assert app._shell_running is False
    assert app._agent_running is False
    assert app._shell_worker is None
    assert app._agent_worker is None
    assert app._active_turn_is_planner is False
