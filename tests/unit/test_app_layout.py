from __future__ import annotations

from types import SimpleNamespace

import pytest

from invincat_cli import theme
from invincat_cli.app_runtime import layout


def test_get_theme_variable_defaults_delegates_to_theme_helpers(monkeypatch) -> None:
    app = SimpleNamespace(theme="demo")
    colors = object()
    monkeypatch.setattr(layout.theme, "get_theme_colors", lambda value: colors)
    monkeypatch.setattr(
        layout.theme,
        "get_css_variable_defaults",
        lambda *, colors: {"primary": "blue"},
    )

    assert layout.get_theme_variable_defaults(app) == {"primary": "blue"}


def test_compose_layout_yields_expected_widgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[tuple[str, dict]] = []

    class FakeContainer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            created.append((type(self).__name__, kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    class FakeVerticalScroll(FakeContainer):
        pass

    class FakeWelcomeBanner:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            created.append(("WelcomeBanner", kwargs))

    class FakeChatInput:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            created.append(("ChatInput", kwargs))

    class FakeStatusBar:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            created.append(("StatusBar", kwargs))

    monkeypatch.setattr(layout, "VerticalScroll", FakeVerticalScroll)
    monkeypatch.setattr(layout, "Container", FakeContainer)
    monkeypatch.setattr(layout, "WelcomeBanner", FakeWelcomeBanner)
    monkeypatch.setattr(layout, "ChatInput", FakeChatInput)
    monkeypatch.setattr(layout, "StatusBar", FakeStatusBar)

    app = SimpleNamespace(
        _lc_thread_id="thread-1",
        _mcp_tool_count=3,
        _connecting=True,
        _resume_thread_intent="resume",
        _server_kwargs={"port": 0},
        _cwd="/tmp/project",
        _image_tracker=object(),
    )

    widgets = list(layout.compose_layout(app))

    assert [type(widget).__name__ for widget in widgets] == [
        "FakeWelcomeBanner",
        "FakeContainer",
        "FakeChatInput",
        "FakeStatusBar",
    ]
    assert created[0] == ("FakeVerticalScroll", {"id": "chat"})
    assert widgets[0].kwargs == {
        "thread_id": "thread-1",
        "mcp_tool_count": 3,
        "connecting": True,
        "resuming": True,
        "local_server": True,
        "id": "welcome-banner",
    }
    assert widgets[1].kwargs == {"id": "messages"}
    assert widgets[2].kwargs["cwd"] == "/tmp/project"
    assert widgets[2].kwargs["id"] == "input-area"
    assert widgets[3].kwargs == {"cwd": "/tmp/project", "id": "status-bar"}


def test_register_custom_themes_registers_custom_entries(monkeypatch) -> None:
    custom = theme.ThemeEntry(
        label="Custom",
        dark=True,
        colors=theme.DARK_COLORS,
        custom=True,
    )
    builtin = theme.ThemeEntry(
        label="Builtin",
        dark=False,
        colors=theme.LIGHT_COLORS,
        custom=False,
    )
    monkeypatch.setattr(
        layout.theme.ThemeEntry,
        "REGISTRY",
        {"custom": custom, "builtin": builtin},
    )
    registered: list[object] = []
    app = SimpleNamespace(register_theme=registered.append)

    layout.register_custom_themes(app)

    assert len(registered) == 1
    assert registered[0].name == "custom"


def test_register_custom_themes_skips_entries_that_fail_registration(
    monkeypatch,
) -> None:
    entry = theme.ThemeEntry(
        label="Custom",
        dark=True,
        colors=theme.DARK_COLORS,
        custom=True,
    )
    monkeypatch.setattr(layout.theme.ThemeEntry, "REGISTRY", {"custom": entry})

    def fail_register(_theme: object) -> None:
        raise RuntimeError("theme rejected")

    app = SimpleNamespace(register_theme=fail_register)

    layout.register_custom_themes(app)
