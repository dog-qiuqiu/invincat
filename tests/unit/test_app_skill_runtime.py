"""Tests for skill runtime helpers used by the Textual app."""

from __future__ import annotations

from typing import cast

from invincat_cli.app_skill_runtime import (
    build_skill_agent_metadata,
    build_skill_invocation_prompt,
    find_skill,
)
from invincat_cli.skills.load import ExtendedSkillMetadata


def _skill(name: str) -> ExtendedSkillMetadata:
    return cast(
        ExtendedSkillMetadata,
        {
            "name": name,
            "description": "Does work",
            "source": "project",
            "path": "/tmp/SKILL.md",
        },
    )


def test_find_skill() -> None:
    skills = [_skill("alpha"), _skill("beta")]

    assert find_skill(skills, "beta") == skills[1]
    assert find_skill(skills, "missing") is None


def test_build_skill_invocation_prompt_without_args() -> None:
    prompt = build_skill_invocation_prompt(
        skill=_skill("alpha"),
        content="Use this workflow.",
        args="",
    )

    assert "skill `alpha`" in prompt
    assert "---\nUse this workflow.\n---" in prompt
    assert "User request" not in prompt


def test_build_skill_invocation_prompt_with_args() -> None:
    prompt = build_skill_invocation_prompt(
        skill=_skill("alpha"),
        content="Use this workflow.",
        args="finish the task",
    )

    assert prompt.endswith("**User request:** finish the task")


def test_build_skill_agent_metadata() -> None:
    assert build_skill_agent_metadata(skill=_skill("alpha"), args="do it") == {
        "__skill": {
            "name": "alpha",
            "description": "Does work",
            "source": "project",
            "args": "do it",
        }
    }
