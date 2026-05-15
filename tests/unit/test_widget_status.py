from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.content import Content
from textual.css.query import NoMatches

from invincat_cli import config as config_mod
from invincat_cli.widgets import status as status_mod
from invincat_cli.widgets.status import ModelLabel, StatusBar, _take_right_cells


class _FakeStatic:
    def __init__(self) -> None:
        self.value: object | None = None
        self.classes: set[str] = set()
        self.display = True

    def update(self, value: object) -> None:
        self.value = value

    def add_class(self, *classes: str) -> None:
        self.classes.update(classes)

    def remove_class(self, *classes: str) -> None:
        self.classes.difference_update(classes)


class _FakeContext:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def __enter__(self) -> _FakeContext:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _plain(value: object) -> str:
    if isinstance(value, Content):
        return value.plain
    return str(value)


def _patch_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    colors = SimpleNamespace(primary="#00ffff")
    monkeypatch.setattr(status_mod.theme, "get_theme_colors", lambda *_args: colors)


def test_take_right_cells_respects_display_width() -> None:
    assert _take_right_cells("abcdef", 3) == "def"
    assert _take_right_cells("a界b", 3) == "界b"
    assert _take_right_cells("abc", 0) == ""


def test_model_label_reports_width_and_renders_full_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme(monkeypatch)
    monkeypatch.setattr(
        ModelLabel,
        "content_size",
        property(lambda _self: SimpleNamespace(width=20)),
    )
    label = ModelLabel()
    label.prefix = "main:"
    label.model = "gpt"

    assert label.get_content_width(SimpleNamespace(), SimpleNamespace()) == 8
    assert label.render().plain == "main:gpt"


def test_model_label_empty_plain_and_single_cell_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme(monkeypatch)
    label = ModelLabel()
    assert label.get_content_width(SimpleNamespace(), SimpleNamespace()) == 0

    monkeypatch.setattr(
        ModelLabel,
        "content_size",
        property(lambda _self: SimpleNamespace(width=0)),
    )
    label.model = "gpt"
    assert label.render() == ""

    monkeypatch.setattr(
        ModelLabel,
        "content_size",
        property(lambda _self: SimpleNamespace(width=20)),
    )
    label.prefix = ""
    assert label.render().plain == "gpt"

    monkeypatch.setattr(
        ModelLabel,
        "content_size",
        property(lambda _self: SimpleNamespace(width=1)),
    )
    label.model = "very-long-model"
    assert label.render().plain == "\u2026"


def test_model_label_left_truncates_when_narrow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme(monkeypatch)
    monkeypatch.setattr(
        ModelLabel,
        "content_size",
        property(lambda _self: SimpleNamespace(width=5)),
    )
    label = ModelLabel()
    label.prefix = "main:"
    label.model = "very-long-model"

    assert label.render().plain == "\u2026odel"


def test_model_label_truncated_prefix_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme(monkeypatch)
    monkeypatch.setattr(
        ModelLabel,
        "content_size",
        property(lambda _self: SimpleNamespace(width=3)),
    )
    label = ModelLabel()
    label.prefix = "\u2026"
    label.model = "gpt"

    assert label.render().plain == "\u2026pt"


def test_status_bar_compose_yields_expected_widgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(status_mod, "Horizontal", _FakeContext)
    monkeypatch.setattr(
        status_mod,
        "t",
        lambda key: {
            "status.plan_mode": "Plan",
            "approval.approve": "Approve",
        }.get(key, key),
    )
    bar = StatusBar(cwd="/tmp/project")

    children = list(bar.compose())

    ids = [getattr(child, "id", None) for child in children]
    assert ids == [
        "mode-indicator",
        "plan-mode-indicator",
        "auto-approve-indicator",
        "status-message",
        "cwd-display",
        "branch-display",
        "message-count-display",
        "tokens-display",
        "model-display",
        "memory-model-display",
    ]
    assert _plain(children[1]._Static__content) == "Plan"  # noqa: SLF001
    assert _plain(children[2]._Static__content) == "Approve | shift+tab"  # noqa: SLF001


def test_status_bar_watchers_update_mode_plan_and_auto_approve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widgets = {
        "#mode-indicator": _FakeStatic(),
        "#plan-mode-indicator": _FakeStatic(),
        "#auto-approve-indicator": _FakeStatic(),
    }
    bar = StatusBar(cwd="/tmp/project")
    monkeypatch.setattr(bar, "query_one", lambda selector, _cls=None: widgets[selector])
    monkeypatch.setattr(
        status_mod,
        "t",
        lambda key: {
            "status.shell_mode": "Shell",
            "status.cmd_mode": "Command",
            "approval.auto_approve": "Auto",
            "approval.approve": "Approve",
        }.get(key, key),
    )

    bar.watch_mode("shell")
    assert widgets["#mode-indicator"].value == "Shell"
    assert widgets["#mode-indicator"].classes == {"shell"}

    bar.watch_mode("command")
    assert widgets["#mode-indicator"].value == "Command"
    assert widgets["#mode-indicator"].classes == {"command"}

    bar.watch_mode("normal")
    assert widgets["#mode-indicator"].value == ""
    assert widgets["#mode-indicator"].classes == {"normal"}

    bar.watch_plan_mode(True)
    assert "on" in widgets["#plan-mode-indicator"].classes
    bar.watch_plan_mode(False)
    assert "on" not in widgets["#plan-mode-indicator"].classes

    bar.watch_auto_approve(True)
    assert _plain(widgets["#auto-approve-indicator"].value) == "Auto | shift+tab"
    assert widgets["#auto-approve-indicator"].classes == {"on"}

    bar.watch_auto_approve(False)
    assert _plain(widgets["#auto-approve-indicator"].value) == "Approve | shift+tab"
    assert widgets["#auto-approve-indicator"].classes == {"off"}


def test_status_bar_watchers_ignore_missing_widgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bar = StatusBar(cwd="/tmp/project")
    monkeypatch.setattr(
        bar,
        "query_one",
        lambda *_args: (_ for _ in ()).throw(NoMatches("missing")),
    )

    bar.watch_mode("shell")
    bar.watch_plan_mode(True)
    bar.watch_auto_approve(True)
    bar.watch_message_count(1)
    bar._render_tokens(1)
    bar.hide_tokens()


def test_status_bar_text_watchers_and_missing_widgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widgets = {
        "#status-message": _FakeStatic(),
        "#branch-display": _FakeStatic(),
        "#cwd-display": _FakeStatic(),
    }
    bar = StatusBar(cwd="/tmp/project")
    monkeypatch.setattr(
        bar,
        "query_one",
        lambda selector, _cls=None: widgets[selector],
    )
    monkeypatch.setattr(
        status_mod, "get_glyphs", lambda: SimpleNamespace(git_branch="git:")
    )

    bar.watch_status_message("Thinking hard")
    assert widgets["#status-message"].value == "Thinking hard"
    assert widgets["#status-message"].classes == {"thinking"}

    bar.watch_status_message("更新记忆")
    assert widgets["#status-message"].classes == {"memory"}

    bar.watch_status_message("")
    assert widgets["#status-message"].value == ""

    bar.watch_branch("main")
    assert widgets["#branch-display"].value == "git: main"

    bar.watch_cwd("/tmp/project")
    assert widgets["#cwd-display"].value == "/tmp/project"

    monkeypatch.setattr(
        bar,
        "query_one",
        lambda *_args: (_ for _ in ()).throw(NoMatches("missing")),
    )
    bar.watch_status_message("ignored")
    bar.watch_branch("ignored")
    bar.watch_cwd("ignored")


def test_status_bar_format_cwd_home_and_home_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    home = tmp_path / "home"
    project = home / "repo"
    project.mkdir(parents=True)
    bar = StatusBar(cwd=project)
    monkeypatch.setattr(Path, "home", lambda: home)

    assert bar._format_cwd(str(project)) == "~/repo"

    monkeypatch.setattr(
        Path,
        "home",
        lambda: (_ for _ in ()).throw(RuntimeError("no home")),
    )
    assert bar._format_cwd(str(project)) == str(project)


def test_status_bar_counts_and_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme(monkeypatch)
    widgets = {
        "#message-count-display": _FakeStatic(),
        "#tokens-display": _FakeStatic(),
    }
    bar = StatusBar(cwd="/tmp/project")
    monkeypatch.setattr(bar, "query_one", lambda selector, _cls=None: widgets[selector])
    monkeypatch.setattr(config_mod.settings, "model_context_limit", 2000)

    bar.watch_message_count(3)
    assert _plain(widgets["#message-count-display"].value) == "messages: 3"
    bar.watch_message_count(0)
    assert widgets["#message-count-display"].value == ""

    bar._render_tokens(1500)
    assert _plain(widgets["#tokens-display"].value) == "tokens: 1.5K (75%)"
    assert widgets["#tokens-display"].classes == {"warn"}

    bar._render_tokens(1900, approximate=True)
    assert _plain(widgets["#tokens-display"].value) == "tokens: ~1.9K (95%)"
    assert widgets["#tokens-display"].classes == {"danger"}

    bar._render_tokens(999)
    assert _plain(widgets["#tokens-display"].value) == "tokens: 999 (49%)"

    bar._render_tokens(1_200_000)
    assert _plain(widgets["#tokens-display"].value) == "tokens: 1.2M (60000%)"
    assert widgets["#tokens-display"].classes == {"danger"}

    monkeypatch.setattr(config_mod.settings, "model_context_limit", 0)
    bar._render_tokens(42)
    assert _plain(widgets["#tokens-display"].value) == "tokens: 42"

    bar._render_tokens(0)
    assert widgets["#tokens-display"].value == ""

    bar.tokens = 42
    bar.set_tokens(42, approximate=True)
    assert _plain(widgets["#tokens-display"].value) == "tokens: ~42"

    bar.hide_tokens()
    assert widgets["#tokens-display"].value == ""


def test_status_bar_model_setters_and_resize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = ModelLabel()
    memory = ModelLabel()
    widgets = {
        "#model-display": primary,
        "#memory-model-display": memory,
        "#branch-display": _FakeStatic(),
        "#cwd-display": _FakeStatic(),
    }
    bar = StatusBar(cwd="/tmp/project")
    monkeypatch.setattr(bar, "query_one", lambda selector, _cls=None: widgets[selector])
    monkeypatch.setattr(
        status_mod,
        "t",
        lambda key: {
            "model.target_primary": "Main",
            "model.target_memory": "Memory",
        }.get(key, key),
    )

    bar.set_model(provider="openai", model="gpt")
    assert (primary.provider, primary.model, primary.prefix) == (
        "openai",
        "gpt",
        "Main:",
    )
    assert (memory.provider, memory.model, memory.prefix) == (
        "openai",
        "gpt",
        "Memory:",
    )

    bar.set_memory_model(provider="anthropic", model="claude", follow_primary=False)
    assert (memory.provider, memory.model, memory.prefix) == (
        "anthropic",
        "claude",
        "Memory:",
    )

    bar.set_model(provider="openai", model="gpt-5")
    assert (memory.provider, memory.model) == ("anthropic", "claude")

    bar.on_resize(SimpleNamespace(size=SimpleNamespace(width=80)))
    assert widgets["#branch-display"].display is False
    assert widgets["#cwd-display"].display is True


def test_status_bar_on_mount_initializes_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = ModelLabel()
    memory = ModelLabel()
    cwd_display = _FakeStatic()
    widgets = {
        "#cwd-display": cwd_display,
        "#model-display": primary,
        "#memory-model-display": memory,
    }
    bar = StatusBar(cwd="/tmp/project")
    monkeypatch.setattr(bar, "query_one", lambda selector, _cls=None: widgets[selector])
    monkeypatch.setattr(config_mod.settings, "model_provider", "openai")
    monkeypatch.setattr(config_mod.settings, "model_name", "gpt")
    monkeypatch.setattr(
        status_mod,
        "t",
        lambda key: {
            "model.target_primary": "Main",
            "model.target_memory": "Memory",
        }.get(key, key),
    )

    bar.on_mount()

    assert cwd_display.value == "/tmp/project"
    assert (primary.provider, primary.model, primary.prefix) == (
        "openai",
        "gpt",
        "Main:",
    )
    assert (memory.provider, memory.model, memory.prefix) == (
        "openai",
        "gpt",
        "Memory:",
    )


def test_status_bar_state_setters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widgets = {
        "#mode-indicator": _FakeStatic(),
        "#auto-approve-indicator": _FakeStatic(),
        "#plan-mode-indicator": _FakeStatic(),
        "#status-message": _FakeStatic(),
        "#tokens-display": _FakeStatic(),
        "#message-count-display": _FakeStatic(),
    }
    bar = StatusBar(cwd="/tmp/project")
    monkeypatch.setattr(bar, "query_one", lambda selector, _cls=None: widgets[selector])
    monkeypatch.setattr(config_mod.settings, "model_context_limit", 0)
    _patch_theme(monkeypatch)

    bar.set_mode("shell")
    bar.set_auto_approve(enabled=True)
    bar.set_status_message("executing")
    bar.set_plan_mode(enabled=True)
    bar.set_tokens(7)
    bar.set_message_count(2)

    assert bar.mode == "shell"
    assert bar.auto_approve is True
    assert bar.status_message == "executing"
    assert bar.plan_mode is True
    assert bar.tokens == 7
    assert bar.message_count == 2
