"""Tests for skill runtime helpers used by the Textual app."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from invincat_cli.app_runtime.skill import (
    build_skill_agent_metadata,
    build_skill_invocation_prompt,
    discover_skills_and_roots,
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


def test_discover_skills_and_roots_collects_all_configured_roots(
    monkeypatch, tmp_path: Path
) -> None:
    built_in = tmp_path / "built_in"
    user = tmp_path / "user"
    project = tmp_path / "project"
    user_agent = tmp_path / "user_agent"
    project_agent = tmp_path / "project_agent"
    user_claude = tmp_path / "user_claude"
    extra = tmp_path / "extra"
    for path in (
        built_in,
        user,
        project,
        user_agent,
        project_agent,
        user_claude,
        extra,
    ):
        path.mkdir()
    discovered = [_skill("alpha")]
    calls: dict[str, object] = {}

    def _list_skills(**kwargs):
        calls.update(kwargs)
        return discovered

    monkeypatch.setattr("invincat_cli.skills.load.list_skills", _list_skills)
    settings = SimpleNamespace(
        get_built_in_skills_dir=lambda: built_in,
        get_user_skills_dir=lambda assistant_id: user / assistant_id,
        get_project_skills_dir=lambda: project,
        ensure_user_agent_skills_dir=lambda: user_agent,
        get_project_agent_skills_dir=lambda: project_agent,
        get_user_claude_skills_dir=lambda: user_claude,
        get_project_claude_skills_dir=lambda: None,
        get_extra_skills_dirs=lambda: [extra],
    )

    skills, roots = discover_skills_and_roots(settings=settings, assistant_id="agent")

    assert skills is discovered
    assert calls["built_in_skills_dir"] == built_in
    assert calls["user_skills_dir"] == user / "agent"
    assert calls["project_claude_skills_dir"] is None
    assert roots == [
        built_in.resolve(),
        (user / "agent").resolve(),
        project.resolve(),
        user_agent.resolve(),
        project_agent.resolve(),
        user_claude.resolve(),
        extra.resolve(),
    ]


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
