from __future__ import annotations

import asyncio
import builtins
import inspect
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from invincat_cli.app_runtime import startup_handlers


class FakeChatInput:
    def __init__(self) -> None:
        self.commands: list[object] | None = None
        self.focused = 0

    def update_slash_commands(self, commands: list[object]) -> None:
        self.commands = commands

    def focus_input(self) -> None:
        self.focused += 1


class FakeChat:
    def __init__(self) -> None:
        self.anchored = 0
        self.styles = SimpleNamespace(scrollbar_size_vertical=1)

    def anchor(self) -> None:
        self.anchored += 1


class FakeStatusBar:
    def __init__(self) -> None:
        self.branch: str | None = None
        self.memory_models: list[tuple[str, str, bool]] = []
        self.auto_approve_states: list[bool] = []

    def set_memory_model(
        self,
        *,
        provider: str,
        model: str,
        follow_primary: bool,
    ) -> None:
        self.memory_models.append((provider, model, follow_primary))

    def set_auto_approve(self, *, enabled: bool) -> None:
        self.auto_approve_states.append(enabled)


class StartupApp:
    def __init__(self) -> None:
        self._ui_adapter = None
        self._message_store = object()
        self._server_kwargs: dict[str, object] | None = {"assistant_id": "agent"}
        self._defer_server_start = False
        self._connecting = False
        self._initial_prompt: str | None = None
        self._lc_thread_id: str | None = None
        self._agent: object | None = None
        self._status_bar = FakeStatusBar()
        self._chat_input: FakeChatInput | None = FakeChatInput()
        self._memory_model_override: str | None = None
        self._memory_model_params_override: dict[str, object] | None = None
        self._model_params_override: dict[str, object] | None = None
        self._auto_approve = False
        self._discovered_skills: list[object] = []
        self._skill_allowed_roots: list[Path] = []
        self._assistant_id = "agent"
        self._profile_override = {"profile": "test"}
        self.chat = FakeChat()
        self.status_bar = FakeStatusBar()
        self.chat_input = FakeChatInput()
        self.workers: list[tuple[object, dict[str, object]]] = []
        self.after_refresh: list[object] = []
        self.notifications: list[tuple[str, dict[str, object]]] = []
        self.scheduler_started = False
        self.mounted: list[object] = []
        self.statuses: list[str] = []
        self.approvals: list[object] = []
        self.tokens: list[int] = []
        self._post_paint_init = object()
        self._startup_task: asyncio.Task | None = None

    def query_one(self, selector: str, *_args: object) -> object:
        if selector == "#chat":
            return self.chat
        if selector == "#status-bar":
            return self.status_bar
        if selector == "#input-area":
            return self.chat_input
        raise LookupError(selector)

    async def _mount_message(self, message: object) -> None:
        self.mounted.append(message)

    def _update_status(self, status: str) -> None:
        self.statuses.append(status)

    async def _request_approval(self, *_args: object) -> object:
        self.approvals.append(_args)
        return None

    def _on_auto_approve_enabled(self) -> None:
        return None

    async def _set_spinner(self, _status: object) -> None:
        return None

    def _set_active_message(self, _message_id: str | None) -> None:
        return None

    def _sync_message_content(self, _message_id: str, _content: str) -> None:
        return None

    async def _request_ask_user(self, *_args: object) -> object:
        return None

    async def _request_approve_plan(self, *_args: object) -> object:
        return None

    def _on_tokens_update(self, tokens: int) -> None:
        self.tokens.append(tokens)

    def _hide_tokens(self) -> None:
        return None

    def _show_tokens(self) -> None:
        return None

    async def _discover_skills(self) -> None:
        return None

    async def _init_session_state(self) -> None:
        return None

    async def _start_server_background(self) -> None:
        return None

    def _prewarm_model_caches(self) -> None:
        return None

    async def _prewarm_threads_cache(self) -> None:
        return None

    async def _check_optional_tools_background(self) -> None:
        return None

    def _start_scheduler(self) -> None:
        self.scheduler_started = True

    def run_worker(self, work: object, **kwargs: object) -> None:
        self.workers.append((work, kwargs))
        if inspect.iscoroutine(work):
            work.close()

    def call_after_refresh(self, callback: object) -> None:
        self.after_refresh.append(callback)

    def notify(self, message: str, **kwargs: object) -> None:
        self.notifications.append((message, kwargs))

    def _discover_skills_and_roots(self) -> tuple[list[object], list[Path]]:
        return ([{"name": "skill-a"}], [Path("/tmp/skills")])

    async def _resolve_git_branch_and_continue(self) -> None:
        return None

    def _prewarm_deferred_imports(self) -> None:
        return None


class FakeAdapter:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self._on_tokens_update = None
        self._on_tokens_hide = None
        self._on_tokens_show = None
        self.message_store = None

    def set_message_store(self, store: object) -> None:
        self.message_store = store


def test_handle_mount_initializes_status_input_and_startup_workers(
    monkeypatch,
) -> None:
    app = StartupApp()
    app._auto_approve = True
    app._memory_model_override = "anthropic:claude"
    app._discovered_skills = [object()]

    monkeypatch.setattr(startup_handlers, "is_ascii_mode", lambda: True)
    monkeypatch.setattr("invincat_cli.config.settings.model_provider", "openai")
    monkeypatch.setattr("invincat_cli.config.settings.model_name", "gpt-primary")
    monkeypatch.setattr(
        "invincat_cli.config._get_default_memory_model_spec",
        lambda: "openai:gpt-memory",
    )
    monkeypatch.setattr(
        "invincat_cli.model_config.get_target_model_params",
        lambda target, model_spec: {"target": target, "model": model_spec},
    )
    monkeypatch.setattr(
        "invincat_cli.commands.registry.COMMANDS",
        [SimpleNamespace(name="/help", description="Help", hidden_keywords="")],
    )
    monkeypatch.setattr(
        "invincat_cli.commands.registry.build_skill_commands",
        lambda _skills: [("/skill:test", "Skill", "Run skill")],
    )

    asyncio.run(startup_handlers.handle_mount(app))

    assert app.chat.anchored == 1
    assert app.chat.styles.scrollbar_size_vertical == 0
    assert app._memory_model_override == "anthropic:claude"
    assert app._memory_model_params_override == {
        "target": "memory",
        "model": "anthropic:claude",
    }
    assert app._model_params_override == {
        "target": "primary",
        "model": "openai:gpt-primary",
    }
    assert app._status_bar is app.status_bar
    assert app._chat_input is app.chat_input
    assert app.status_bar.memory_models == [("anthropic", "claude", False)]
    assert app.status_bar.auto_approve_states == [True]
    assert app.chat_input.commands == [
        ("/help", "Help", ""),
        ("/skill:test", "Skill", "Run skill"),
    ]
    assert app.chat_input.focused == 1
    assert app.workers[-1][1]["group"] == "startup-import-prewarm"
    assert app._startup_task is not None
    app._startup_task.cancel()


def test_post_paint_init_sets_adapter_and_schedules_workers(monkeypatch) -> None:
    app = StartupApp()
    monkeypatch.setattr("invincat_cli.textual_adapter.TextualUIAdapter", FakeAdapter)

    asyncio.run(startup_handlers.post_paint_init(app))

    assert isinstance(app._ui_adapter, FakeAdapter)
    assert app._ui_adapter.message_store is app._message_store
    assert app.scheduler_started is True
    groups = [kwargs["group"] for _work, kwargs in app.workers]
    assert groups == [
        "startup-skill-discovery",
        "session-init",
        "server-startup",
        "startup-model-prewarm",
        "startup-thread-prewarm",
        "startup-tool-check",
    ]


def test_post_paint_init_defers_initial_prompt_until_after_refresh(monkeypatch) -> None:
    app = StartupApp()
    app._initial_prompt = "hello"
    app._agent = object()
    monkeypatch.setattr("invincat_cli.textual_adapter.TextualUIAdapter", FakeAdapter)

    asyncio.run(startup_handlers.post_paint_init(app))

    assert app.after_refresh


def test_post_paint_init_loads_history_for_resumed_thread(monkeypatch) -> None:
    app = StartupApp()
    app._lc_thread_id = "thread-1"
    app._agent = object()
    monkeypatch.setattr("invincat_cli.textual_adapter.TextualUIAdapter", FakeAdapter)

    asyncio.run(startup_handlers.post_paint_init(app))

    assert app.after_refresh


def test_resolve_git_branch_and_continue_sets_status_branch(monkeypatch) -> None:
    app = StartupApp()

    def run_git(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(returncode=0, stdout="feature/test\n")

    monkeypatch.setattr("subprocess.run", run_git)

    asyncio.run(startup_handlers.resolve_git_branch_and_continue(app))

    assert app._status_bar.branch == "feature/test"
    assert app.after_refresh == [app._post_paint_init]


def test_resolve_git_branch_and_continue_handles_git_failure(monkeypatch) -> None:
    app = StartupApp()

    def fail_git(*_args: object, **_kwargs: object) -> object:
        raise OSError("git failed")

    monkeypatch.setattr("subprocess.run", fail_git)

    asyncio.run(startup_handlers.resolve_git_branch_and_continue(app))

    assert app._status_bar.branch == ""
    assert app.after_refresh == [app._post_paint_init]


def test_resolve_git_branch_and_continue_handles_git_timeout_and_missing_binary(
    monkeypatch,
) -> None:
    app = StartupApp()

    def missing_git(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError

    monkeypatch.setattr("subprocess.run", missing_git)

    asyncio.run(startup_handlers.resolve_git_branch_and_continue(app))

    assert app._status_bar.branch == ""
    assert app.after_refresh == [app._post_paint_init]

    app = StartupApp()

    def timeout_git(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired("git", 2)

    monkeypatch.setattr("subprocess.run", timeout_git)

    asyncio.run(startup_handlers.resolve_git_branch_and_continue(app))

    assert app._status_bar.branch == ""
    assert app.after_refresh == [app._post_paint_init]


def test_resolve_git_branch_and_continue_handles_outer_failure(monkeypatch) -> None:
    app = StartupApp()

    async def fail_to_thread(_func: object, *_args: object, **_kwargs: object) -> str:
        raise RuntimeError("thread failed")

    monkeypatch.setattr(startup_handlers.asyncio, "to_thread", fail_to_thread)

    asyncio.run(startup_handlers.resolve_git_branch_and_continue(app))

    assert app.after_refresh == [app._post_paint_init]


def test_check_optional_tools_background_notifies_missing_tools(monkeypatch) -> None:
    app = StartupApp()

    monkeypatch.setattr("invincat_cli.main.check_optional_tools", lambda: ["git"])
    monkeypatch.setattr(
        "invincat_cli.main.format_tool_warning_tui",
        lambda tool: f"missing {tool}",
    )

    asyncio.run(startup_handlers.check_optional_tools_background(app))

    assert app.notifications == [
        (
            "missing git",
            {"severity": "warning", "timeout": 15, "markup": False},
        )
    ]


def test_check_optional_tools_background_ignores_os_errors(monkeypatch) -> None:
    app = StartupApp()

    def fail_check() -> list[str]:
        raise OSError("tool check failed")

    monkeypatch.setattr("invincat_cli.main.check_optional_tools", fail_check)

    asyncio.run(startup_handlers.check_optional_tools_background(app))

    assert app.notifications == []


def test_check_optional_tools_background_handles_import_and_unexpected_errors(
    monkeypatch,
) -> None:
    app = StartupApp()
    monkeypatch.setitem(sys.modules, "invincat_cli.main", None)

    asyncio.run(startup_handlers.check_optional_tools_background(app))

    assert app.notifications == []

    app = StartupApp()
    monkeypatch.delitem(sys.modules, "invincat_cli.main", raising=False)

    real_import = builtins.__import__

    def import_main(
        name: str,
        globals: object = None,
        locals: object = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        module = real_import(name, globals, locals, fromlist, level)
        if name == "invincat_cli.main":
            setattr(
                module,
                "check_optional_tools",
                lambda: (_ for _ in ()).throw(RuntimeError("tool failed")),
            )
        return module

    monkeypatch.setattr(builtins, "__import__", import_main)

    asyncio.run(startup_handlers.check_optional_tools_background(app))

    assert app.notifications == []


def test_discover_skills_updates_cache_and_chat_commands(monkeypatch) -> None:
    app = StartupApp()
    monkeypatch.setattr(
        "invincat_cli.commands.registry.SLASH_COMMANDS", [("/help", "", "")]
    )
    monkeypatch.setattr(
        "invincat_cli.commands.registry.build_skill_commands",
        lambda skills: [("/skill:skill-a", str(len(skills)), "")],
    )

    asyncio.run(startup_handlers.discover_skills(app))

    assert app._discovered_skills == [{"name": "skill-a"}]
    assert app._skill_allowed_roots == [Path("/tmp/skills")]
    assert app._chat_input is not None
    assert app._chat_input.commands == [
        ("/help", "", ""),
        ("/skill:skill-a", "1", ""),
    ]


def test_discover_skills_notifies_on_filesystem_error(monkeypatch) -> None:
    app = StartupApp()

    def fail_discovery() -> tuple[list[object], list[Path]]:
        raise OSError("bad disk")

    app._discover_skills_and_roots = fail_discovery
    monkeypatch.setattr("invincat_cli.commands.registry.SLASH_COMMANDS", [])
    monkeypatch.setattr(
        "invincat_cli.commands.registry.build_skill_commands", lambda _skills: []
    )

    asyncio.run(startup_handlers.discover_skills(app))

    assert app._discovered_skills == []
    assert app._skill_allowed_roots == []
    assert app.notifications[-1][1]["severity"] == "warning"


def test_discover_skills_notifies_on_unexpected_error_without_chat_input(
    monkeypatch,
) -> None:
    app = StartupApp()
    app._chat_input = None

    def fail_discovery() -> tuple[list[object], list[Path]]:
        raise RuntimeError("bad metadata")

    app._discover_skills_and_roots = fail_discovery
    monkeypatch.setattr("invincat_cli.commands.registry.SLASH_COMMANDS", [])
    monkeypatch.setattr(
        "invincat_cli.commands.registry.build_skill_commands", lambda _skills: []
    )

    asyncio.run(startup_handlers.discover_skills(app))

    assert app._discovered_skills == []
    assert app._skill_allowed_roots == []
    assert app.notifications[-1][1]["severity"] == "warning"


def test_discover_skills_and_roots_uses_agent_fallback(monkeypatch) -> None:
    app = StartupApp()
    app._assistant_id = None
    calls: list[str] = []

    def discover_roots(
        *, settings: object, assistant_id: str
    ) -> tuple[list[object], list[Path]]:
        calls.append(assistant_id)
        return ([], [])

    monkeypatch.setattr(startup_handlers, "discover_roots", discover_roots)

    assert startup_handlers.discover_skills_and_roots(app) == ([], [])
    assert calls == ["agent"]


def test_prewarm_threads_cache_uses_configured_limit(monkeypatch) -> None:
    calls: list[int] = []

    monkeypatch.setattr("invincat_cli.sessions.get_thread_limit", lambda: 7)

    async def prewarm(*, limit: int) -> None:
        calls.append(limit)

    monkeypatch.setattr("invincat_cli.sessions.prewarm_thread_message_counts", prewarm)

    asyncio.run(startup_handlers.prewarm_threads_cache())

    assert calls == [7]


def test_prewarm_model_caches_runs_profile_lookup(monkeypatch) -> None:
    app = StartupApp()
    calls: list[object] = []

    monkeypatch.setattr(
        "invincat_cli.model_config.get_available_models",
        lambda: calls.append("models"),
    )
    monkeypatch.setattr(
        "invincat_cli.model_config.get_model_profiles",
        lambda *, cli_override: calls.append(cli_override),
    )

    asyncio.run(startup_handlers.prewarm_model_caches(app))

    assert calls == ["models", {"profile": "test"}]


def test_prewarm_deferred_imports_and_model_cache_failure(monkeypatch) -> None:
    def fake_module(name: str, **attrs: object) -> ModuleType:
        module = ModuleType(name)
        for attr, value in attrs.items():
            setattr(module, attr, value)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    fake_module("invincat_cli.widgets.approval", ApprovalMenu=object)
    fake_module("invincat_cli.widgets.ask_user", AskUserMenu=object)
    fake_module("invincat_cli.widgets.memory_viewer", MemoryViewerScreen=object)
    fake_module("invincat_cli.widgets.model_selector", ModelSelectorScreen=object)
    fake_module(
        "invincat_cli.widgets.thread_selector",
        DeleteThreadConfirmScreen=object,
        ThreadSelectorScreen=object,
    )

    startup_handlers.prewarm_deferred_imports()

    real_import = builtins.__import__

    def fail_third_party_import(
        name: str,
        globals: object = None,
        locals: object = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "deepagents.backends":
            raise RuntimeError("third-party unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_third_party_import)

    startup_handlers.prewarm_deferred_imports()

    app = StartupApp()
    monkeypatch.setattr(
        "invincat_cli.model_config.get_available_models",
        lambda: (_ for _ in ()).throw(RuntimeError("models failed")),
    )
    monkeypatch.setattr(
        "invincat_cli.model_config.get_model_profiles",
        lambda *, cli_override: None,
    )

    asyncio.run(startup_handlers.prewarm_model_caches(app))
