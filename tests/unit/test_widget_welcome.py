from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from textual.content import Content

from invincat_cli.core.version import __version__
from invincat_cli.widgets import welcome as welcome_mod
from invincat_cli.widgets.welcome import (
    WelcomeBanner,
    build_connecting_footer,
    build_failure_footer,
    build_welcome_footer,
)


def _plain(value: object) -> str:
    if isinstance(value, Content):
        return value.plain
    return str(value)


@pytest.fixture(autouse=True)
def stable_welcome_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    colors = SimpleNamespace(
        primary="#00ffff",
        success="#00ff00",
        error="#ff0000",
        tool="#ffaa00",
    )
    monkeypatch.setattr(welcome_mod.theme, "get_theme_colors", lambda *_args: colors)
    monkeypatch.setattr(
        welcome_mod,
        "get_i18n",
        lambda: SimpleNamespace(get_all_tips=lambda: ["Use tests"]),
    )
    monkeypatch.setattr(
        welcome_mod,
        "t",
        lambda key: {
            "welcome.ready": "Ready.",
            "welcome.connecting": "Connecting...",
            "welcome.resuming": "Resuming...",
        }.get(key, key),
    )
    monkeypatch.setattr(
        welcome_mod, "get_glyphs", lambda: SimpleNamespace(checkmark="*")
    )
    monkeypatch.setattr(welcome_mod.random, "choice", lambda values: values[0])


def test_footer_builders_render_failure_connecting_and_ready_text() -> None:
    assert "bad config" in build_failure_footer("bad config").plain
    assert build_connecting_footer().plain == "\nConnecting...\n"
    assert build_connecting_footer(resuming=True).plain == "\nResuming...\n"
    assert build_connecting_footer(local_server=True).plain == "\nConnecting...\n"

    ready = build_welcome_footer(primary_color="#fff", tip="Ship small")
    assert ready.plain == "\nReady. What would you like to build?\nTip: Ship small"


def test_build_banner_includes_project_thread_tools_and_footer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        WelcomeBanner, "app", property(lambda _self: SimpleNamespace(theme="dark"))
    )
    monkeypatch.setattr(
        welcome_mod,
        "get_banner",
        lambda: f"Invincat v{__version__} (local)",
    )
    monkeypatch.setattr(welcome_mod, "_is_editable_install", lambda: True)
    monkeypatch.setattr(welcome_mod, "_get_editable_install_path", lambda: "/repo")
    monkeypatch.setattr(welcome_mod, "get_langsmith_project_name", lambda: "proj")

    banner = WelcomeBanner(thread_id="thread-1", mcp_tool_count=2)
    rendered = banner._build_banner("https://smith/projects/proj")

    assert rendered.plain == (
        f"Invincat v{__version__} (local)\n"
        "Installed from: /repo\n"
        "* LangSmith tracing: 'proj'\n"
        "Thread: thread-1\n"
        "* Loaded 2 MCP tools\n"
        "\nReady. What would you like to build?\n"
        "Tip: Use tests"
    )


def test_build_banner_renders_connecting_and_failure_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        WelcomeBanner,
        "app",
        property(lambda _self: SimpleNamespace(theme="textual-ansi")),
    )
    monkeypatch.setattr(welcome_mod, "get_banner", lambda: "Invincat")
    monkeypatch.setattr(welcome_mod, "_is_editable_install", lambda: False)
    monkeypatch.setattr(welcome_mod, "_get_editable_install_path", lambda: None)
    monkeypatch.setattr(welcome_mod, "get_langsmith_project_name", lambda: None)

    connecting = WelcomeBanner(connecting=True, local_server=True)
    assert connecting._build_banner().plain == "Invincat\n\nConnecting...\n"

    failed = WelcomeBanner(connecting=True)
    failed.set_failed("boom")
    assert failed._build_banner().plain == "Invincat\n\nServer failed to start: boom\n"


def test_build_banner_editable_install_without_version_tag_uses_primary_style(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        WelcomeBanner, "app", property(lambda _self: SimpleNamespace(theme="dark"))
    )
    monkeypatch.setattr(welcome_mod, "get_banner", lambda: "Invincat dev")
    monkeypatch.setattr(welcome_mod, "_is_editable_install", lambda: True)
    monkeypatch.setattr(welcome_mod, "_get_editable_install_path", lambda: None)
    monkeypatch.setattr(welcome_mod, "get_langsmith_project_name", lambda: None)

    banner = WelcomeBanner()

    assert banner._build_banner().plain.startswith("Invincat dev\n")


def test_build_banner_ansi_project_url_uses_linked_project_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        WelcomeBanner,
        "app",
        property(lambda _self: SimpleNamespace(theme="textual-ansi")),
    )
    monkeypatch.setattr(welcome_mod, "get_banner", lambda: "Invincat")
    monkeypatch.setattr(welcome_mod, "_is_editable_install", lambda: False)
    monkeypatch.setattr(welcome_mod, "_get_editable_install_path", lambda: None)
    monkeypatch.setattr(welcome_mod, "get_langsmith_project_name", lambda: "proj")

    banner = WelcomeBanner()
    rendered = banner._build_banner("https://smith/projects/proj")

    assert "* LangSmith tracing: 'proj'" in rendered.plain


def test_welcome_banner_state_methods_rebuild_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builds: list[tuple[str | None, bool, bool, int, str | None]] = []

    def fake_build(self: WelcomeBanner, project_url: str | None = None) -> Content:
        builds.append(
            (
                project_url,
                self._connecting,
                self._failed,
                self._mcp_tool_count,
                self._cli_thread_id,
            )
        )
        return Content("banner")

    updates: list[str] = []
    monkeypatch.setattr(WelcomeBanner, "_build_banner", fake_build)
    monkeypatch.setattr(welcome_mod, "get_langsmith_project_name", lambda: None)

    banner = WelcomeBanner(thread_id="old", connecting=True)
    monkeypatch.setattr(banner, "update", lambda value: updates.append(_plain(value)))

    banner.update_thread_id("new")
    banner.set_connected(mcp_tool_count=3)
    banner.set_connecting()
    banner.set_failed("no server")
    banner._project_url = "https://smith"
    banner._on_theme_change()

    assert updates == ["banner", "banner", "banner", "banner", "banner"]
    assert builds[-5:] == [
        (None, True, False, 0, "new"),
        (None, False, False, 3, "new"),
        (None, True, False, 3, "new"),
        (None, False, True, 3, "new"),
        ("https://smith", False, True, 3, "new"),
    ]


def test_welcome_banner_mount_registers_theme_watch_and_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        WelcomeBanner, "app", property(lambda _self: SimpleNamespace(theme="dark"))
    )
    monkeypatch.setattr(welcome_mod, "get_langsmith_project_name", lambda: "proj")
    banner = WelcomeBanner()
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        banner,
        "watch",
        lambda obj, attr, callback, *, init: calls.append((attr, init)),
    )
    monkeypatch.setattr(
        banner,
        "run_worker",
        lambda worker, *, exclusive: calls.append(("worker", exclusive)),
    )

    banner.on_mount()

    assert calls == [("theme", False), ("worker", True)]


def test_welcome_banner_mount_skips_worker_without_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        WelcomeBanner, "app", property(lambda _self: SimpleNamespace(theme="dark"))
    )
    monkeypatch.setattr(welcome_mod, "get_langsmith_project_name", lambda: None)
    banner = WelcomeBanner()
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        banner,
        "watch",
        lambda obj, attr, callback, *, init: calls.append((attr, init)),
    )
    monkeypatch.setattr(
        banner,
        "run_worker",
        lambda worker, *, exclusive: calls.append(("worker", exclusive)),
    )

    banner.on_mount()

    assert calls == [("theme", False)]


def test_welcome_banner_click_opens_style_link(monkeypatch: pytest.MonkeyPatch) -> None:
    event = object()
    opened: list[object] = []
    monkeypatch.setattr(welcome_mod, "open_style_link", opened.append)
    monkeypatch.setattr(
        WelcomeBanner, "app", property(lambda _self: SimpleNamespace(theme="dark"))
    )
    monkeypatch.setattr(welcome_mod, "get_langsmith_project_name", lambda: None)

    WelcomeBanner().on_click(event)  # type: ignore[arg-type]

    assert opened == [event]


def test_fetch_and_update_sets_project_url_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        WelcomeBanner, "_build_banner", lambda _self, _url=None: Content("updated")
    )
    monkeypatch.setattr(welcome_mod, "get_langsmith_project_name", lambda: "proj")
    monkeypatch.setattr(
        welcome_mod, "fetch_langsmith_project_url", lambda _name: "https://smith/proj"
    )
    banner = WelcomeBanner()
    updates: list[str] = []
    monkeypatch.setattr(banner, "update", lambda value: updates.append(_plain(value)))

    asyncio.run(banner._fetch_and_update())

    assert banner._project_url == "https://smith/proj"
    assert updates == ["updated"]


def test_fetch_and_update_ignores_missing_project_and_fetch_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        WelcomeBanner, "_build_banner", lambda _self, _url=None: Content("updated")
    )
    monkeypatch.setattr(welcome_mod, "get_langsmith_project_name", lambda: None)
    no_project = WelcomeBanner()
    no_project_updates: list[str] = []
    monkeypatch.setattr(
        no_project, "update", lambda value: no_project_updates.append(_plain(value))
    )

    asyncio.run(no_project._fetch_and_update())
    assert no_project_updates == []

    monkeypatch.setattr(welcome_mod, "get_langsmith_project_name", lambda: "proj")
    monkeypatch.setattr(
        welcome_mod,
        "fetch_langsmith_project_url",
        lambda _name: (_ for _ in ()).throw(OSError("offline")),
    )
    failed = WelcomeBanner()
    failed_updates: list[str] = []
    monkeypatch.setattr(
        failed, "update", lambda value: failed_updates.append(_plain(value))
    )

    asyncio.run(failed._fetch_and_update())
    assert failed._project_url is None
    assert failed_updates == []
