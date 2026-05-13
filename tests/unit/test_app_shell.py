"""Tests for shell helpers used by the Textual app."""

from __future__ import annotations

from invincat_cli.app_shell import (
    format_shell_output,
    is_interactive_command,
    shell_termination_strategy,
    should_start_new_shell_session,
)


def test_is_interactive_command_matches_basename() -> None:
    assert is_interactive_command("/usr/bin/vim file.txt") is True
    assert is_interactive_command("python -i") is True
    assert is_interactive_command("ls -la") is False
    assert is_interactive_command("") is False


def test_format_shell_output_combines_stdout_and_stderr() -> None:
    assert format_shell_output(b"hello\n", b"warn\n") == "hello\n[stderr]\nwarn"
    assert format_shell_output(b"", b"warn\n") == "\n[stderr]\nwarn"
    assert format_shell_output(b"hello\n", b"") == "hello"
    assert format_shell_output(None, None) == ""


def test_shell_platform_helpers() -> None:
    assert should_start_new_shell_session("linux") is True
    assert should_start_new_shell_session("darwin") is True
    assert should_start_new_shell_session("win32") is False
    assert shell_termination_strategy("linux") == "process_group"
    assert shell_termination_strategy("win32") == "process"
