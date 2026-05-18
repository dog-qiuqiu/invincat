"""CLI commands for skill management.

These commands are registered with the CLI via main.py:
- invincat-cli skills list [options]
- invincat-cli skills create <name> [options]
- invincat-cli skills info <name> [options]
- invincat-cli skills delete <name> [options]
"""

from __future__ import annotations

import argparse
import shutil as shutil  # noqa: F401
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from deepagents.middleware.skills import SkillMetadata

from invincat_cli import theme

MAX_SKILL_NAME_LENGTH = 64


def _validate_name(name: str) -> tuple[bool, str]:
    """Validate name per Agent Skills spec.

    Requirements (https://agentskills.io/specification):
    - Max 64 characters
    - Unicode lowercase alphanumeric and hyphens only
    - Cannot start or end with hyphen
    - No consecutive hyphens
    - No path traversal sequences

    Unicode lowercase alphanumeric means any character where
    `c.isalpha() and c.islower()` or `c.isdigit()` returns `True`,
    which covers accented Latin characters (e.g., `'cafe'`,
    `'uber-tool'`) and other scripts.  This matches the SDK's
    `_validate_skill_name` implementation.

    Args:
        name: The name to validate.

    Returns:
        Tuple of (is_valid, error_message). If valid, error_message is empty.
    """
    # Check for empty or whitespace-only names
    if not name or not name.strip():
        return False, "cannot be empty"

    # Check length (spec: max 64 chars)
    if len(name) > MAX_SKILL_NAME_LENGTH:
        return False, "cannot exceed 64 characters"

    # Check for path traversal sequences (CLI-specific; the SDK validates
    # against the directory name instead, but the CLI accepts user input
    # directly so we need explicit path-safety checks)
    if ".." in name or "/" in name or "\\" in name:
        return False, "cannot contain path components"

    # Structural hyphen checks
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return (
            False,
            "must be lowercase alphanumeric with single hyphens only",
        )

    # Character-by-character check (matches SDK's _validate_skill_name)
    for c in name:
        if c == "-":
            continue
        if (c.isalpha() and c.islower()) or c.isdigit():
            continue
        return (
            False,
            "must be lowercase alphanumeric with single hyphens only",
        )

    return True, ""


def _validate_agent_name(name: str) -> tuple[bool, str]:
    """Validate an agent identifier used as part of the user skills path."""
    if not name or not name.strip():
        return False, "cannot be empty"
    if len(name) > MAX_SKILL_NAME_LENGTH:
        return False, "cannot exceed 64 characters"
    if ".." in name or "/" in name or "\\" in name:
        return False, "cannot contain path components"
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return (
            False,
            "must be lowercase alphanumeric with hyphens or underscores only",
        )

    for c in name:
        if c in {"-", "_"}:
            continue
        if (c.isalpha() and c.islower()) or c.isdigit():
            continue
        return (
            False,
            "must be lowercase alphanumeric with hyphens or underscores only",
        )

    return True, ""


def _validate_skill_path(skill_dir: Path, base_dir: Path) -> tuple[bool, str]:
    """Validate that the resolved skill directory is within the base directory.

    Args:
        skill_dir: The skill directory path to validate
        base_dir: The base skills directory that should contain skill_dir

    Returns:
        Tuple of (is_valid, error_message). If valid, error_message is empty.
    """
    try:
        # Resolve both paths to their canonical form
        resolved_skill = skill_dir.resolve()
        resolved_base = base_dir.resolve()

        # Check if skill_dir is within base_dir
        if not resolved_skill.is_relative_to(resolved_base):
            return False, f"Skill directory must be within {base_dir}"
    except (OSError, RuntimeError) as e:
        return False, f"Invalid path: {e}"
    else:
        return True, ""


def _find_containing_skills_dir(
    skill_dir: Path,
    candidate_dirs: list[Path | None],
) -> Path | None:
    """Return the candidate skills root that contains ``skill_dir``."""
    for base_dir in candidate_dirs:
        if base_dir is None:
            continue
        is_valid, _ = _validate_skill_path(skill_dir, base_dir)
        if is_valid:
            return base_dir
    return None


def _format_info_fields(skill: SkillMetadata) -> list[tuple[str, str]]:
    """Extract non-empty optional metadata fields for display.

    The upstream `_parse_skill_metadata` normalises empty/whitespace license
    and compatibility values to `None`, so the truthy checks below are
    sufficient.

    Args:
        skill: Skill metadata to extract display fields from.

    Returns:
        Ordered list of (label, value) tuples for non-empty fields.
            Fields appear in order: License, Compatibility, Allowed Tools,
            Metadata.
    """
    fields: list[tuple[str, str]] = []
    license_val = skill.get("license")
    if license_val:
        fields.append(("License", license_val))
    compat_val = skill.get("compatibility")
    if compat_val:
        fields.append(("Compatibility", compat_val))
    if skill.get("allowed_tools"):
        fields.append(
            ("Allowed Tools", ", ".join(str(t) for t in skill["allowed_tools"]))
        )
    meta = skill.get("metadata")
    if meta and isinstance(meta, dict):
        formatted = ", ".join(f"{k}={v}" for k, v in meta.items())
        fields.append(("Metadata", formatted))
    return fields


from invincat_cli.skills.commands_create import (  # noqa: E402, F401
    _create,
    _generate_template,
)
from invincat_cli.skills.commands_delete import _delete  # noqa: E402, F401
from invincat_cli.skills.commands_info import _info  # noqa: E402, F401
from invincat_cli.skills.commands_list import _list  # noqa: E402, F401


def setup_skills_parser(
    subparsers: Any,  # noqa: ANN401  # argparse subparsers uses dynamic typing
    *,
    make_help_action: Callable[[Callable[[], None]], type[argparse.Action]],
    add_output_args: Callable[[argparse.ArgumentParser], None] | None = None,
) -> argparse.ArgumentParser:
    """Setup the skills subcommand parser with all its subcommands.

    Each subcommand gets a dedicated help screen so that
    `invincat-cli skills -h` shows skills-specific help, not the
    global help.

    Args:
        subparsers: The parent subparsers object to add the skills parser to.
        make_help_action: Factory that accepts a zero-argument help
            callable and returns an argparse Action class wired to it.
        add_output_args: Optional hook to add a shared `--json` flag.

    Returns:
        The skills subparser for argument handling.
    """

    # Lazy wrapper: defers ui import until the help action fires.
    def _lazy_help(fn_name: str) -> Callable[[], None]:
        def _show() -> None:
            from invincat_cli.presentation import help as ui

            getattr(ui, fn_name)()

        return _show

    def help_parent(help_fn: Callable[[], None]) -> list[argparse.ArgumentParser]:
        parent = argparse.ArgumentParser(add_help=False)
        parent.add_argument("-h", "--help", action=make_help_action(help_fn))
        return [parent]

    skills_parser = subparsers.add_parser(
        "skills",
        help="Manage agent skills",
        description="Manage agent skills - list, create, view, and delete skills.",
        add_help=False,
        parents=help_parent(_lazy_help("show_skills_help")),
    )
    if add_output_args is not None:
        add_output_args(skills_parser)
    skills_subparsers = skills_parser.add_subparsers(
        dest="skills_command", help="Skills command"
    )

    # Skills list
    list_parser = skills_subparsers.add_parser(
        "list",
        aliases=["ls"],
        help="List all available skills",
        description=(
            "List skills from all four skill directories "
            "(user, user alias, project, project alias)."
        ),
        add_help=False,
        parents=help_parent(_lazy_help("show_skills_list_help")),
    )
    if add_output_args is not None:
        add_output_args(list_parser)
    list_parser.add_argument(
        "--agent",
        default="agent",
        help="Agent identifier for skills (default: agent)",
    )
    list_parser.add_argument(
        "--project",
        action="store_true",
        help="Show only project-level skills",
    )

    # Skills create
    create_parser = skills_subparsers.add_parser(
        "create",
        help="Create a new skill",
        description=(
            "Create a new skill with a template SKILL.md file. "
            "By default, skills are created in "
            "~/.invincat/<agent>/skills/. "
            "Use --project to create in the project's "
            ".invincat/skills/ directory."
        ),
        add_help=False,
        parents=help_parent(_lazy_help("show_skills_create_help")),
    )
    if add_output_args is not None:
        add_output_args(create_parser)
    create_parser.add_argument(
        "name",
        help="Name of the skill to create (e.g., web-research)",
    )
    create_parser.add_argument(
        "--agent",
        default="agent",
        help="Agent identifier for skills (default: agent)",
    )
    create_parser.add_argument(
        "--project",
        action="store_true",
        help="Create skill in project directory instead of user directory",
    )

    # Skills info
    info_parser = skills_subparsers.add_parser(
        "info",
        help="Show detailed information about a skill",
        description="Show detailed information about a specific skill",
        add_help=False,
        parents=help_parent(_lazy_help("show_skills_info_help")),
    )
    if add_output_args is not None:
        add_output_args(info_parser)
    info_parser.add_argument("name", help="Name of the skill to show info for")
    info_parser.add_argument(
        "--agent",
        default="agent",
        help="Agent identifier for skills (default: agent)",
    )
    info_parser.add_argument(
        "--project",
        action="store_true",
        help="Search only in project skills",
    )

    # Skills delete
    delete_parser = skills_subparsers.add_parser(
        "delete",
        help="Delete a skill",
        description="Delete a skill directory and all its contents",
        add_help=False,
        parents=help_parent(_lazy_help("show_skills_delete_help")),
    )
    if add_output_args is not None:
        add_output_args(delete_parser)
    delete_parser.add_argument("name", help="Name of the skill to delete")
    delete_parser.add_argument(
        "--agent",
        default="agent",
        help="Agent identifier for skills (default: agent)",
    )
    delete_parser.add_argument(
        "--project",
        action="store_true",
        help="Search only in project skills",
    )
    delete_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Skip confirmation prompt",
    )
    delete_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without making changes",
    )
    return skills_parser


def execute_skills_command(args: argparse.Namespace) -> None:
    """Execute skills subcommands based on parsed arguments.

    Args:
        args: Parsed command line arguments with skills_command attribute

    Raises:
        SystemExit: If the agent name is invalid.
    """
    from invincat_cli.config import console

    # validate agent argument
    if args.agent:
        is_valid, error_msg = _validate_agent_name(args.agent)
        if not is_valid:
            console.print(
                f"[bold red]Error:[/bold red] Invalid agent name: {error_msg}"
            )
            console.print(
                "[dim]Agent names must only contain letters, numbers, "
                "hyphens, and underscores.[/dim]",
                style=theme.MUTED,
            )
            raise SystemExit(1)

    output_format = getattr(args, "output_format", "text")

    # "ls" is an argparse alias for "list" — argparse stores the alias
    # as-is in the namespace, so we must match both values.
    if args.skills_command in {"list", "ls"}:
        _list(agent=args.agent, project=args.project, output_format=output_format)
    elif args.skills_command == "create":
        _create(
            args.name,
            agent=args.agent,
            project=args.project,
            output_format=output_format,
        )
    elif args.skills_command == "info":
        _info(
            args.name,
            agent=args.agent,
            project=args.project,
            output_format=output_format,
        )
    elif args.skills_command == "delete":
        _delete(
            args.name,
            agent=args.agent,
            project=args.project,
            force=args.force,
            dry_run=args.dry_run,
            output_format=output_format,
        )
    else:
        # No subcommand provided, show skills help screen
        from invincat_cli.presentation.help import show_skills_help

        show_skills_help()


__all__ = [
    "execute_skills_command",
    "setup_skills_parser",
]
