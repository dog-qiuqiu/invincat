"""Delete skills command implementation."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from invincat_cli import theme
from invincat_cli.skills import commands as _commands

if TYPE_CHECKING:
    from invincat_cli.io.output import OutputFormat


def _delete(
    skill_name: str,
    *,
    agent: str = "agent",
    project: bool = False,
    force: bool = False,
    dry_run: bool = False,
    output_format: OutputFormat = "text",
) -> None:
    """Delete a skill directory after validation and optional user confirmation.

    Validates the skill name, locates the skill in user or project directories,
    confirms the deletion with the user (unless `force` is `True`), and
    recursively removes the skill directory.

    Args:
        skill_name: Name of the skill to delete.
        agent: Agent identifier for skills.
        project: If `True`, only search in project skills.

            If `False`, search in both user and project skills.
        force: If `True`, skip confirmation prompt.
        dry_run: If `True`, print what would be removed without deleting.
        output_format: Output format — `'text'` (Rich) or `'json'`.

    Raises:
        SystemExit: If the deletion fails or a safety check is violated.
    """
    from rich.markup import escape as escape_markup

    from invincat_cli.config import Settings, console, get_glyphs
    from invincat_cli.skills.load import list_skills

    # Validate skill name first (per Agent Skills spec)
    is_valid, error_msg = _commands._validate_name(skill_name)
    if not is_valid:
        console.print(f"[bold red]Error:[/bold red] Invalid skill name: {error_msg}")
        raise SystemExit(1)

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
            source_tag = "[project]" if s["source"] == "project" else "[user]"
            console.print(f"  - {s['name']} {source_tag}", style=theme.MUTED)
        raise SystemExit(1)

    skill_path = Path(skill["path"])
    skill_dir = skill_path.parent

    # Validate the path is safe to delete.  Skills may come from either the
    # DeepAgents-specific directory or the `.agents/skills` alias, so choose the
    # concrete root that actually contains the discovered skill.
    candidate_base_dirs = (
        [project_skills_dir, project_agent_skills_dir]
        if skill["source"] == "project"
        else [user_skills_dir, user_agent_skills_dir]
    )
    base_dir = _commands._find_containing_skills_dir(skill_dir, candidate_base_dirs)
    if not base_dir:
        console.print(
            "[bold red]Error:[/bold red] Skill directory is not inside a known skills directory. "
            "Refusing to delete."
        )
        raise SystemExit(1)

    if dry_run:
        if output_format == "json":
            from invincat_cli.io.output import write_json

            write_json(
                "skills delete",
                {
                    "name": skill_name,
                    "path": str(skill_dir),
                    "dry_run": True,
                },
            )
            return
        console.print(
            f"Would delete skill '{skill_name}' at {skill_dir}",
        )
        console.print("No changes made.", style=theme.MUTED)
        return

    # Display confirmation summary (text mode only)
    if output_format != "json":
        source_label = "Project Skill" if skill["source"] == "project" else "User Skill"
        source_color = "green" if skill["source"] == "project" else "cyan"

        # Count files for the confirmation summary (display-only; a permission
        # error in a subdirectory should not abort the entire delete flow).
        try:
            file_count = sum(1 for f in skill_dir.rglob("*") if f.is_file())
        except OSError:
            file_count = -1

        console.print(
            f"\n[bold]Skill:[/bold] {escape_markup(skill_name)}"
            f" [bold {source_color}]({source_label})[/bold {source_color}]",
            style=theme.PRIMARY,
        )
        console.print(
            f"[bold]Location:[/bold] {escape_markup(str(skill_dir))}/",
            style=theme.MUTED,
        )
        if file_count >= 0:
            console.print(
                f"[bold]Files:[/bold] {file_count} file(s) will be deleted\n",
                style=theme.MUTED,
            )
        else:
            console.print(
                "[bold]Files:[/bold] (unable to count files)\n",
                style=theme.MUTED,
            )

    # Confirmation (skip in JSON mode — no interactive prompt)
    if not force and output_format != "json":
        console.print(
            "[yellow]Are you sure you want to delete this skill? (y/N)[/yellow] ",
            end="",
        )
        try:
            response = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Cancelled.[/dim]")
            return

        if response not in {"y", "yes"}:
            console.print("[dim]Cancelled.[/dim]")
            return

    # Re-validate immediately before deletion to narrow the TOCTOU window
    # (the user may have paused at the confirmation prompt).
    if skill_dir.is_symlink():
        console.print(
            "[bold red]Error:[/bold red] Skill directory is a symlink. "
            "Refusing to delete for safety."
        )
        raise SystemExit(1)

    base_dir = _commands._find_containing_skills_dir(skill_dir, candidate_base_dirs)
    if not base_dir:
        console.print(
            "[bold red]Error:[/bold red] Skill directory is not inside a known skills directory. "
            "Refusing to delete."
        )
        raise SystemExit(1)

    # Delete the skill directory
    try:
        shutil.rmtree(skill_dir)
    except OSError as e:
        console.print(
            f"[bold red]Error:[/bold red] Failed to fully delete skill: {e}\n"
            f"[yellow]Warning:[/yellow] Some files may have been partially removed.\n"
            f"Please inspect: {skill_dir}/"
        )
        raise SystemExit(1) from e

    if output_format == "json":
        from invincat_cli.io.output import write_json

        write_json(
            "skills delete",
            {
                "name": skill_name,
                "path": str(skill_dir),
                "deleted": True,
            },
        )
        return

    checkmark = get_glyphs().checkmark
    console.print(
        f"{checkmark} Skill '{skill_name}' deleted successfully!",
        style=theme.PRIMARY,
    )
