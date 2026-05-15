from __future__ import annotations

import asyncio
import logging
from collections import deque
from types import SimpleNamespace

from invincat_cli.app_runtime import server_handlers
from invincat_cli.model_config import ModelConfigError
from invincat_cli.widgets.messages import ErrorMessage


class ServerApp:
    class ServerStartFailed:
        def __init__(self, *, error: BaseException) -> None:
            self.error = error

    class ServerReady:
        def __init__(
            self,
            *,
            agent: object,
            server_proc: object,
            mcp_server_info: object,
            model: object,
        ) -> None:
            self.agent = agent
            self.server_proc = server_proc
            self.mcp_server_info = mcp_server_info
            self.model = model

    def __init__(self) -> None:
        self._resume_thread_intent = None
        self._assistant_id = "agent"
        self._server_kwargs: dict[str, object] = {"assistant_id": "agent"}
        self._session_state = SimpleNamespace(thread_id="current")
        self._lc_thread_id = "current"
        self._model_kwargs = None
        self._mcp_preload_kwargs = None
        self._server_proc = None
        self._connecting = True
        self._agent = None
        self._mcp_server_info = None
        self._mcp_tool_count = 0
        self._model = None
        self._initial_prompt = None
        self._deferred_actions: list[object] = []
        self._agent_running = False
        self._pending_messages = deque()
        self._queued_widgets = deque()
        self._pending_plan_handoff_prompt = object()
        self.notifications: list[tuple[str, dict[str, object]]] = []
        self.posted: list[object] = []
        self.after_refresh: list[object] = []
        self.messages: list[object] = []
        self.banner = SimpleNamespace(
            connected=None,
            failed=None,
            set_connected=lambda count: setattr(self.banner, "connected", count),
            set_failed=lambda error: setattr(self.banner, "failed", error),
        )
        self.resolved = False
        self.missing_banner = False
        self.drain_error: Exception | None = None
        self.drain_calls = 0
        self.queue_calls = 0

    def notify(self, message: str, **kwargs: object) -> None:
        self.notifications.append((message, kwargs))

    def post_message(self, message: object) -> None:
        self.posted.append(message)

    def query_one(self, *_args: object) -> object:
        if self.missing_banner:
            raise server_handlers.NoMatches("missing")
        return self.banner

    def call_after_refresh(self, callback: object) -> None:
        self.after_refresh.append(callback)

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    async def _maybe_drain_deferred(self) -> None:
        self.drain_calls += 1
        if self.drain_error is not None:
            raise self.drain_error
        return None

    async def _process_next_from_queue(self) -> None:
        self.queue_calls += 1
        return None

    async def _resolve_resume_thread(self) -> None:
        self.resolved = True
        self._resume_thread_intent = None


def test_resolve_resume_thread_returns_without_intent() -> None:
    app = ServerApp()
    app._resume_thread_intent = None
    app._lc_thread_id = "current"

    asyncio.run(server_handlers.resolve_resume_thread(app))

    assert app._lc_thread_id == "current"
    assert app.notifications == []


def test_resolve_resume_thread_uses_most_recent_and_agent(monkeypatch) -> None:
    app = ServerApp()
    app._resume_thread_intent = "__MOST_RECENT__"

    async def get_most_recent(_agent):
        return "t-1"

    async def get_thread_agent(_tid):
        return "custom"

    monkeypatch.setattr("invincat_cli.sessions.get_most_recent", get_most_recent)
    monkeypatch.setattr("invincat_cli.sessions.get_thread_agent", get_thread_agent)

    asyncio.run(server_handlers.resolve_resume_thread(app))

    assert app._lc_thread_id == "t-1"
    assert app._assistant_id == "custom"
    assert app._server_kwargs["assistant_id"] == "custom"
    assert app._session_state.thread_id == "t-1"


def test_resolve_resume_thread_generates_when_most_recent_missing(monkeypatch) -> None:
    app = ServerApp()
    app._resume_thread_intent = "__MOST_RECENT__"
    app._assistant_id = "custom"

    async def get_most_recent(_agent):
        return None

    monkeypatch.setattr("invincat_cli.sessions.get_most_recent", get_most_recent)
    monkeypatch.setattr("invincat_cli.sessions.generate_thread_id", lambda: "new")

    asyncio.run(server_handlers.resolve_resume_thread(app))

    assert app._lc_thread_id == "new"
    assert app.notifications[-1][1]["severity"] == "warning"


def test_resolve_resume_thread_uses_existing_thread_or_not_found(monkeypatch) -> None:
    existing = ServerApp()
    existing._resume_thread_intent = "existing"

    async def thread_exists(_tid):
        return True

    async def get_thread_agent(_tid):
        return "agent-x"

    monkeypatch.setattr("invincat_cli.sessions.thread_exists", thread_exists)
    monkeypatch.setattr("invincat_cli.sessions.get_thread_agent", get_thread_agent)

    asyncio.run(server_handlers.resolve_resume_thread(existing))

    assert existing._lc_thread_id == "existing"

    missing = ServerApp()
    missing._resume_thread_intent = "missing"

    async def thread_missing(_tid):
        return False

    async def find_similar_threads(_tid):
        return ["m1"]

    monkeypatch.setattr("invincat_cli.sessions.thread_exists", thread_missing)
    monkeypatch.setattr("invincat_cli.sessions.generate_thread_id", lambda: "new")
    monkeypatch.setattr(
        "invincat_cli.sessions.find_similar_threads", find_similar_threads
    )

    asyncio.run(server_handlers.resolve_resume_thread(missing))

    assert missing._lc_thread_id == "new"
    assert missing.notifications[-1][1]["severity"] == "warning"


def test_resolve_resume_thread_falls_back_on_lookup_error(monkeypatch) -> None:
    app = ServerApp()
    app._resume_thread_intent = "broken"

    async def fail(_thread_id: str) -> bool:
        raise RuntimeError("db down")

    monkeypatch.setattr("invincat_cli.sessions.thread_exists", fail)
    monkeypatch.setattr("invincat_cli.sessions.generate_thread_id", lambda: "new")

    asyncio.run(server_handlers.resolve_resume_thread(app))

    assert app._lc_thread_id == "new"
    assert app.notifications[-1][1]["severity"] == "warning"


def test_start_server_background_posts_ready(monkeypatch) -> None:
    app = ServerApp()
    agent = object()
    proc = object()

    async def start_server_and_get_agent(**_kwargs):
        return (agent, proc, None)

    monkeypatch.setattr(
        "invincat_cli.server.manager.start_server_and_get_agent",
        start_server_and_get_agent,
    )

    asyncio.run(server_handlers.start_server_background(app))

    assert app._server_proc is proc
    assert isinstance(app.posted[-1], ServerApp.ServerReady)
    assert app.posted[-1].agent is agent


def test_start_server_background_resolves_resume_and_model_kwargs(monkeypatch) -> None:
    app = ServerApp()
    app._resume_thread_intent = "latest"
    app._model_kwargs = {"model_spec": "openai:gpt"}
    agent = object()
    proc = object()
    model = object()
    saved: list[str] = []

    class ModelResult:
        provider = "openai"
        model_name = "gpt"

        def __init__(self) -> None:
            self.applied = 0
            self.model = model

        def apply_to_settings(self) -> None:
            self.applied += 1

    async def start_server_and_get_agent(**_kwargs):
        return (agent, proc, None)

    monkeypatch.setattr(
        "invincat_cli.config.create_model", lambda **_kwargs: ModelResult()
    )
    monkeypatch.setattr("invincat_cli.model_config.save_recent_model", saved.append)
    monkeypatch.setattr(
        "invincat_cli.server.manager.start_server_and_get_agent",
        start_server_and_get_agent,
    )

    asyncio.run(server_handlers.start_server_background(app))

    assert app.resolved is True
    assert app._model_kwargs is None
    assert saved == ["openai:gpt"]
    assert isinstance(app.posted[-1], ServerApp.ServerReady)
    assert app.posted[-1].model is model


def test_start_server_background_posts_failure_for_model_config_error(
    monkeypatch,
) -> None:
    app = ServerApp()
    app._model_kwargs = {"model_spec": "bad"}
    error = ModelConfigError("bad model")

    def fail_create(**_kwargs):
        raise error

    monkeypatch.setattr("invincat_cli.config.create_model", fail_create)

    asyncio.run(server_handlers.start_server_background(app))

    assert isinstance(app.posted[-1], ServerApp.ServerStartFailed)
    assert app.posted[-1].error is error


def test_start_server_background_posts_failure_for_server_error(monkeypatch) -> None:
    app = ServerApp()
    error = RuntimeError("server failed")

    async def start_server_and_get_agent(**_kwargs):
        return error

    monkeypatch.setattr(
        "invincat_cli.server.manager.start_server_and_get_agent",
        start_server_and_get_agent,
    )

    asyncio.run(server_handlers.start_server_background(app))

    assert isinstance(app.posted[-1], ServerApp.ServerStartFailed)
    assert app.posted[-1].error is error


def test_start_server_background_posts_failure_for_gather_exception(
    monkeypatch,
) -> None:
    app = ServerApp()

    async def fail_gather(*_coros, **_kwargs):
        for coro in _coros:
            close = getattr(coro, "close", None)
            if close is not None:
                close()
        raise RuntimeError("gather failed")

    async def start_server_and_get_agent(**_kwargs):
        return (object(), object(), None)

    monkeypatch.setattr(
        "invincat_cli.server.manager.start_server_and_get_agent",
        start_server_and_get_agent,
    )
    monkeypatch.setattr(server_handlers.asyncio, "gather", fail_gather)

    asyncio.run(server_handlers.start_server_background(app))

    assert isinstance(app.posted[-1], ServerApp.ServerStartFailed)
    assert "gather failed" in str(app.posted[-1].error)


def test_start_server_background_includes_mcp_preload_info(monkeypatch) -> None:
    app = ServerApp()
    app._mcp_preload_kwargs = {"thread_id": "t-1"}
    agent = object()
    proc = object()
    info = [SimpleNamespace(tools=[object()])]

    async def start_server_and_get_agent(**_kwargs):
        return (agent, proc, None)

    async def preload(**_kwargs):
        return info

    monkeypatch.setattr(
        "invincat_cli.server.manager.start_server_and_get_agent",
        start_server_and_get_agent,
    )
    monkeypatch.setattr("invincat_cli.main._preload_session_mcp_server_info", preload)

    asyncio.run(server_handlers.start_server_background(app))

    assert isinstance(app.posted[-1], ServerApp.ServerReady)
    assert app.posted[-1].mcp_server_info == info


def test_start_server_background_logs_mcp_preload_error(
    monkeypatch,
    caplog,
) -> None:
    app = ServerApp()
    app._mcp_preload_kwargs = {"thread_id": "t-1"}
    agent = object()
    proc = object()

    async def start_server_and_get_agent(**_kwargs):
        return (agent, proc, None)

    async def preload(**_kwargs):
        raise RuntimeError("preload failed")

    caplog.set_level(logging.WARNING, logger=server_handlers.__name__)
    monkeypatch.setattr(
        "invincat_cli.server.manager.start_server_and_get_agent",
        start_server_and_get_agent,
    )
    monkeypatch.setattr("invincat_cli.main._preload_session_mcp_server_info", preload)

    asyncio.run(server_handlers.start_server_background(app))

    assert isinstance(app.posted[-1], ServerApp.ServerReady)
    assert app.posted[-1].mcp_server_info is None
    assert "MCP metadata preload failed" in caplog.text


def test_handle_server_ready_updates_state_banner_and_schedules_work() -> None:
    app = ServerApp()
    app._initial_prompt = None
    app._lc_thread_id = None
    app._pending_messages.append("queued")
    app._deferred_actions.append(object())
    server_info = [SimpleNamespace(tools=[object(), object()])]
    model = object()
    event = SimpleNamespace(
        agent=object(),
        server_proc=object(),
        mcp_server_info=server_info,
        model=model,
    )

    server_handlers.handle_server_ready(app, event)

    assert app._connecting is False
    assert app._agent is event.agent
    assert app._server_proc is event.server_proc
    assert app._mcp_server_info == server_info
    assert app._mcp_tool_count == 2
    assert app._model is model
    assert app.banner.connected == 2
    assert len(app.after_refresh) == 2


def test_handle_server_ready_tolerates_missing_banner_and_drains_failures() -> None:
    app = ServerApp()
    app.missing_banner = True
    app._lc_thread_id = None
    app._deferred_actions.append(object())
    app.drain_error = RuntimeError("deferred failed")
    event = SimpleNamespace(
        agent=object(),
        server_proc=object(),
        mcp_server_info=[],
        model=None,
    )

    server_handlers.handle_server_ready(app, event)

    assert app._connecting is False
    assert len(app.after_refresh) == 1

    async def run_callback() -> None:
        app.after_refresh[0]()
        await asyncio.sleep(0)

    asyncio.run(run_callback())

    assert app.drain_calls == 1
    assert isinstance(app.messages[-1], ErrorMessage)


def test_handle_server_ready_schedules_initial_prompt_or_history(monkeypatch) -> None:
    prompt_app = ServerApp()
    prompt_app._initial_prompt = "hello"
    prompt_app._agent = None
    event = SimpleNamespace(
        agent=object(),
        server_proc=object(),
        mcp_server_info=[],
        model=None,
    )

    server_handlers.handle_server_ready(prompt_app, event)

    assert prompt_app.after_refresh

    history_app = ServerApp()
    history_app._lc_thread_id = "thread-1"

    server_handlers.handle_server_ready(history_app, event)

    assert history_app.after_refresh


def test_handle_server_start_failed_clears_queues_and_deferred() -> None:
    app = ServerApp()
    removed: list[bool] = []
    app._pending_messages.extend(["one"])
    app._queued_widgets.extend([SimpleNamespace(remove=lambda: removed.append(True))])
    app._deferred_actions.append(object())

    server_handlers.handle_server_start_failed(
        app,
        SimpleNamespace(error=RuntimeError("failed")),
    )

    assert app._connecting is False
    assert app.banner.failed == "failed"
    assert not app._pending_messages
    assert not app._queued_widgets
    assert removed == [True]
    assert app._deferred_actions == []
    assert app._pending_plan_handoff_prompt is None


def test_handle_server_start_failed_tolerates_missing_banner() -> None:
    app = ServerApp()
    app.missing_banner = True

    server_handlers.handle_server_start_failed(
        app,
        SimpleNamespace(error=RuntimeError("failed")),
    )

    assert app._connecting is False
