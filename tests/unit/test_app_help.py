"""Tests for interactive app help content."""

from __future__ import annotations

from invincat_cli.app_help import build_help_content
from invincat_cli.command_registry import COMMANDS
from invincat_cli.core.version import DOCS_URL


def test_help_content_uses_command_registry() -> None:
    text = str(build_help_content())

    assert DOCS_URL in text
    for command in {"/help", "/plan", "/update", "/auto-update"}:
        assert command in text

    registry_names = [entry.name for entry in COMMANDS]
    assert registry_names.index("/update") < registry_names.index("/auto-update")
