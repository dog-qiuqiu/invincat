from __future__ import annotations

from typing import Any

import pytest

import invincat_cli.presentation.help as ui


class _FakeConsole:
    def __init__(self) -> None:
        self.messages: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def print(self, *args: Any, **kwargs: Any) -> None:
        self.messages.append((args, kwargs))

    @property
    def text(self) -> str:
        return "\n".join(
            str(args[0])
            for args, _kwargs in self.messages
            if args and args[0] is not None
        )


@pytest.fixture
def fake_console(monkeypatch: pytest.MonkeyPatch) -> _FakeConsole:
    console = _FakeConsole()
    monkeypatch.setattr(ui, "console", console)
    return console


def test_print_option_section_includes_custom_and_shared_options(
    fake_console: _FakeConsole,
) -> None:
    ui._print_option_section("  --agent NAME            Agent identifier")

    text = fake_console.text
    assert "Options:" in text
    assert "--agent NAME" in text
    assert "--json" in text
    assert "-h, --help" in text


@pytest.mark.parametrize(
    ("show_fn", "expected"),
    [
        (ui.show_list_help, "invincat-cli agents list [options]"),
        (ui.show_agents_help, "invincat-cli agents <command> [options]"),
        (ui.show_skills_help, "invincat-cli skills <command> [options]"),
        (ui.show_skills_list_help, "invincat-cli skills list [options]"),
        (ui.show_skills_create_help, "invincat-cli skills create <name> [options]"),
        (ui.show_skills_info_help, "invincat-cli skills info <name> [options]"),
        (ui.show_skills_delete_help, "invincat-cli skills delete <name> [options]"),
        (ui.show_update_help, "invincat-cli update [options]"),
        (ui.show_threads_help, "invincat-cli threads <command> [options]"),
        (ui.show_threads_delete_help, "invincat-cli threads delete <ID> [options]"),
        (ui.show_threads_list_help, "invincat-cli threads list [options]"),
    ],
)
def test_subcommand_help_screens_print_usage_and_common_flags(
    fake_console: _FakeConsole,
    show_fn: Any,
    expected: str,
) -> None:
    show_fn()

    text = fake_console.text
    assert expected in text
    assert "--json" in text
    assert "-h, --help" in text


def test_show_help_prints_standard_top_level_help(
    monkeypatch: pytest.MonkeyPatch,
    fake_console: _FakeConsole,
) -> None:
    monkeypatch.setattr(ui, "_get_editable_install_path", lambda: None)
    monkeypatch.setattr(ui, "_is_editable_install", lambda: False)

    ui.show_help()

    text = fake_console.text
    assert "invincat-cli" in text
    assert "invincat-cli [OPTIONS]" in text
    assert "--non-interactive MSG" in text
    assert "--acp" in text


def test_show_help_prints_editable_install_path(
    monkeypatch: pytest.MonkeyPatch,
    fake_console: _FakeConsole,
) -> None:
    monkeypatch.setattr(ui, "_get_editable_install_path", lambda: "/tmp/dev")
    monkeypatch.setattr(ui, "_is_editable_install", lambda: True)

    ui.show_help()

    assert "(local: /tmp/dev)" in fake_console.text
