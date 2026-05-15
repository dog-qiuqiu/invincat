from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from invincat_cli.io import editor


def test_resolve_editor_uses_visual_before_editor(monkeypatch) -> None:
    monkeypatch.setenv("VISUAL", "code --reuse-window")
    monkeypatch.setenv("EDITOR", "vim")

    assert editor.resolve_editor() == ["code", "--reuse-window"]


def test_resolve_editor_falls_back_to_platform_defaults(monkeypatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr(editor.sys, "platform", "win32")

    assert editor.resolve_editor() == ["notepad"]

    monkeypatch.setattr(editor.sys, "platform", "darwin")
    assert editor.resolve_editor() == ["vi"]


def test_resolve_editor_returns_none_for_empty_command(monkeypatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "   ")

    assert editor.resolve_editor() is None


def test_prepare_command_adds_wait_and_vim_flags() -> None:
    assert editor._prepare_command(["code"], "file.md") == [
        "code",
        "--wait",
        "file.md",
    ]
    assert editor._prepare_command(["vim"], "file.md") == [
        "vim",
        "-i",
        "NONE",
        "file.md",
    ]
    assert editor._prepare_command(["code", "--wait"], "file.md") == [
        "code",
        "--wait",
        "file.md",
    ]


def test_open_in_editor_reads_back_normalized_content(monkeypatch) -> None:
    def run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        Path(cmd[-1]).write_text("edited\r\ntext\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(editor, "resolve_editor", lambda: ["fake-editor"])
    monkeypatch.setattr(editor.subprocess, "run", run)

    assert editor.open_in_editor("initial") == "edited\ntext"


def test_open_in_editor_returns_none_on_empty_or_failed_edit(monkeypatch) -> None:
    def empty_edit(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        Path(cmd[-1]).write_text("   \n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(editor, "resolve_editor", lambda: ["fake-editor"])
    monkeypatch.setattr(editor.subprocess, "run", empty_edit)

    assert editor.open_in_editor("initial") is None

    monkeypatch.setattr(
        editor.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )

    assert editor.open_in_editor("initial") is None


def test_open_in_editor_returns_none_when_editor_command_is_empty(monkeypatch) -> None:
    monkeypatch.setattr(editor, "resolve_editor", lambda: None)

    assert editor.open_in_editor("initial") is None


def test_open_in_editor_returns_none_when_command_missing(monkeypatch) -> None:
    def missing(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(editor, "resolve_editor", lambda: ["missing-editor"])
    monkeypatch.setattr(editor.subprocess, "run", missing)

    assert editor.open_in_editor("initial") is None


def test_open_in_editor_returns_none_on_unexpected_editor_failure(monkeypatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("editor failed")

    monkeypatch.setattr(editor, "resolve_editor", lambda: ["bad-editor"])
    monkeypatch.setattr(editor.subprocess, "run", fail)

    assert editor.open_in_editor("initial") is None
