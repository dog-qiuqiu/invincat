"""Skill runtime helpers for the Textual app."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from invincat_cli.skills.load import ExtendedSkillMetadata


def discover_skills_and_roots(
    *,
    settings: Any,
    assistant_id: str,
) -> tuple[list[ExtendedSkillMetadata], list[Path]]:
    """Discover skills and build pre-resolved containment roots."""
    from invincat_cli.skills.load import list_skills

    skills = list_skills(
        built_in_skills_dir=settings.get_built_in_skills_dir(),
        user_skills_dir=settings.get_user_skills_dir(assistant_id),
        project_skills_dir=settings.get_project_skills_dir(),
        user_agent_skills_dir=settings.ensure_user_agent_skills_dir(),
        project_agent_skills_dir=settings.get_project_agent_skills_dir(),
        user_claude_skills_dir=settings.get_user_claude_skills_dir(),
        project_claude_skills_dir=settings.get_project_claude_skills_dir(),
    )
    roots = [
        directory.resolve()
        for directory in (
            settings.get_built_in_skills_dir(),
            settings.get_user_skills_dir(assistant_id),
            settings.get_project_skills_dir(),
            settings.ensure_user_agent_skills_dir(),
            settings.get_project_agent_skills_dir(),
            settings.get_user_claude_skills_dir(),
            settings.get_project_claude_skills_dir(),
        )
        if directory is not None
    ]
    roots.extend(directory.resolve() for directory in settings.get_extra_skills_dirs())
    return skills, roots


def find_skill(
    skills: list[ExtendedSkillMetadata],
    skill_name: str,
) -> ExtendedSkillMetadata | None:
    """Return the discovered skill matching a command name."""
    return next((skill for skill in skills if skill["name"] == skill_name), None)


def build_skill_invocation_prompt(
    *,
    skill: ExtendedSkillMetadata,
    content: str,
    args: str,
) -> str:
    """Build the prompt envelope used to invoke a skill."""
    prompt = (
        f"I'm invoking the skill `{skill['name']}`. "
        "Below are the full instructions from the skill's SKILL.md file. "
        "Follow these instructions to complete the task.\n\n"
        f"---\n{content}\n---"
    )
    if args:
        prompt += f"\n\n**User request:** {args}"
    return prompt


def build_skill_agent_metadata(
    *,
    skill: ExtendedSkillMetadata,
    args: str,
) -> dict[str, Any]:
    """Build metadata attached to the agent message for skill invocations."""
    return {
        "__skill": {
            "name": skill["name"],
            "description": str(skill.get("description", "")),
            "source": str(skill.get("source", "")),
            "args": args,
        },
    }
