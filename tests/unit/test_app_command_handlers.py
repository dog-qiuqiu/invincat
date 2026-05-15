from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace

from invincat_cli.app_runtime import command_handlers
from invincat_cli.widgets.messages import AppMessage, ErrorMessage, QueuedUserMessage


class CommandApp:
    def __init__(self) -> None:
        self._pending_messages = deque(["pending"])
        self._queued_widgets = deque(["queued"])
        self._context_tokens = 0
        self._tokens_approximate = True
        self._session_state = SimpleNamespace(
            thread_id="thread-1",
            reset_thread=lambda: "thread-2",
        )
        self._agent_running = False
        self._shell_running = False
        self._deferred_actions = []
        self.messages: list[object] = []
        self.cleared = False
        self.tokens: list[int] = []
        self.statuses: list[str] = []
        self.workers: list[tuple[object, dict[str, object]]] = []
        self.actions: list[tuple[str, object]] = []
        self.exited = False

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    async def _clear_messages(self) -> None:
        self.cleared = True

    async def _get_conversation_token_count(self) -> int:
        return 111

    async def _clear_default_model(self, **kwargs: object) -> None:
        self.actions.append(("clear_default", kwargs))

    async def _set_default_model(self, model_arg: str, **kwargs: object) -> None:
        self.actions.append(("set_default", (model_arg, kwargs)))

    async def _switch_model(self, model_arg: str, **kwargs: object) -> None:
        self.actions.append(("switch", (model_arg, kwargs)))

    async def _show_model_selector(self, **kwargs: object) -> None:
        self.actions.append(("model_selector", kwargs))

    async def action_open_editor(self) -> None:
        self.actions.append(("editor", None))

    async def _handle_offload(self) -> None:
        self.actions.append(("offload", None))

    async def _handle_plan_task(self) -> None:
        self.actions.append(("plan", None))

    async def _exit_plan_mode(self) -> None:
        self.actions.append(("exit_plan", None))

    async def _show_thread_selector(self) -> None:
        self.actions.append(("threads", None))

    async def _handle_update_command(self) -> None:
        self.actions.append(("update", None))

    async def _handle_auto_update_toggle(self) -> None:
        self.actions.append(("auto_update", None))

    async def _show_mcp_viewer(self) -> None:
        self.actions.append(("mcp", None))

    async def _show_memory_viewer(self) -> None:
        self.actions.append(("memory", None))

    async def _handle_wecombot_command(self, command: str, **kwargs: object) -> None:
        self.actions.append(("wecom", (command, kwargs)))

    async def _handle_schedule_command(self, command: str) -> None:
        self.actions.append(("schedule", command))

    async def _show_theme_selector(self) -> None:
        self.actions.append(("theme", None))

    async def _show_language_selector(self) -> None:
        self.actions.append(("language", None))

    async def _handle_skill_command(self, command: str) -> None:
        self.actions.append(("skill", command))

    async def _discover_skills(self) -> str:
        return "discover"

    def exit(self) -> None:
        self.exited = True

    def _update_tokens(self, value: int) -> None:
        self.tokens.append(value)

    def _update_status(self, value: str) -> None:
        self.statuses.append(value)

    def query_one(self, *_args: object) -> object:
        raise command_handlers.NoMatches("missing")

    def run_worker(self, worker: object, **kwargs: object) -> None:
        self.workers.append((worker, kwargs))
        close = getattr(worker, "close", None)
        if callable(close):
            close()

    def _register_custom_themes(self) -> None:
        self.actions.append(("register_themes", None))


def message_contents(app: CommandApp) -> list[object]:
    return [getattr(message, "_content", None) for message in app.messages]


def test_handle_clear_command_resets_chat_and_thread() -> None:
    app = CommandApp()
    app._context_tokens = 42

    asyncio.run(command_handlers.handle_clear_command(app))

    assert app._pending_messages == deque()
    assert app._queued_widgets == deque()
    assert app.cleared is True
    assert app._context_tokens == 0
    assert app._tokens_approximate is False
    assert app.tokens == [0]
    assert app.statuses == [""]
    assert isinstance(app.messages[-1], AppMessage)


def test_handle_clear_command_updates_existing_welcome_banner() -> None:
    app = CommandApp()
    thread_ids: list[str] = []

    class Banner:
        def update_thread_id(self, thread_id: str) -> None:
            thread_ids.append(thread_id)

    app.query_one = lambda *_args: Banner()  # type: ignore[method-assign]

    asyncio.run(command_handlers.handle_clear_command(app))

    assert thread_ids == ["thread-2"]


def test_handle_tokens_command_uses_conversation_count(monkeypatch) -> None:
    app = CommandApp()
    app._context_tokens = 12
    monkeypatch.setattr(
        command_handlers,
        "build_tokens_message",
        lambda **kwargs: f"tokens={kwargs['conversation_tokens']}",
    )

    asyncio.run(command_handlers.handle_tokens_command(app, "/tokens"))

    assert message_contents(app)[-1] == "tokens=111"


def test_handle_tokens_command_skips_conversation_count_when_context_empty(
    monkeypatch,
) -> None:
    app = CommandApp()
    monkeypatch.setattr(
        command_handlers,
        "build_tokens_message",
        lambda **kwargs: f"tokens={kwargs['conversation_tokens']}",
    )

    asyncio.run(command_handlers.handle_tokens_command(app, "/tokens"))

    assert message_contents(app)[-1] == "tokens=None"


def test_handle_url_command_mounts_now_or_defers(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(command_handlers.webbrowser, "open", opened.append)

    idle = CommandApp()
    asyncio.run(command_handlers.handle_url_command(idle, "/docs", "/docs"))

    assert opened == [command_handlers.COMMAND_URLS["/docs"]]
    assert len(idle.messages) == 2

    busy = CommandApp()
    busy._agent_running = True
    asyncio.run(command_handlers.handle_url_command(busy, "/docs", "/docs"))

    assert isinstance(busy._queued_widgets[-1], QueuedUserMessage)
    assert busy._deferred_actions[-1].kind == "chat_output"


def test_deferred_url_output_replaces_placeholder(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(command_handlers.webbrowser, "open", opened.append)
    app = CommandApp()
    app._agent_running = True

    asyncio.run(command_handlers.handle_url_command(app, "/docs", "/docs"))
    queued_widget = app._queued_widgets[-1]
    asyncio.run(app._deferred_actions[-1].execute())

    assert queued_widget not in app._queued_widgets
    assert message_contents(app)[-1] is not None


def test_handle_trace_command_reports_missing_session_and_config(monkeypatch) -> None:
    no_session = CommandApp()
    no_session._session_state = None

    asyncio.run(command_handlers.handle_trace_command(no_session, "/trace"))

    assert isinstance(no_session.messages[-1], AppMessage)

    not_configured = CommandApp()
    monkeypatch.setattr(
        "invincat_cli.config.build_langsmith_thread_url",
        lambda _thread_id: None,
    )

    asyncio.run(command_handlers.handle_trace_command(not_configured, "/trace"))

    assert isinstance(not_configured.messages[-1], AppMessage)


def test_handle_trace_command_mounts_resolve_failure(monkeypatch) -> None:
    app = CommandApp()

    def fail(_thread_id: str) -> str:
        raise RuntimeError("bad config")

    monkeypatch.setattr("invincat_cli.config.build_langsmith_thread_url", fail)

    asyncio.run(command_handlers.handle_trace_command(app, "/trace"))

    assert isinstance(app.messages[-1], AppMessage)


def test_handle_trace_command_mounts_success_and_defers_when_busy(monkeypatch) -> None:
    class Loop:
        def run_in_executor(self, _executor: object, callback: object) -> None:
            assert callable(callback)
            callback()

    monkeypatch.setattr(
        "invincat_cli.config.build_langsmith_thread_url",
        lambda thread_id: f"https://trace.example/{thread_id}",
    )
    monkeypatch.setattr(command_handlers.asyncio, "get_running_loop", lambda: Loop())
    monkeypatch.setattr(
        command_handlers.webbrowser,
        "open",
        lambda _url: (_ for _ in ()).throw(RuntimeError("browser failed")),
    )

    idle = CommandApp()
    asyncio.run(command_handlers.handle_trace_command(idle, "/trace"))
    assert "trace.example/thread-1" in str(message_contents(idle)[-1])

    busy = CommandApp()
    busy._shell_running = True
    asyncio.run(command_handlers.handle_trace_command(busy, "/trace"))
    assert busy._deferred_actions[-1].kind == "chat_output"


def test_handle_model_command_routes_parse_results(monkeypatch) -> None:
    app = CommandApp()

    asyncio.run(
        command_handlers.handle_model_command(app, "/model --model-params '{bad'")
    )
    assert isinstance(app.messages[-1], ErrorMessage)

    asyncio.run(command_handlers.handle_model_command(app, "/model --default --clear"))
    assert app.actions[-1][0] == "clear_default"

    asyncio.run(
        command_handlers.handle_model_command(app, "/model --default openai:gpt")
    )
    assert app.actions[-1][0] == "set_default"

    asyncio.run(command_handlers.handle_model_command(app, "/model openai:gpt"))
    assert app.actions[-1][0] == "switch"


def test_handle_model_command_shows_usage_and_selector() -> None:
    app = CommandApp()

    asyncio.run(command_handlers.handle_model_command(app, "/model --default"))
    assert command_handlers.MODEL_DEFAULT_USAGE in str(message_contents(app)[-1])

    asyncio.run(command_handlers.handle_model_command(app, "/model"))
    assert app.actions[-1] == (
        "model_selector",
        {"target": "primary", "extra_kwargs": None},
    )

    asyncio.run(
        command_handlers.handle_model_command(
            app,
            "/model 2 --model-params '{\"temperature\": 0.2}'",
        )
    )
    assert app.actions[-1][0] == "model_selector"
    assert app.actions[-1][1]["target"] == "memory"


def test_handle_reload_command_reports_success(monkeypatch) -> None:
    app = CommandApp()
    monkeypatch.setattr(
        "invincat_cli.config.settings.reload_from_environment",
        lambda: {"model": ("old", "new")},
    )
    monkeypatch.setattr("invincat_cli.model_config.clear_caches", lambda: None)
    monkeypatch.setattr(command_handlers.theme, "reload_registry", lambda: None)
    monkeypatch.setattr(
        command_handlers,
        "build_reload_report",
        lambda changes, *, theme_reload_ok: f"reload {theme_reload_ok} {changes}",
    )

    asyncio.run(command_handlers.handle_reload_command(app, "/reload"))

    assert "reload True" in str(message_contents(app)[-1])
    assert app.workers[-1][1]["exclusive"] is True


def test_handle_reload_command_reports_config_failure(monkeypatch) -> None:
    app = CommandApp()

    def fail() -> dict:
        raise ValueError("bad env")

    monkeypatch.setattr(
        "invincat_cli.config.settings.reload_from_environment",
        fail,
    )

    asyncio.run(command_handlers.handle_reload_command(app, "/reload"))

    assert isinstance(app.messages[-1], AppMessage)
    assert app.workers == []


def test_handle_reload_command_reports_theme_reload_failure(monkeypatch) -> None:
    app = CommandApp()
    monkeypatch.setattr(
        "invincat_cli.config.settings.reload_from_environment",
        lambda: {},
    )
    monkeypatch.setattr("invincat_cli.model_config.clear_caches", lambda: None)

    def fail_theme_reload() -> None:
        raise RuntimeError("bad theme")

    monkeypatch.setattr(command_handlers.theme, "reload_registry", fail_theme_reload)
    monkeypatch.setattr(
        command_handlers,
        "build_reload_report",
        lambda changes, *, theme_reload_ok: f"theme={theme_reload_ok}",
    )

    asyncio.run(command_handlers.handle_reload_command(app, "/reload"))

    assert message_contents(app)[-1] == "theme=False"
    assert ("register_themes", None) not in app.actions


def test_handle_app_command_dispatches_routes(monkeypatch) -> None:
    app = CommandApp()
    monkeypatch.setattr(command_handlers.webbrowser, "open", lambda _url: None)
    monkeypatch.setattr(
        command_handlers,
        "resolve_version_message",
        lambda: "version text",
    )
    monkeypatch.setattr(
        "invincat_cli.config.build_langsmith_thread_url",
        lambda thread_id: f"https://trace.example/{thread_id}",
    )
    monkeypatch.setattr(
        "invincat_cli.config.settings.reload_from_environment",
        lambda: {},
    )
    monkeypatch.setattr("invincat_cli.model_config.clear_caches", lambda: None)
    monkeypatch.setattr(command_handlers.theme, "reload_registry", lambda: None)
    monkeypatch.setattr(
        command_handlers,
        "build_reload_report",
        lambda changes, *, theme_reload_ok: "reload text",
    )

    commands = [
        "/q",
        "/help",
        "/clear",
        "/editor",
        "/offload",
        "/plan",
        "/exit-plan",
        "/threads",
        "/trace",
        "/update",
        "/auto-update",
        "/tokens",
        "/mcp",
        "/memory",
        "/wecombot-status",
        "/schedule list",
        "/theme",
        "/language",
        "/model",
        "/reload",
        "/skill:demo run",
        "/skill-creator build one",
        "/version",
        "/docs",
        "/unknown",
    ]

    for command in commands:
        asyncio.run(command_handlers.handle_app_command(app, command))

    assert app.exited is True
    assert ("editor", None) in app.actions
    assert ("offload", None) in app.actions
    assert ("plan", None) in app.actions
    assert ("exit_plan", None) in app.actions
    assert ("threads", None) in app.actions
    assert ("update", None) in app.actions
    assert ("auto_update", None) in app.actions
    assert ("mcp", None) in app.actions
    assert ("memory", None) in app.actions
    assert ("wecom", ("/wecombot-status", {"action": "status"})) in app.actions
    assert ("schedule", "/schedule list") in app.actions
    assert ("theme", None) in app.actions
    assert ("language", None) in app.actions
    assert (
        "model_selector",
        {"target": "primary", "extra_kwargs": None},
    ) in app.actions
    assert ("skill", "/skill:demo run") in app.actions
    assert ("skill", "/skill:skill-creator build one") in app.actions
    assert any("version text" in str(content) for content in message_contents(app))
    assert any("reload text" in str(content) for content in message_contents(app))
