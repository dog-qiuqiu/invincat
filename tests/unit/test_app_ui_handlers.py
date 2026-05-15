from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from invincat_cli.app_runtime import ui_handlers
from invincat_cli.i18n import Language
from invincat_cli.widgets.status import StatusBar


class FakeChat:
    def __init__(self) -> None:
        self.scroll_y = 12.0
        self.is_anchored = True
        self.released = False
        self.anchored = False
        self.scrolled_to: list[tuple[float, bool]] = []

    def release_anchor(self) -> None:
        self.released = True

    def scroll_to(self, *, y: float, animate: bool = False) -> None:
        self.scrolled_to.append((y, animate))

    def anchor(self) -> None:
        self.anchored = True


class FakeChatInput:
    def __init__(self) -> None:
        self.focused = 0
        self.commands: list[object] = []

    def focus_input(self) -> None:
        self.focused += 1

    def update_slash_commands(self, commands: object) -> None:
        self.commands.append(commands)


class FakeBanner:
    def __init__(self) -> None:
        self._project_url = "file:///project"
        self.updated: list[object] = []
        self.thread_ids: list[str] = []

    def _build_banner(self, project_url: str) -> str:
        return f"banner:{project_url}"

    def update(self, value: object) -> None:
        self.updated.append(value)

    def update_thread_id(self, thread_id: str) -> None:
        self.thread_ids.append(thread_id)


class FakeStatusBar:
    def __init__(self) -> None:
        self.refreshed = 0

    def refresh(self) -> None:
        self.refreshed += 1


class UIApp:
    def __init__(self) -> None:
        self.theme = "invincat-dark"
        self._chat_input: FakeChatInput | None = FakeChatInput()
        self._mcp_server_info = [{"name": "server"}]
        self._cwd = Path("/tmp/project")
        self._assistant_id = "assistant-1"
        self._session_state = SimpleNamespace(thread_id="thread-1")
        self._agent_running = False
        self._shell_running = False
        self._connecting = False
        self._discovered_skills = {"skill-a": object()}
        self.chat = FakeChat()
        self.banner = FakeBanner()
        self.status_bar = FakeStatusBar()
        self.screens: list[object] = []
        self.screen_callbacks: list[object] = []
        self.notifications: list[tuple[str, str | None, int | float | None]] = []
        self.later: list[tuple[object, tuple[object, ...]]] = []
        self.deferred: list[object] = []
        self.resumed_threads: list[str] = []
        self.refreshed_ui = 0
        self.css_refreshes: list[bool] = []
        self.missing: set[object] = set()

    def query_one(self, selector: object, *_args: object) -> object:
        if selector in self.missing:
            raise ui_handlers.NoMatches(str(selector))
        if selector == "#chat":
            return self.chat
        if selector == "#welcome-banner":
            return self.banner
        if selector is StatusBar:
            return self.status_bar
        raise ui_handlers.NoMatches(str(selector))

    def push_screen(self, screen: object, callback: object) -> None:
        self.screens.append(screen)
        self.screen_callbacks.append(callback)

    def refresh_css(self, *, animate: bool) -> None:
        self.css_refreshes.append(animate)

    def notify(
        self,
        message: str,
        *,
        severity: str | None = None,
        timeout: int | float | None = None,
        **_kwargs: object,
    ) -> None:
        self.notifications.append((message, severity, timeout))

    def call_later(self, callback: object, *args: object) -> None:
        self.later.append((callback, args))

    def _refresh_all_ui_text(self) -> None:
        self.refreshed_ui += 1

    def _resolve_memory_store_paths(self) -> dict[str, str]:
        return {"user": "/tmp/user.json", "project": "/tmp/project.json"}

    def _defer_action(self, action: object) -> None:
        self.deferred.append(action)

    async def _resume_thread(self, thread_id: str) -> None:
        self.resumed_threads.append(thread_id)


class FakeThemeSelectorScreen:
    def __init__(self, *, current_theme: str) -> None:
        self.current_theme = current_theme


class FakeLanguageSelectorScreen:
    def __init__(self, *, current_language: Language) -> None:
        self.current_language = current_language


class FakeMCPViewerScreen:
    def __init__(self, *, server_info: list[dict[str, object]]) -> None:
        self.server_info = server_info


class FakeMemoryViewerScreen:
    def __init__(self, *, memory_store_paths: dict[str, str]) -> None:
        self.memory_store_paths = memory_store_paths


class FakeThreadSelectorScreen:
    def __init__(
        self,
        *,
        current_thread: str | None,
        thread_limit: int,
        initial_threads: list[dict[str, object]],
    ) -> None:
        self.current_thread = current_thread
        self.thread_limit = thread_limit
        self.initial_threads = initial_threads


def install_fake_screen_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.widgets.theme_selector",
        SimpleNamespace(ThemeSelectorScreen=FakeThemeSelectorScreen),
    )
    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.widgets.language_selector",
        SimpleNamespace(LanguageSelectorScreen=FakeLanguageSelectorScreen),
    )
    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.widgets.mcp_viewer",
        SimpleNamespace(MCPViewerScreen=FakeMCPViewerScreen),
    )
    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.widgets.memory_viewer",
        SimpleNamespace(MemoryViewerScreen=FakeMemoryViewerScreen),
    )
    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.widgets.thread_selector",
        SimpleNamespace(ThreadSelectorScreen=FakeThreadSelectorScreen),
    )


def test_show_theme_selector_applies_theme_persists_and_restores_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_screen_modules(monkeypatch)
    app = UIApp()
    monkeypatch.setattr(ui_handlers, "save_theme_preference", lambda _theme: False)

    asyncio.run(ui_handlers.show_theme_selector(app))

    screen = app.screens[-1]
    assert isinstance(screen, FakeThemeSelectorScreen)
    assert screen.current_theme == "invincat-dark"
    assert app.chat.released is True

    app.screen_callbacks[-1]("invincat-light")  # type: ignore[index,operator]

    assert app.theme == "invincat-light"
    assert app.css_refreshes == [False]
    assert app.chat.scrolled_to == [(12.0, False)]
    assert app.chat.anchored is True
    assert app._chat_input is not None
    assert app._chat_input.focused == 1
    callback, args = app.later[-1]
    assert args == ()

    asyncio.run(callback())  # type: ignore[operator]

    assert app.notifications[-1][1] == "warning"


def test_show_theme_selector_notifies_when_persist_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_screen_modules(monkeypatch)
    app = UIApp()

    def fail_save(_theme: str) -> bool:
        raise RuntimeError("disk failed")

    monkeypatch.setattr(ui_handlers, "save_theme_preference", fail_save)

    asyncio.run(ui_handlers.show_theme_selector(app))
    app.screen_callbacks[-1]("invincat-light")  # type: ignore[index,operator]
    callback, _args = app.later[-1]

    asyncio.run(callback())  # type: ignore[operator]

    assert app.notifications[-1][1] == "warning"


def test_show_theme_selector_cancel_only_restores_scroll_and_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_screen_modules(monkeypatch)
    app = UIApp()

    asyncio.run(ui_handlers.show_theme_selector(app))
    app.screen_callbacks[-1](None)  # type: ignore[index,operator]

    assert app.theme == "invincat-dark"
    assert app.css_refreshes == []
    assert app.later == []
    assert app.chat.scrolled_to == [(12.0, False)]
    assert app._chat_input is not None
    assert app._chat_input.focused == 1


def test_show_language_selector_notifies_refreshes_and_restores_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_screen_modules(monkeypatch)
    app = UIApp()

    asyncio.run(ui_handlers.show_language_selector(app))

    screen = app.screens[-1]
    assert isinstance(screen, FakeLanguageSelectorScreen)

    app.screen_callbacks[-1](Language.EN)  # type: ignore[index,operator]

    assert app.notifications[-1][1] == "information"
    assert app.refreshed_ui == 1
    assert app.chat.scrolled_to == [(12.0, False)]
    assert app._chat_input is not None
    assert app._chat_input.focused == 1


def test_refresh_all_ui_text_updates_mounted_widgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = UIApp()
    monkeypatch.setattr(
        ui_handlers,
        "build_startup_slash_commands",
        lambda **_kwargs: ["help", "skill-a"],
    )

    ui_handlers.refresh_all_ui_text(app)

    assert app.banner.updated == ["banner:file:///project"]
    assert app.status_bar.refreshed == 1
    assert app._chat_input is not None
    assert app._chat_input.commands == [["help", "skill-a"]]


def test_refresh_all_ui_text_tolerates_missing_optional_widgets() -> None:
    app = UIApp()
    app.missing = {"#welcome-banner", StatusBar}

    ui_handlers.refresh_all_ui_text(app)

    assert app.banner.updated == []
    assert app.status_bar.refreshed == 0


def test_show_mcp_and_memory_viewers_restore_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_screen_modules(monkeypatch)
    app = UIApp()

    asyncio.run(ui_handlers.show_mcp_viewer(app))
    asyncio.run(ui_handlers.show_memory_viewer(app))

    mcp_screen = app.screens[-2]
    memory_screen = app.screens[-1]
    assert isinstance(mcp_screen, FakeMCPViewerScreen)
    assert mcp_screen.server_info == [{"name": "server"}]
    assert isinstance(memory_screen, FakeMemoryViewerScreen)
    assert memory_screen.memory_store_paths == {
        "user": "/tmp/user.json",
        "project": "/tmp/project.json",
    }

    app.screen_callbacks[-2](None)  # type: ignore[index,operator]
    app.screen_callbacks[-1](None)  # type: ignore[index,operator]

    assert app._chat_input is not None
    assert app._chat_input.focused == 2


def test_resolve_memory_store_paths_uses_settings_agent_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = UIApp()
    app._cwd = tmp_path

    monkeypatch.setattr(
        "invincat_cli.config.settings.get_agent_dir",
        lambda assistant_id: tmp_path / "agents" / assistant_id,
    )

    paths = ui_handlers.resolve_memory_store_paths(app)

    assert paths["user"] == str((tmp_path / "agents/assistant-1/memory_user.json"))
    assert paths["project"] == str((tmp_path / ".invincat/memory_project.json"))


def test_show_thread_selector_resumes_immediately_when_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_screen_modules(monkeypatch)
    app = UIApp()
    monkeypatch.setattr("invincat_cli.sessions.get_thread_limit", lambda: 7)
    monkeypatch.setattr(
        "invincat_cli.sessions.get_cached_threads",
        lambda **_kwargs: [{"thread_id": "thread-2"}],
    )

    asyncio.run(ui_handlers.show_thread_selector(app))

    screen = app.screens[-1]
    assert isinstance(screen, FakeThreadSelectorScreen)
    assert screen.current_thread == "thread-1"
    assert screen.thread_limit == 7
    assert screen.initial_threads == [{"thread_id": "thread-2"}]

    app.screen_callbacks[-1]("thread-2")  # type: ignore[index,operator]

    callback, args = app.later[-1]
    assert args == ("thread-2",)
    asyncio.run(callback(*args))  # type: ignore[operator]
    assert app.resumed_threads == ["thread-2"]
    assert app._chat_input is not None
    assert app._chat_input.focused == 1


def test_show_thread_selector_defers_when_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_screen_modules(monkeypatch)
    app = UIApp()
    app._agent_running = True
    monkeypatch.setattr("invincat_cli.sessions.get_thread_limit", lambda: 7)
    monkeypatch.setattr("invincat_cli.sessions.get_cached_threads", lambda **_: [])

    asyncio.run(ui_handlers.show_thread_selector(app))
    app.screen_callbacks[-1]("thread-3")  # type: ignore[index,operator]

    assert app.later == []
    assert len(app.deferred) == 1
    action = app.deferred[0]
    assert action.kind == "thread_switch"
    assert app.notifications[-1][2] == 3
    assert app._chat_input is not None
    assert app._chat_input.focused == 1


def test_update_welcome_banner_updates_when_present() -> None:
    app = UIApp()

    ui_handlers.update_welcome_banner(
        app,
        "thread-2",
        missing_message="missing %s",
        warn_if_missing=True,
    )

    assert app.banner.thread_ids == ["thread-2"]


def test_update_welcome_banner_logs_missing(caplog: pytest.LogCaptureFixture) -> None:
    app = UIApp()
    app.missing = {"#welcome-banner"}

    ui_handlers.update_welcome_banner(
        app,
        "thread-2",
        missing_message="missing %s",
        warn_if_missing=True,
    )

    assert "missing thread-2" in caplog.text


def test_update_welcome_banner_logs_missing_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=ui_handlers.logger.name)
    app = UIApp()
    app.missing = {"#welcome-banner"}

    ui_handlers.update_welcome_banner(
        app,
        "thread-2",
        missing_message="missing %s",
        warn_if_missing=False,
    )

    assert "missing thread-2" in caplog.text
