"""Tests for agent system prompt construction."""

from __future__ import annotations

from invincat_cli.agent import get_system_prompt


def test_system_prompt_includes_current_time_reference() -> None:
    prompt = get_system_prompt("test-agent", cwd="/tmp/project")

    assert "### Current Date and Time" in prompt
    assert "Local time is `" in prompt
    assert "relative scheduling phrases" in prompt
