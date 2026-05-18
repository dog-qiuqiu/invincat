"""Create skills command implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from invincat_cli import theme
from invincat_cli.skills import commands as _commands

if TYPE_CHECKING:
    from invincat_cli.io.output import OutputFormat


def _generate_template(skill_name: str) -> str:
    """Generate a `SKILL.md` template for a new skill.

    The template follows the Agent Skills spec
    (https://agentskills.io/specification) and the skill-creator guidance:

    - Description includes "when to use" trigger information (not the body)
    - Body contains only instructions loaded after the skill triggers

    Args:
        skill_name: Name of the skill (used in frontmatter and heading).

    Returns:
        Complete `SKILL.md` content with YAML frontmatter and markdown body.
    """
    title = skill_name.title().replace("-", " ")
    description = (
        "TODO: Explain what this skill does and when to use it. "
        "Include specific triggers — scenarios, file types, or phrases "
        "that should activate this skill. Example: 'Create and edit PDF "
        "documents. Use when the user asks to merge, split, fill, or "
        "annotate PDF files.'"
    )
    return f"""---
name: {skill_name}
description: "{description}"
# (Warning: SKILL.md files exceeding 10 MB are silently skipped at load time.)
# Optional fields per Agent Skills spec:
# license: Apache-2.0
# compatibility: Designed for Invincat CLI
# metadata:
#   author: your-org
#   version: "1.0"
# allowed-tools: Bash(git:*) Read
---

# {title}

## Overview

[TODO: 1-2 sentences explaining what this skill enables]

## Instructions

### Step 1: [First Action]
[Explain what to do first]

### Step 2: [Second Action]
[Explain what to do next]

### Step 3: [Final Action]
[Explain how to complete the task]

## Best Practices

- [Best practice 1]
- [Best practice 2]
- [Best practice 3]

## Examples

### Example 1: [Scenario Name]

**User Request:** "[Example user request]"

**Approach:**
1. [Step-by-step breakdown]
2. [Using tools and commands]
3. [Expected outcome]
"""


def _create(
    skill_name: str,
    agent: str,
    project: bool = False,
    *,
    output_format: OutputFormat = "text",
) -> None:
    """Create a new skill with a template SKILL.md file.

    Args:
        skill_name: Name of the skill to create.
        agent: Agent identifier for skills
        project: If True, create in project skills directory.
            If False, create in user skills directory.
        output_format: Output format — `'text'` (Rich) or `'json'`.

    Raises:
        SystemExit: If the skill name is invalid or the directory cannot be created.
    """
    from invincat_cli.config import Settings, console, get_glyphs

    # Validate skill name first (per Agent Skills spec)
    is_valid, error_msg = _commands._validate_name(skill_name)
    if not is_valid:
        console.print(f"[bold red]Error:[/bold red] Invalid skill name: {error_msg}")
        console.print(
            "[dim]Per Agent Skills spec: names must be lowercase alphanumeric "
            "with hyphens only.\n"
            "Examples: web-research, code-review, data-analysis[/dim]",
            style=theme.MUTED,
        )
        raise SystemExit(1)

    # Determine target directory
    settings = Settings.from_environment()
    if project:
        if not settings.project_root:
            console.print("[bold red]Error:[/bold red] Not in a project directory.")
            console.print(
                "[dim]Project skills require a .git directory "
                "in the project root.[/dim]",
                style=theme.MUTED,
            )
            raise SystemExit(1)
        skills_dir = settings.ensure_project_skills_dir()
        if skills_dir is None:
            console.print(
                "[bold red]Error:[/bold red] Could not create project skills directory."
            )
            raise SystemExit(1)
    else:
        skills_dir = settings.ensure_user_skills_dir(agent)

    skill_dir = skills_dir / skill_name

    # Validate the resolved path is within skills_dir
    is_valid_path, path_error = _commands._validate_skill_path(skill_dir, skills_dir)
    if not is_valid_path:
        console.print(f"[bold red]Error:[/bold red] {path_error}")
        raise SystemExit(1)

    if skill_dir.exists():
        if output_format == "json":
            from invincat_cli.io.output import write_json

            write_json(
                "skills create",
                {
                    "name": skill_name,
                    "path": str(skill_dir),
                    "project": project,
                    "already_existed": True,
                },
            )
            return
        console.print(
            f"Skill '{skill_name}' already exists at {skill_dir}",
            style=theme.MUTED,
        )
        return

    # Create skill directory
    skill_dir.mkdir(parents=True, exist_ok=True)

    template = _generate_template(skill_name)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(template)

    if output_format == "json":
        from invincat_cli.io.output import write_json

        write_json(
            "skills create",
            {
                "name": skill_name,
                "path": str(skill_dir),
                "project": project,
            },
        )
        return

    checkmark = get_glyphs().checkmark
    console.print(
        f"\n[bold]{checkmark} Skill '{skill_name}' created successfully![/bold]",
        style=theme.PRIMARY,
    )
    console.print(f"Location: {skill_dir}\n", style=theme.MUTED)
    console.print(
        "[dim]Edit the SKILL.md file to customize:\n"
        "  1. Update the description in YAML frontmatter\n"
        "  2. Fill in the instructions and examples\n"
        "  3. Add any supporting files (scripts, configs, etc.)\n"
        "\n"
        f"  nano {skill_md}\n"
        "\n"
        "  See examples/skills/ in the invincat-cli repo for example skills:\n"
        "   - web-research: Structured research workflow\n"
        "   - langgraph-docs: LangGraph documentation lookup\n"
        "\n"
        "   Copy an example:\n"
        "   cp -r examples/skills/web-research ~/.invincat/agent/skills/\n",
        style=theme.MUTED,
    )
