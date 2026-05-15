"""List skills command implementation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from invincat_cli import theme

if TYPE_CHECKING:
    from invincat_cli.io.output import OutputFormat


def _list(
    agent: str, *, project: bool = False, output_format: OutputFormat = "text"
) -> None:
    """List all available skills for the specified agent.

    Args:
        agent: Agent identifier for skills (default: agent).
        project: If True, show only project skills.
            If False, show all skills (user + project).
        output_format: Output format — `'text'` (Rich) or `'json'`.
    """
    from rich.markup import escape as escape_markup

    from invincat_cli.config import Settings, console, get_glyphs
    from invincat_cli.skills.load import list_skills

    settings = Settings.from_environment()
    user_skills_dir = settings.get_user_skills_dir(agent)
    project_skills_dir = settings.get_project_skills_dir()
    user_agent_skills_dir = settings.get_user_agent_skills_dir()
    project_agent_skills_dir = settings.get_project_agent_skills_dir()

    # If --project flag is used, only show project skills
    if project:
        if not project_skills_dir:
            if output_format == "json":
                from invincat_cli.io.output import write_json

                write_json("skills list", [])
                return
            console.print("[yellow]Not in a project directory.[/yellow]")
            console.print(
                "[dim]Project skills require a .git directory "
                "in the project root.[/dim]",
                style=theme.MUTED,
            )
            return

        # Check both project skill directories
        has_deepagents_skills = project_skills_dir.exists() and any(
            project_skills_dir.iterdir()
        )
        has_agent_skills = (
            project_agent_skills_dir
            and project_agent_skills_dir.exists()
            and any(project_agent_skills_dir.iterdir())
        )

        if not has_deepagents_skills and not has_agent_skills:
            if output_format == "json":
                from invincat_cli.io.output import write_json

                write_json("skills list", [])
                return
            console.print("[yellow]No project skills found.[/yellow]")
            console.print(
                f"[dim]Project skills will be created in {project_skills_dir}/ "
                "when you add them.[/dim]",
                style=theme.MUTED,
            )
            console.print(
                "\n[dim]Create a project skill:\n"
                "  deepagents skills create my-skill --project[/dim]",
                style=theme.MUTED,
            )
            return

        skills = list_skills(
            user_skills_dir=None,
            project_skills_dir=project_skills_dir,
            user_agent_skills_dir=None,
            project_agent_skills_dir=project_agent_skills_dir,
        )

        if output_format == "json":
            from invincat_cli.io.output import write_json

            write_json("skills list", [dict(s) for s in skills])
            return

        console.print("\n[bold]Project Skills:[/bold]\n", style=theme.PRIMARY)
    else:
        # Load skills from all directories (including built-in)
        skills = list_skills(
            built_in_skills_dir=settings.get_built_in_skills_dir(),
            user_skills_dir=user_skills_dir,
            project_skills_dir=project_skills_dir,
            user_agent_skills_dir=user_agent_skills_dir,
            project_agent_skills_dir=project_agent_skills_dir,
        )

        if output_format == "json":
            from invincat_cli.io.output import write_json

            write_json("skills list", [dict(s) for s in skills])
            return

        if not skills:
            console.print()
            console.print("[yellow]No skills found.[/yellow]")
            console.print()
            console.print(
                "[dim]Skills are loaded from these directories "
                "(highest precedence first):\n"
                "  1. .agents/skills/                 project skills\n"
                "  2. .invincat/skills/             project skills (alias)\n"
                "  3. ~/.agents/skills/               user skills\n"
                "  4. ~/.invincat/<agent>/skills/   user skills (alias)\n"
                "  5. <package>/built_in_skills/      built-in skills[/dim]",
                style=theme.MUTED,
            )
            console.print(
                "\n[dim]Create your first skill:\n"
                "  deepagents skills create my-skill[/dim]",
                style=theme.MUTED,
            )
            return

        console.print("\n[bold]Available Skills:[/bold]\n", style=theme.PRIMARY)

    # Group skills by source
    user_skills = [s for s in skills if s["source"] == "user"]
    project_skills_list = [s for s in skills if s["source"] == "project"]
    built_in_skills_list = [s for s in skills if s["source"] == "built-in"]

    # Show user skills
    if user_skills and not project:
        console.print("[bold cyan]User Skills:[/bold cyan]", style=theme.PRIMARY)
        bullet = get_glyphs().bullet
        for skill in user_skills:
            skill_path = Path(skill["path"])
            name = escape_markup(skill["name"])
            console.print(f"  {bullet} [bold]{name}[/bold]", style=theme.PRIMARY)
            console.print(
                f"    {escape_markup(str(skill_path.parent))}/",
                style=theme.MUTED,
            )
            console.print()
            console.print(
                f"    {escape_markup(skill['description'])}",
                style=theme.MUTED,
            )
            console.print()

    # Show project skills
    if project_skills_list:
        if not project and user_skills:
            console.print()
        console.print("[bold green]Project Skills:[/bold green]", style=theme.PRIMARY)
        bullet = get_glyphs().bullet
        for skill in project_skills_list:
            skill_path = Path(skill["path"])
            name = escape_markup(skill["name"])
            console.print(f"  {bullet} [bold]{name}[/bold]", style=theme.PRIMARY)
            console.print(
                f"    {escape_markup(str(skill_path.parent))}/",
                style=theme.MUTED,
            )
            console.print()
            console.print(
                f"    {escape_markup(skill['description'])}",
                style=theme.MUTED,
            )
            console.print()

    # Show built-in skills
    if built_in_skills_list and not project:
        if user_skills or project_skills_list:
            console.print()
        console.print(
            "[bold magenta]Built-in Skills:[/bold magenta]", style=theme.PRIMARY
        )
        bullet = get_glyphs().bullet
        for skill in built_in_skills_list:
            name = escape_markup(skill["name"])
            console.print(f"  {bullet} [bold]{name}[/bold]", style=theme.PRIMARY)
            console.print()
            console.print(
                f"    {escape_markup(skill['description'])}",
                style=theme.MUTED,
            )
            console.print()
