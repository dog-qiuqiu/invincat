from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from invincat_cli.app_runtime import agent_handlers
from invincat_cli.app_runtime.agent import AgentTurnRequest
from invincat_cli.core.session_stats import SessionStats
from invincat_cli.goal_mode.models import GoalState
from invincat_cli.widgets.messages import AppMessage, ErrorMessage


class FakeAdapter:
    def __init__(self) -> None:
        self.finalized_errors: list[str] = []

    def finalize_pending_tools_with_error(self, error: str) -> None:
        self.finalized_errors.append(error)


class FakeChat:
    def __init__(self) -> None:
        self.anchored = False

    def anchor(self) -> None:
        self.anchored = True


class FakeChatInput:
    def __init__(self) -> None:
        self.cursor_states: list[bool] = []

    def set_cursor_active(self, *, active: bool) -> None:
        self.cursor_states.append(active)


class FakeSchedulerRunner:
    def __init__(self) -> None:
        self.finished: list[tuple[str, str, str, str]] = []

    def finish_run(
        self,
        run_id: str,
        task_id: str,
        *,
        status: str,
        error: str,
    ) -> None:
        self.finished.append((run_id, task_id, status, error))


class FakeServerProc:
    def __init__(self, tail: str, *, fail: bool = False) -> None:
        self.tail = tail
        self.fail = fail
        self.max_chars: list[int] = []

    def read_log_tail(self, *, max_chars: int) -> str:
        self.max_chars.append(max_chars)
        if self.fail:
            raise RuntimeError("log unavailable")
        return self.tail


class FakeWorker:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class AgentHandlersApp:
    def __init__(self) -> None:
        self._processing_pending = False
        self._active_scheduled_run: tuple[str, str] | None = ("run-1", "task-1")
        self._agent: object | None = object()
        self._planner_agent = object()
        self._planner_thread_id = "planner-thread"
        self._ui_adapter: FakeAdapter | None = FakeAdapter()
        self._session_state: SimpleNamespace | None = SimpleNamespace(
            thread_id="thread-1",
            goal_mode=False,
            goal=None,
        )
        self._agent_generation = 0
        self._agent_running = False
        self._agent_worker: object | None = None
        self._active_turn_is_planner = False
        self._chat_input: FakeChatInput | None = FakeChatInput()
        self._assistant_id = "assistant-1"
        self._backend = object()
        self._image_tracker = object()
        self._sandbox_type = "workspace-write"
        self._model_override = "model-a"
        self._model_params_override = {"temperature": 0}
        self._memory_model_override = "memory-model"
        self._memory_model_params_override = {"limit": 2}
        self._scheduled_turn_retry_used = False
        self._scheduled_turn_status: str | None = None
        self._scheduled_turn_error: str | None = None
        self._server_proc: FakeServerProc | None = None
        self._scheduler_runner: FakeSchedulerRunner | None = FakeSchedulerRunner()
        self._inflight_turn_stats: SessionStats | None = None
        self._inflight_turn_start: float | None = None
        self._session_stats = SessionStats()
        self._tokens_approximate = True
        self._shell_running = False
        self.chat = FakeChat()
        self.messages: list[object] = []
        self.run_requests: list[AgentTurnRequest] = []
        self.workers: list[tuple[object, bool]] = []
        self.spinners: list[str | None] = []
        self.tokens_shown: list[bool] = []
        self.deferred_drained = 0
        self.deferred_error: Exception | None = None
        self.stale_generations: list[int] = []
        self.auto_offload_calls = 0
        self.auto_offload_error: Exception | None = None
        self.memory_update_calls = 0
        self.scheduler_drain_calls = 0
        self.queue_process_calls = 0
        self.cleanup_generations: list[int] = []
        self.exception_decisions: list[BaseException] = []
        self.schedule_payloads: list[object] = []

    def query_one(self, selector: str, *_args: object) -> object:
        if selector == "#chat":
            return self.chat
        raise LookupError(selector)

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    def _finish_active_scheduled_run_as_failed(self, error: str) -> None:
        agent_handlers.finish_active_scheduled_run_as_failed(self, error)

    def _run_agent_task(self, request: AgentTurnRequest) -> object:
        self.run_requests.append(request)

        async def noop() -> None:
            return None

        return noop()

    def run_worker(self, coroutine: object, *, exclusive: bool) -> str:
        self.workers.append((coroutine, exclusive))
        close = getattr(coroutine, "close", None)
        if close is not None:
            close()
        return "worker-1"

    async def _handle_agent_task_exception(self, exc: BaseException) -> bool:
        self.exception_decisions.append(exc)
        return await agent_handlers.handle_agent_task_exception(self, exc)

    async def _cleanup_agent_task(self, *, generation: int = 0) -> None:
        self.cleanup_generations.append(generation)

    async def _set_spinner(self, value: str | None) -> None:
        self.spinners.append(value)

    def _show_tokens(self, *, approximate: bool) -> None:
        self.tokens_shown.append(approximate)

    async def _maybe_drain_deferred(self) -> None:
        self.deferred_drained += 1
        if self.deferred_error is not None:
            raise self.deferred_error

    def _handle_stale_agent_cleanup(self, *, generation: int) -> None:
        self.stale_generations.append(generation)
        agent_handlers.handle_stale_agent_cleanup(self, generation=generation)

    async def _run_post_agent_cleanup_side_effects(self) -> None:
        await agent_handlers.run_post_agent_cleanup_side_effects(self)

    async def _maybe_auto_offload(self) -> None:
        self.auto_offload_calls += 1
        if self.auto_offload_error is not None:
            raise self.auto_offload_error

    async def _maybe_notify_memory_update(self) -> None:
        self.memory_update_calls += 1

    async def _drain_scheduler_if_idle(self) -> None:
        self.scheduler_drain_calls += 1

    async def _process_next_from_queue(self) -> None:
        self.queue_process_calls += 1

    async def _handle_schedule_tool_payload(self, payload: object) -> None:
        self.schedule_payloads.append(payload)


def message_contents(app: AgentHandlersApp) -> list[str]:
    return [str(getattr(message, "_content", "")) for message in app.messages]


def test_send_to_agent_reports_unconfigured_runtime_and_finishes_schedule() -> None:
    app = AgentHandlersApp()
    app._agent = None
    app._processing_pending = True

    accepted = asyncio.run(agent_handlers.send_to_agent(app, "hello"))

    assert accepted is False
    assert app.chat.anchored is True
    assert app._active_scheduled_run is None
    assert app._scheduler_runner is not None
    assert app._scheduler_runner.finished == [
        ("run-1", "task-1", "failed", "Agent not available")
    ]
    assert isinstance(app.messages[-1], AppMessage)
    assert "not configured" in message_contents(app)[-1]


def test_execute_watchdog_timeout_cancels_turn_and_drains_queue() -> None:
    app = AgentHandlersApp()
    worker = FakeWorker()
    app._agent_running = True
    app._agent_worker = worker
    app._active_turn_is_planner = True

    asyncio.run(agent_handlers.handle_execute_watchdog_timeout(app, "call-1"))

    assert worker.cancelled is True
    assert app._agent_running is False
    assert app._agent_worker is None
    assert app._active_turn_is_planner is False
    assert app.spinners == [None]
    assert app.tokens_shown == [True]
    assert app.queue_process_calls == 1
    assert app._chat_input is not None
    assert app._chat_input.cursor_states == [True]
    assert app._active_scheduled_run is None
    assert app._scheduler_runner is not None
    assert app._scheduler_runner.finished == [
        ("run-1", "task-1", "failed", "execute tool timed out: call-1")
    ]


def test_send_to_agent_starts_worker_with_planner_request() -> None:
    app = AgentHandlersApp()
    on_text_delta = object()
    on_wecom_file_request = object()
    post_turn_hook = object()

    accepted = asyncio.run(
        agent_handlers.send_to_agent(
            app,
            "plan this",
            message_kwargs={"additional_kwargs": {"mode": "plan"}},
            agent_override=app._planner_agent,
            thread_id_override="planner-thread",
            post_turn_hook=post_turn_hook,
            on_text_delta=on_text_delta,
            on_wecom_file_request=on_wecom_file_request,
        )
    )

    assert accepted is True
    assert app._active_scheduled_run is None
    assert app._agent_generation == 1
    assert app._agent_running is True
    assert app._active_turn_is_planner is True
    assert app._chat_input is not None
    assert app._chat_input.cursor_states == [False]
    assert app._agent_worker == "worker-1"
    assert len(app.workers) == 1
    assert app.workers[0][1] is False
    request = app.run_requests[0]
    assert request.message == "plan this"
    assert request.message_kwargs == {"additional_kwargs": {"mode": "plan"}}
    assert request.generation == 1
    assert request.agent_override is app._planner_agent
    assert request.thread_id_override == "planner-thread"
    assert request.post_turn_hook is post_turn_hook
    assert request.on_text_delta is on_text_delta
    assert request.on_wecom_file_request is on_wecom_file_request


def test_send_to_agent_preserves_planner_turn_with_goal_context() -> None:
    app = AgentHandlersApp()
    assert app._session_state is not None
    app._session_state.goal = GoalState.create(
        objective="Ship goal mode",
        thread_id="thread-1",
    )
    app._session_state.goal_mode = True

    accepted = asyncio.run(
        agent_handlers.send_to_agent(
            app,
            "plan next step",
            agent_override=app._planner_agent,
            thread_id_override="planner-thread",
        )
    )

    assert accepted is True
    assert app._active_turn_is_planner is True
    request = app.run_requests[0]
    assert "<active_goal>" in request.message
    assert "Ship goal mode" in request.message
    assert "User message:\nplan next step" in request.message
    assert request.generation == 1
    assert request.agent_override is app._planner_agent
    assert request.thread_id_override == "planner-thread"
    assert request.message_kwargs is None
    assert request.post_turn_hook is None
    assert request.on_text_delta is None
    assert request.on_wecom_file_request is None


def test_handle_agent_task_exception_retries_scheduled_timeout() -> None:
    app = AgentHandlersApp()

    retry = asyncio.run(
        agent_handlers.handle_agent_task_exception(app, TimeoutError("timed out"))
    )

    assert retry is True
    assert app._scheduled_turn_retry_used is True
    assert app._scheduled_turn_status is None
    assert app._scheduled_turn_error is None
    assert isinstance(app.messages[-1], AppMessage)
    assert "retrying once" in message_contents(app)[-1]
    assert app._ui_adapter is not None
    assert "TimeoutError: timed out" in app._ui_adapter.finalized_errors[-1]


def test_handle_agent_task_exception_marks_failure_and_includes_server_tail() -> None:
    app = AgentHandlersApp()
    app._scheduled_turn_retry_used = True
    app._server_proc = FakeServerProc("provider stacktrace")

    retry = asyncio.run(
        agent_handlers.handle_agent_task_exception(
            app,
            RuntimeError("An internal error occurred"),
        )
    )

    assert retry is False
    assert app._scheduled_turn_status == "failed"
    assert app._scheduled_turn_error == "RuntimeError: An internal error occurred"
    assert app._server_proc.max_chars == [4000]
    assert isinstance(app.messages[-1], ErrorMessage)
    assert "[server log tail]" in message_contents(app)[-1]
    assert "provider stacktrace" in message_contents(app)[-1]
    assert app._ui_adapter is not None
    assert "provider stacktrace" in app._ui_adapter.finalized_errors[-1]


def test_handle_agent_task_exception_tolerates_mount_failure() -> None:
    app = AgentHandlersApp()

    async def fail_mount(_message: object) -> None:
        raise RuntimeError("closing")

    app._mount_message = fail_mount  # type: ignore[method-assign]

    retry = asyncio.run(
        agent_handlers.handle_agent_task_exception(app, RuntimeError("boom"))
    )

    assert retry is False
    assert app._ui_adapter is not None
    assert app._ui_adapter.finalized_errors


def test_agent_error_detail_with_server_log_handles_read_failure() -> None:
    app = AgentHandlersApp()
    app._server_proc = FakeServerProc("ignored", fail=True)

    detail = agent_handlers.agent_error_detail_with_server_log(
        app,
        RuntimeError("An internal error occurred"),
    )

    assert detail == "RuntimeError: An internal error occurred"
    assert app._server_proc.max_chars == [4000]


def test_finish_active_scheduled_run_as_failed_is_noop_without_active_run() -> None:
    app = AgentHandlersApp()
    app._active_scheduled_run = None

    agent_handlers.finish_active_scheduled_run_as_failed(app, "failed")

    assert app._scheduler_runner is not None
    assert app._scheduler_runner.finished == []


def test_cleanup_agent_task_current_generation_runs_post_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentHandlersApp()
    app._agent_running = True
    app._agent_worker = "worker"
    app._active_turn_is_planner = True
    completed: list[AgentHandlersApp] = []

    async def complete(app_arg: AgentHandlersApp) -> None:
        completed.append(app_arg)

    monkeypatch.setattr(agent_handlers, "complete_active_scheduled_run", complete)

    asyncio.run(agent_handlers.cleanup_agent_task(app, generation=0))

    assert app._agent_running is False
    assert app._agent_worker is None
    assert app._active_turn_is_planner is False
    assert app.spinners == [None]
    assert app._chat_input is not None
    assert app._chat_input.cursor_states == [True]
    assert app.tokens_shown == [True]
    assert app.deferred_drained == 1
    assert app.auto_offload_calls == 1
    assert app.memory_update_calls == 1
    assert completed == [app]
    assert app.scheduler_drain_calls == 1
    assert app.queue_process_calls == 1


def test_cleanup_agent_task_reports_deferred_error_and_still_runs_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentHandlersApp()
    app.deferred_error = RuntimeError("deferred failed")
    completed = 0

    async def complete(_app: AgentHandlersApp) -> None:
        nonlocal completed
        completed += 1

    monkeypatch.setattr(agent_handlers, "complete_active_scheduled_run", complete)

    asyncio.run(agent_handlers.cleanup_agent_task(app, generation=0))

    assert isinstance(app.messages[-1], ErrorMessage)
    assert "deferred action failed" in message_contents(app)[-1]
    assert completed == 1
    assert app.queue_process_calls == 1


def test_cleanup_agent_task_stale_generation_finishes_schedule_without_post_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentHandlersApp()
    app._agent_generation = 3
    completed = 0

    async def complete(_app: AgentHandlersApp) -> None:
        nonlocal completed
        completed += 1

    monkeypatch.setattr(agent_handlers, "complete_active_scheduled_run", complete)

    asyncio.run(agent_handlers.cleanup_agent_task(app, generation=2))

    assert app.stale_generations == [2]
    assert app._scheduler_runner is not None
    assert app._scheduler_runner.finished == [
        ("run-1", "task-1", "failed", "Interrupted by user")
    ]
    assert completed == 0
    assert app.deferred_drained == 0
    assert app.queue_process_calls == 0


def test_run_post_cleanup_side_effects_continues_after_auto_offload_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentHandlersApp()
    app.auto_offload_error = RuntimeError("offload failed")
    completed: list[AgentHandlersApp] = []

    async def complete(app_arg: AgentHandlersApp) -> None:
        completed.append(app_arg)

    monkeypatch.setattr(agent_handlers, "complete_active_scheduled_run", complete)

    asyncio.run(agent_handlers.run_post_agent_cleanup_side_effects(app))

    assert app.auto_offload_calls == 1
    assert app.memory_update_calls == 1
    assert completed == [app]
    assert app.scheduler_drain_calls == 1
    assert app.queue_process_calls == 1


def test_cleanup_agent_task_stops_after_deferred_when_runtime_busy() -> None:
    app = AgentHandlersApp()
    app._shell_running = True

    asyncio.run(agent_handlers.cleanup_agent_task(app, generation=0))

    assert app.deferred_drained == 1
    assert app.auto_offload_calls == 0
    assert app.queue_process_calls == 0


def test_run_agent_task_executes_textual_with_context_and_restores_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentHandlersApp()
    app._active_turn_is_planner = True
    recorded: dict[str, object] = {}
    hook_calls = 0

    async def fake_execute_task_textual(**kwargs: object) -> None:
        recorded.update(kwargs)
        turn_stats = kwargs["turn_stats"]
        assert isinstance(turn_stats, SessionStats)
        turn_stats.record_request("model-a", 12, 4)
        handler = kwargs["on_wecom_file_request"]
        assert handler is not None

    async def post_turn_hook() -> None:
        nonlocal hook_calls
        hook_calls += 1

    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.textual_adapter",
        SimpleNamespace(execute_task_textual=fake_execute_task_textual),
    )
    monkeypatch.setattr(
        agent_handlers,
        "active_scheduled_wecom_chat_id",
        lambda _app: "chat-1",
    )

    request = AgentTurnRequest(
        message="run task",
        message_kwargs={"additional_kwargs": {"x": 1}},
        generation=7,
        agent_override="agent-override",
        thread_id_override="thread-override",
        post_turn_hook=post_turn_hook,
    )

    asyncio.run(agent_handlers.run_agent_task(app, request))

    assert recorded["user_input"] == "run task"
    assert recorded["agent"] == "agent-override"
    assert recorded["assistant_id"] == "assistant-1"
    assert recorded["session_state"] is app._session_state
    assert recorded["adapter"] is app._ui_adapter
    assert recorded["backend"] is app._backend
    assert recorded["image_tracker"] is app._image_tracker
    assert recorded["sandbox_type"] == "workspace-write"
    assert recorded["is_planner_turn"] is True
    assert recorded["message_kwargs"] == {"additional_kwargs": {"x": 1}}
    assert recorded["context"] == {
        "model": "model-a",
        "model_params": {"temperature": 0},
        "memory_model": "memory-model",
        "memory_model_params": {"limit": 2},
        "wecom_enabled": True,
        "scheduled_run": True,
    }
    assert recorded["on_text_delta"] is None
    assert recorded["on_schedule_payload"] == app._handle_schedule_tool_payload
    assert hook_calls == 1
    assert app._session_state is not None
    assert app._session_state.thread_id == "thread-1"
    assert app._session_stats.request_count == 1
    assert app._inflight_turn_stats is None
    assert app.cleanup_generations == [7]


def test_run_agent_task_returns_without_adapter_agent_or_session() -> None:
    app = AgentHandlersApp()
    app._ui_adapter = None

    asyncio.run(agent_handlers.run_agent_task(app, AgentTurnRequest(message="hello")))

    assert app.cleanup_generations == []

    app = AgentHandlersApp()
    app._agent = None

    asyncio.run(agent_handlers.run_agent_task(app, AgentTurnRequest(message="hello")))

    assert app.cleanup_generations == []

    app = AgentHandlersApp()
    app._session_state = None

    asyncio.run(agent_handlers.run_agent_task(app, AgentTurnRequest(message="hello")))

    assert app.cleanup_generations == []


def test_run_agent_task_retries_once_after_handler_requests_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentHandlersApp()
    app._active_scheduled_run = ("run-1", "task-1")
    attempts = 0

    async def fake_execute_task_textual(**_kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        raise TimeoutError("transient")

    async def fake_sleep(delay: float) -> None:
        assert delay == agent_handlers.SCHEDULED_TRANSIENT_RETRY_DELAY_SECONDS

    async def fake_handle(exc: BaseException) -> bool:
        app.exception_decisions.append(exc)
        return len(app.exception_decisions) == 1

    async def fake_recursive_run(request: AgentTurnRequest) -> None:
        app.run_requests.append(request)

    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.textual_adapter",
        SimpleNamespace(execute_task_textual=fake_execute_task_textual),
    )
    monkeypatch.setattr(
        agent_handlers,
        "active_scheduled_wecom_chat_id",
        lambda _app: None,
    )
    monkeypatch.setattr(agent_handlers.asyncio, "sleep", fake_sleep)
    app._handle_agent_task_exception = fake_handle  # type: ignore[method-assign]
    app._run_agent_task = fake_recursive_run  # type: ignore[method-assign]

    request = AgentTurnRequest(message="run task", generation=5)

    asyncio.run(agent_handlers.run_agent_task(app, request))

    assert attempts == 1
    assert len(app.exception_decisions) == 1
    assert app.run_requests == [request]
    assert app.cleanup_generations == []
