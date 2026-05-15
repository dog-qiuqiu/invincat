"""Tests for terminal compatibility helpers."""

from __future__ import annotations

import sys

from invincat_cli.app_runtime import terminal


class _FakeStderr:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.flushed = False

    def write(self, value: str) -> None:
        self.writes.append(value)

    def flush(self) -> None:
        self.flushed = True


def test_is_iterm_tty_requires_terminal_env_and_tty(monkeypatch) -> None:
    monkeypatch.setenv("LC_TERMINAL", "iTerm2")
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.setattr(terminal.os, "isatty", lambda fd: fd == 2)

    assert terminal._is_iterm_tty()

    monkeypatch.setattr(terminal.os, "isatty", lambda _fd: False)

    assert not terminal._is_iterm_tty()


def test_is_iterm_tty_accepts_term_program_and_missing_isatty(monkeypatch) -> None:
    monkeypatch.delenv("LC_TERMINAL", raising=False)
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.setattr(terminal.os, "isatty", lambda fd: fd == 2)

    assert terminal._is_iterm_tty()

    monkeypatch.delattr(terminal.os, "isatty")

    assert not terminal._is_iterm_tty()


def test_write_iterm_escape_writes_to_real_stderr_when_available(
    monkeypatch,
) -> None:
    fake_stderr = _FakeStderr()
    monkeypatch.setattr(terminal, "_is_iterm_tty", lambda: True)
    monkeypatch.setattr(sys, "__stderr__", fake_stderr)

    terminal._write_iterm_escape("sequence")

    assert fake_stderr.writes == ["sequence"]
    assert fake_stderr.flushed is True


def test_write_iterm_escape_ignores_non_iterm_tty(monkeypatch) -> None:
    fake_stderr = _FakeStderr()
    monkeypatch.setattr(terminal, "_is_iterm_tty", lambda: False)
    monkeypatch.setattr(sys, "__stderr__", fake_stderr)

    terminal._write_iterm_escape("sequence")

    assert fake_stderr.writes == []


def test_write_iterm_escape_ignores_os_errors_and_missing_stderr(
    monkeypatch,
) -> None:
    class BrokenStderr:
        def write(self, _value: str) -> None:
            raise OSError("closed")

    monkeypatch.setattr(terminal, "_is_iterm_tty", lambda: True)
    monkeypatch.setattr(sys, "__stderr__", BrokenStderr())

    terminal._write_iterm_escape("sequence")

    monkeypatch.setattr(sys, "__stderr__", None)

    terminal._write_iterm_escape("sequence")


def test_restore_cursor_guide_writes_enable_sequence(monkeypatch) -> None:
    writes: list[str] = []
    monkeypatch.setattr(terminal, "_write_iterm_escape", writes.append)

    terminal.restore_cursor_guide()

    assert writes == [terminal._ITERM_CURSOR_GUIDE_ON]


def test_disable_cursor_guide_registers_restore_once(monkeypatch) -> None:
    registered: list[object] = []
    monkeypatch.setattr(terminal, "_atexit_registered", False)
    monkeypatch.setattr(terminal, "_is_iterm_tty", lambda: True)
    monkeypatch.setattr(terminal, "_write_iterm_escape", lambda _sequence: None)
    monkeypatch.setattr(terminal.atexit, "register", registered.append)

    terminal.disable_cursor_guide()
    terminal.disable_cursor_guide()

    assert registered == [terminal.restore_cursor_guide]
