from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from invincat_cli.app_runtime import model_handlers
from invincat_cli.app_runtime.model_runtime import ResolvedModelSpec
from invincat_cli.widgets.messages import AppMessage, ErrorMessage


class FakeChatInput:
    def __init__(self) -> None:
        self.focused = 0

    def focus_input(self) -> None:
        self.focused += 1


class FakeStatusBar:
    def __init__(self) -> None:
        self.models: list[tuple[str, str]] = []
        self.memory_models: list[tuple[str, str, bool]] = []

    def set_model(self, *, provider: str, model: str) -> None:
        self.models.append((provider, model))

    def set_memory_model(
        self,
        *,
        provider: str,
        model: str,
        follow_primary: bool,
    ) -> None:
        self.memory_models.append((provider, model, follow_primary))


class FakeBanner:
    def __init__(self) -> None:
        self.connecting = 0

    def set_connecting(self) -> None:
        self.connecting += 1


class FakeChat:
    def __init__(self) -> None:
        self.anchored = False

    def anchor(self) -> None:
        self.anchored = True


class FakeModelSelectorScreen:
    def __init__(
        self,
        *,
        current_model: str | None,
        current_provider: str | None,
        current_memory_model: str | None,
        current_memory_provider: str | None,
        initial_target: str,
        cli_profile_override: object,
    ) -> None:
        self.current_model = current_model
        self.current_provider = current_provider
        self.current_memory_model = current_memory_model
        self.current_memory_provider = current_memory_provider
        self.initial_target = initial_target
        self.cli_profile_override = cli_profile_override


class FakeModelResult:
    def __init__(
        self,
        *,
        provider: str = "openai",
        model_name: str = "gpt-new",
        model: object | None = None,
    ) -> None:
        self.provider = provider
        self.model_name = model_name
        self.model = model if model is not None else object()
        self.applied = 0

    def apply_to_settings(self) -> None:
        self.applied += 1


class ModelApp:
    def __init__(self) -> None:
        self._memory_model_override: str | None = None
        self._memory_model_params_override: dict[str, object] | None = None
        self._profile_override = "profile-a"
        self._agent_running = False
        self._shell_running = False
        self._connecting = False
        self._chat_input: FakeChatInput | None = FakeChatInput()
        self._status_bar: FakeStatusBar | None = FakeStatusBar()
        self._server_kwargs: dict[str, object] | None = None
        self._model_kwargs: dict[str, object] | None = {"old": True}
        self._defer_server_start = True
        self._model_switching = False
        self._model_override: str | None = None
        self._model_params_override: dict[str, object] | None = None
        self._model: object | None = None
        self.remote_agent: object | None = object()
        self.banner = FakeBanner()
        self.chat = FakeChat()
        self.screens: list[object] = []
        self.screen_callbacks: list[object] = []
        self.deferred: list[object] = []
        self.notifications: list[tuple[str, int | float | None]] = []
        self.later: list[object] = []
        self.workers: list[tuple[object, bool, str | None]] = []
        self.messages: list[object] = []
        self.invalidated = 0
        self.switch_calls: list[tuple[str, str, dict[str, object] | None, bool]] = []
        self.default_model_calls: list[tuple[str, str, bool, bool]] = []

    def push_screen(self, screen: object, callback: object) -> None:
        self.screens.append(screen)
        self.screen_callbacks.append(callback)

    def _defer_action(self, action: object) -> None:
        self.deferred.append(action)

    def notify(self, message: str, *, timeout: int | float | None = None) -> None:
        self.notifications.append((message, timeout))

    def call_later(self, callback: object) -> None:
        self.later.append(callback)

    def query_one(self, selector: str, *_args: object) -> object:
        if selector == "#welcome-banner":
            return self.banner
        if selector == "#chat":
            return self.chat
        raise model_handlers.NoMatches(selector)

    def run_worker(
        self,
        worker: object,
        *,
        exclusive: bool,
        group: str | None = None,
    ) -> None:
        self.workers.append((worker, exclusive, group))

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    def _remote_agent(self) -> object | None:
        return self.remote_agent

    async def _switch_model(
        self,
        model_spec: str,
        *,
        target: str,
        extra_kwargs: dict[str, object] | None,
        persist_as_default: bool,
    ) -> None:
        self.switch_calls.append((model_spec, target, extra_kwargs, persist_as_default))

    def _invalidate_planner_agent_cache(self) -> None:
        self.invalidated += 1

    def _apply_primary_model_status(self, *, model_result: object) -> None:
        model_handlers.apply_primary_model_status(self, model_result=model_result)

    def _start_server_after_primary_model_switch(
        self,
        *,
        resolved: ResolvedModelSpec,
        target_kwargs: dict[str, object] | None,
    ) -> None:
        model_handlers.start_server_after_primary_model_switch(
            self,
            resolved=resolved,
            target_kwargs=target_kwargs,
        )

    @property
    def _start_server_background(self) -> str:
        return "start-server"

    async def _apply_primary_model_switch(
        self,
        *,
        resolved: ResolvedModelSpec,
        model_result: object,
        target_kwargs: dict[str, object] | None,
        remote_agent: object,
        save_recent_model: object,
    ) -> None:
        await model_handlers.apply_primary_model_switch(
            self,
            resolved=resolved,
            model_result=model_result,
            target_kwargs=target_kwargs,
            remote_agent=remote_agent,
            save_recent_model=save_recent_model,
        )

    async def _apply_memory_model_switch(
        self,
        *,
        resolved: ResolvedModelSpec,
        model_result: object,
        target_kwargs: dict[str, object] | None,
    ) -> None:
        await model_handlers.apply_memory_model_switch(
            self,
            resolved=resolved,
            model_result=model_result,
            target_kwargs=target_kwargs,
        )

    async def _set_default_model(
        self,
        model_spec: str,
        *,
        target: str,
        announce: bool,
        apply_to_session: bool = False,
    ) -> bool:
        self.default_model_calls.append(
            (model_spec, target, announce, apply_to_session)
        )
        return True


def install_fake_model_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.widgets.model_selector",
        SimpleNamespace(ModelSelectorScreen=FakeModelSelectorScreen),
    )


def message_contents(app: ModelApp) -> list[str]:
    return [str(getattr(message, "_content", "")) for message in app.messages]


def test_show_model_selector_runs_switch_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_model_selector(monkeypatch)
    monkeypatch.setattr("invincat_cli.config.settings.model_provider", "openai")
    monkeypatch.setattr("invincat_cli.config.settings.model_name", "gpt-old")
    app = ModelApp()
    app._memory_model_override = "anthropic:claude"

    asyncio.run(
        model_handlers.show_model_selector(
            app,
            target="memory",
            extra_kwargs={"temperature": 0.1},
        )
    )

    screen = app.screens[-1]
    assert isinstance(screen, FakeModelSelectorScreen)
    assert screen.current_provider == "openai"
    assert screen.current_model == "gpt-old"
    assert screen.current_memory_provider == "anthropic"
    assert screen.current_memory_model == "claude"
    assert screen.initial_target == "memory"
    assert screen.cli_profile_override == "profile-a"

    app.screen_callbacks[-1](("openai:gpt-new", "gpt-new", "primary"))  # type: ignore[index,operator]

    assert len(app.later) == 1
    assert app.deferred == []
    assert app._chat_input is not None
    assert app._chat_input.focused == 1


def test_show_model_selector_defers_when_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_model_selector(monkeypatch)
    app = ModelApp()
    app._agent_running = True

    asyncio.run(model_handlers.show_model_selector(app))
    app.screen_callbacks[-1](("openai:gpt-new", "gpt-new", "primary"))  # type: ignore[index,operator]

    assert app.later == []
    assert len(app.deferred) == 1
    assert app.deferred[0].kind == "model_switch"
    assert app.notifications[-1][1] == 3
    assert app._chat_input is not None
    assert app._chat_input.focused == 1


def test_start_server_after_primary_model_switch_updates_state() -> None:
    app = ModelApp()
    app._server_kwargs = {"port": 0}
    resolved = ResolvedModelSpec(
        raw="gpt-new",
        provider="openai",
        model_name="gpt-new",
        display="openai:gpt-new",
        parsed=False,
    )

    model_handlers.start_server_after_primary_model_switch(
        app,
        resolved=resolved,
        target_kwargs={"temperature": 0},
    )

    assert app._server_kwargs == {
        "port": 0,
        "model_name": "openai:gpt-new",
        "model_params": {"temperature": 0},
    }
    assert app._model_kwargs is None
    assert app._defer_server_start is False
    assert app._connecting is True
    assert app.banner.connecting == 1
    assert app.workers == [("start-server", True, "server-startup")]


def test_apply_primary_model_status_ignores_missing_status_bar() -> None:
    app = ModelApp()
    app._status_bar = None
    result = FakeModelResult(provider="openai", model_name="gpt-new")

    model_handlers.apply_primary_model_status(app, model_result=result)

    assert app._status_bar is None


def test_apply_primary_model_status_updates_memory_when_following_primary() -> None:
    app = ModelApp()
    result = FakeModelResult(provider="openai", model_name="gpt-new")

    model_handlers.apply_primary_model_status(app, model_result=result)

    assert app._status_bar is not None
    assert app._status_bar.models == [("openai", "gpt-new")]
    assert app._status_bar.memory_models == [("openai", "gpt-new", True)]

    app._memory_model_override = "anthropic:claude"
    model_handlers.apply_primary_model_status(app, model_result=result)

    assert app._status_bar.models[-1] == ("openai", "gpt-new")
    assert app._status_bar.memory_models == [("openai", "gpt-new", True)]


def test_apply_primary_model_switch_sets_state_and_reports_success() -> None:
    app = ModelApp()
    app.remote_agent = None
    app._server_kwargs = {}
    model = object()
    result = FakeModelResult(provider="openai", model_name="gpt-new", model=model)
    resolved = ResolvedModelSpec(
        raw="gpt-new",
        provider="openai",
        model_name="gpt-new",
        display="openai:gpt-new",
        parsed=False,
    )

    asyncio.run(
        model_handlers.apply_primary_model_switch(
            app,
            resolved=resolved,
            model_result=result,
            target_kwargs={"temperature": 0},
            remote_agent=None,
            save_recent_model=lambda _spec: True,
        )
    )

    assert result.applied == 1
    assert app._model_override == "openai:gpt-new"
    assert app._model_params_override == {"temperature": 0}
    assert app.invalidated == 1
    assert app._model is model
    assert app._connecting is True
    assert isinstance(app.messages[-1], AppMessage)
    assert "openai:gpt-new" in message_contents(app)[-1]


def test_apply_primary_model_switch_reports_preference_save_failure() -> None:
    app = ModelApp()
    result = FakeModelResult()
    resolved = ResolvedModelSpec(
        raw="gpt-new",
        provider="openai",
        model_name="gpt-new",
        display="openai:gpt-new",
        parsed=False,
    )

    asyncio.run(
        model_handlers.apply_primary_model_switch(
            app,
            resolved=resolved,
            model_result=result,
            target_kwargs=None,
            remote_agent=object(),
            save_recent_model=lambda _spec: False,
        )
    )

    assert isinstance(app.messages[-1], ErrorMessage)


def test_apply_memory_model_switch_updates_override_status_and_message() -> None:
    app = ModelApp()
    result = FakeModelResult(provider="anthropic", model_name="claude")
    resolved = ResolvedModelSpec(
        raw="anthropic:claude",
        provider="anthropic",
        model_name="claude",
        display="anthropic:claude",
        parsed=True,
    )

    asyncio.run(
        model_handlers.apply_memory_model_switch(
            app,
            resolved=resolved,
            model_result=result,
            target_kwargs={"max_tokens": 1000},
        )
    )

    assert app._memory_model_override == "anthropic:claude"
    assert app._memory_model_params_override == {"max_tokens": 1000}
    assert app._status_bar is not None
    assert app._status_bar.memory_models == [("anthropic", "claude", False)]
    assert isinstance(app.messages[-1], AppMessage)


def install_model_switch_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    create_model: object | None = None,
    has_credentials: object | None = None,
    save_recent: object | None = None,
) -> None:
    monkeypatch.setattr("invincat_cli.config.settings.model_provider", "openai")
    monkeypatch.setattr("invincat_cli.config.settings.model_name", "gpt-old")
    monkeypatch.setattr("invincat_cli.config.detect_provider", lambda _spec: "openai")
    monkeypatch.setattr(
        "invincat_cli.config.create_model",
        create_model or (lambda *_args, **_kwargs: FakeModelResult()),
    )
    monkeypatch.setattr("invincat_cli.model_config.clear_caches", lambda: None)
    monkeypatch.setattr(
        "invincat_cli.model_config.has_provider_credentials",
        has_credentials or (lambda _provider: True),
    )
    monkeypatch.setattr(
        "invincat_cli.model_config.get_credential_env_var",
        lambda provider: f"{provider.upper()}_API_KEY",
    )
    monkeypatch.setattr(
        "invincat_cli.model_config.get_target_model_params",
        lambda _target, _display: {"saved": True},
    )
    monkeypatch.setattr(
        "invincat_cli.model_config.save_recent_model",
        save_recent or (lambda _display: True),
    )


def test_switch_model_reports_in_progress() -> None:
    app = ModelApp()
    app._model_switching = True

    asyncio.run(model_handlers.switch_model(app, "openai:gpt-new"))

    assert isinstance(app.messages[-1], AppMessage)


def test_switch_model_reports_missing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_model_switch_fakes(
        monkeypatch,
        has_credentials=lambda _provider: False,
    )
    app = ModelApp()

    asyncio.run(model_handlers.switch_model(app, "openai:gpt-new"))

    assert isinstance(app.messages[-1], ErrorMessage)
    assert "OPENAI_API_KEY" in message_contents(app)[-1]
    assert app._model_switching is False


def test_switch_model_proceeds_when_credentials_cannot_be_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_model_switch_fakes(
        monkeypatch,
        has_credentials=lambda _provider: None,
    )
    app = ModelApp()

    asyncio.run(model_handlers.switch_model(app, "openai:gpt-new"))

    assert isinstance(app.messages[-1], AppMessage)
    assert "openai:gpt-new" in message_contents(app)[-1]
    assert app._model_switching is False


def test_switch_model_reports_missing_server_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_model_switch_fakes(monkeypatch)
    app = ModelApp()
    app.remote_agent = None
    app._server_kwargs = None

    asyncio.run(model_handlers.switch_model(app, "openai:gpt-new"))

    assert isinstance(app.messages[-1], ErrorMessage)
    assert app._model_switching is False


def test_switch_model_reports_already_using(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_model_switch_fakes(monkeypatch)
    app = ModelApp()

    asyncio.run(model_handlers.switch_model(app, "openai:gpt-old"))

    assert isinstance(app.messages[-1], AppMessage)
    assert "openai:gpt-old" in message_contents(app)[-1]
    assert app._model_switching is False


def test_switch_model_applies_primary_and_persists_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = FakeModelResult(provider="openai", model_name="gpt-new")
    install_model_switch_fakes(
        monkeypatch,
        create_model=lambda *_args, **_kwargs: created,
    )
    app = ModelApp()
    app._server_kwargs = {}
    app.remote_agent = None

    asyncio.run(
        model_handlers.switch_model(
            app,
            "openai:gpt-new",
            extra_kwargs={"temperature": 0},
            persist_as_default=True,
        )
    )

    assert app._model_override == "openai:gpt-new"
    assert app._model_params_override == {"temperature": 0}
    assert app.default_model_calls == [("openai:gpt-new", "primary", False, False)]
    assert app.chat.anchored is True
    assert app._model_switching is False


def test_switch_model_reports_create_model_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_create(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("model failed")

    install_model_switch_fakes(monkeypatch, create_model=fail_create)
    app = ModelApp()

    asyncio.run(model_handlers.switch_model(app, "openai:gpt-new"))

    assert isinstance(app.messages[-1], ErrorMessage)
    assert "model failed" in message_contents(app)[-1]
    assert app._model_switching is False


def test_switch_model_applies_memory_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = FakeModelResult(provider="openai", model_name="gpt-memory")
    install_model_switch_fakes(
        monkeypatch,
        create_model=lambda *_args, **_kwargs: created,
    )
    app = ModelApp()

    asyncio.run(
        model_handlers.switch_model(
            app,
            "openai:gpt-memory",
            target="memory",
            extra_kwargs={"temperature": 0},
            persist_as_default=True,
        )
    )

    assert app._memory_model_override == "openai:gpt-memory"
    assert app._memory_model_params_override == {"temperature": 0}
    assert app.default_model_calls == [("openai:gpt-memory", "memory", False, False)]
    assert app._model_switching is False


def test_set_default_model_applies_memory_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("invincat_cli.config.detect_provider", lambda _spec: "openai")
    monkeypatch.setattr("invincat_cli.model_config.save_default_model", lambda _: False)
    monkeypatch.setattr(
        "invincat_cli.model_config.save_memory_default_model",
        lambda _spec: True,
    )
    app = ModelApp()

    ok = asyncio.run(
        model_handlers.set_default_model(
            app,
            "gpt-memory",
            target="memory",
            apply_to_session=True,
        )
    )

    assert ok is True
    assert app._memory_model_override == "openai:gpt-memory"
    assert app._memory_model_params_override is None
    assert app._status_bar is not None
    assert app._status_bar.memory_models == [("openai", "gpt-memory", False)]
    assert isinstance(app.messages[-1], AppMessage)


def test_set_default_model_reports_save_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("invincat_cli.config.detect_provider", lambda _spec: None)
    monkeypatch.setattr("invincat_cli.model_config.save_default_model", lambda _: False)
    monkeypatch.setattr(
        "invincat_cli.model_config.save_memory_default_model",
        lambda _spec: True,
    )
    app = ModelApp()

    ok = asyncio.run(model_handlers.set_default_model(app, "custom-model"))

    assert ok is False
    assert isinstance(app.messages[-1], ErrorMessage)


def test_clear_default_model_reports_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_primary_calls = 0

    def clear_primary() -> bool:
        nonlocal clear_primary_calls
        clear_primary_calls += 1
        return True

    monkeypatch.setattr("invincat_cli.model_config.clear_default_model", clear_primary)
    monkeypatch.setattr(
        "invincat_cli.model_config.clear_memory_default_model",
        lambda: False,
    )
    app = ModelApp()

    asyncio.run(model_handlers.clear_default_model(app))
    asyncio.run(model_handlers.clear_default_model(app, target="memory"))

    assert clear_primary_calls == 1
    assert isinstance(app.messages[-2], AppMessage)
    assert isinstance(app.messages[-1], ErrorMessage)
