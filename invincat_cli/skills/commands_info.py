"""Show skill information command implementation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from invincat_cli import theme
from invincat_cli.skills import commands as _commands

if TYPE_CHECKING:
    from invincat_cli.io.output import OutputFormat


def _info(
    skill_name: str,
    *,
    agent: str = "agent",
    project: bool = False,
    output_format: OutputFormat = "text",
) -> None:
    """Show detailed information about a specific skill.

    Args:
        skill_name: Name of the skill to show info for.
        agent: Agent identifier for skills (default: agent).
        project: If True, only search in project skills.
            If False, search in both user and project skills.
        output_format: Output format — `'text'` (Rich) or `'json'`.

    Raises:
        SystemExit: If the skill is not found or not in a project directory.
    """
    from rich.markup import escape as escape_markup

    from invincat_cli.config import Settings, console
    from invincat_cli.skills.load import list_skills, load_skill_content

    settings = Settings.from_environment()
    user_skills_dir = settings.get_user_skills_dir(agent)
    project_skills_dir = settings.get_project_skills_dir()
    user_agent_skills_dir = settings.get_user_agent_skills_dir()
    project_agent_skills_dir = settings.get_project_agent_skills_dir()

    # Load skills based on --project flag
    if project:
        if not project_skills_dir:
            console.print("[bold red]Error:[/bold red] Not in a project directory.")
            raise SystemExit(1)
        skills = list_skills(
            user_skills_dir=None,
            project_skills_dir=project_skills_dir,
            user_agent_skills_dir=None,
            project_agent_skills_dir=project_agent_skills_dir,
        )
    else:
        skills = list_skills(
            built_in_skills_dir=settings.get_built_in_skills_dir(),
            user_skills_dir=user_skills_dir,
            project_skills_dir=project_skills_dir,
            user_agent_skills_dir=user_agent_skills_dir,
            project_agent_skills_dir=project_agent_skills_dir,
        )

    # Find the skill
    skill = next((s for s in skills if s["name"] == skill_name), None)

    if not skill:
        console.print(f"[bold red]Error:[/bold red] Skill '{skill_name}' not found.")
        console.print("\n[dim]Available skills:[/dim]", style=theme.MUTED)
        for s in skills:
            console.print(f"  - {s['name']}", style=theme.MUTED)
        raise SystemExit(1)

    if output_format == "json":
        from invincat_cli.io.output import write_json

        write_json("skills info", dict(skill))
        return

    # Read the full SKILL.md file with containment checks
    skill_path = Path(skill["path"])
    allowed_roots = [
        d.resolve()
        for d in (
            settings.get_built_in_skills_dir(),
            user_skills_dir,
            project_skills_dir,
            user_agent_skills_dir,
            project_agent_skills_dir,
            settings.get_user_claude_skills_dir(),
            settings.get_project_claude_skills_dir(),
        )
        if d is not None
    ]
    allowed_roots.extend(d.resolve() for d in settings.get_extra_skills_dirs())
    try:
        skill_content = load_skill_content(str(skill_path), allowed_roots=allowed_roots)
    except PermissionError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise SystemExit(1) from e
    except OSError as e:
        console.print(
            f"[bold red]Error:[/bold red] Could not read SKILL.md for "
            f"'{skill_name}': {e}"
        )
        raise SystemExit(1) from e
    if skill_content is None:
        console.print(
            f"[bold red]Error:[/bold red] Could not read SKILL.md for '{skill_name}'. "
            "Check file encoding and permissions."
        )
        raise SystemExit(1)

    # Determine source label
    source_labels = {
        "project": ("Project Skill", "green"),
        "user": ("User Skill", "cyan"),
        "built-in": ("Built-in Skill", "magenta"),
    }
    source_label, source_color = source_labels.get(skill["source"], ("Skill", "dim"))

    # Check if this project skill shadows a user skill with the same name.
    # This is a cosmetic hint — if the second list_skills() call fails
    # (e.g. permission error reading user dirs) we silently skip the warning
    # rather than crashing the entire `skills info` display.
    shadowed_user_skill = False
    if skill["source"] == "project" and not project:
        try:
            user_only = list_skills(
                user_skills_dir=user_skills_dir,
                project_skills_dir=None,
                user_agent_skills_dir=user_agent_skills_dir,
                project_agent_skills_dir=None,
            )
            shadowed_user_skill = any(s["name"] == skill_name for s in user_only)
        except Exception:  # noqa: BLE001, S110  # Shadow detection is cosmetic, safe to swallow
            pass

    console.print(
        f"\n[bold]Skill: {escape_markup(skill['name'])}[/bold] "
        f"[bold {source_color}]({source_label})[/bold {source_color}]\n",
        style=theme.PRIMARY,
    )
    if shadowed_user_skill:
        console.print(
            f"[yellow]Note: Overrides user skill '{escape_markup(skill_name)}' "
            "of the same name[/yellow]\n"
        )
    console.print(
        f"[bold]Location:[/bold] {escape_markup(str(skill_path.parent))}/\n",
        style=theme.MUTED,
    )
    console.print(
        f"[bold]Description:[/bold] {escape_markup(skill['description'])}\n",
        style=theme.MUTED,
    )

    # Show optional metadata fields
    for label, value in _commands._format_info_fields(skill):
        console.print(
            f"[bold]{label}:[/bold] {escape_markup(value)}\n",
            style=theme.MUTED,
        )

    # List supporting files
    skill_dir = skill_path.parent
    try:
        supporting_files = [f for f in skill_dir.iterdir() if f.name != "SKILL.md"]
    except OSError:
        supporting_files = []

    if supporting_files:
        console.print("[bold]Supporting Files:[/bold]", style=theme.MUTED)
        for file in supporting_files:
            console.print(f"  - {escape_markup(file.name)}", style=theme.MUTED)
        console.print()

    # Show the full SKILL.md content
    console.print("[bold]Full SKILL.md Content:[/bold]\n", style=theme.PRIMARY)
    console.print(skill_content, style=theme.MUTED)
    console.print()
