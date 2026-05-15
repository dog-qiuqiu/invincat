"""Agent discovery and listing helpers."""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from invincat_cli.io.output import OutputFormat

    try:
        from deepagents.middleware.async_subagents import AsyncSubAgent
    except ImportError:  # pragma: no cover - typing fallback
        AsyncSubAgent = dict  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)


def load_async_subagents(config_path: Path | None = None) -> list[AsyncSubAgent]:
    """Load remote async subagent definitions from `config.toml`."""
    from invincat_cli import agent as _agent

    if config_path is None:
        config_path = _agent.Path.home() / ".invincat" / "config.toml"

    if not config_path.exists():
        return []

    try:
        with config_path.open("rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, PermissionError, OSError) as e:
        logger.warning("Could not read async subagents from %s: %s", config_path, e)
        _agent.console.print(
            f"[bold yellow]Warning:[/bold yellow] Could not read async subagents "
            f"from {config_path}: {e}",
        )
        return []

    section = data.get("async_subagents")
    if not isinstance(section, dict):
        return []

    required = {"description", "graph_id"}
    agents: list[AsyncSubAgent] = []
    for name, spec in section.items():
        if not isinstance(spec, dict):
            logger.warning("Skipping async subagent '%s': expected a table", name)
            continue
        missing = required - spec.keys()
        if missing:
            logger.warning(
                "Skipping async subagent '%s': missing fields %s", name, missing
            )
            continue
        agent: AsyncSubAgent = {
            "name": name,
            "description": spec["description"],
            "graph_id": spec["graph_id"],
        }
        if "url" in spec and isinstance(spec["url"], str):
            agent["url"] = spec["url"]
        if "headers" in spec and isinstance(spec["headers"], dict):
            agent["headers"] = spec["headers"]
        agents.append(agent)

    return agents


def _agent_metadata(agent_path: Path) -> dict[str, object]:
    from invincat_cli import agent as _agent

    agent_name = agent_path.name
    return {
        "name": agent_name,
        "path": str(agent_path),
        "has_memory": (agent_path / "memory_user.json").exists(),
        "has_skills": (agent_path / "skills").is_dir(),
        "is_default": agent_name == _agent.DEFAULT_AGENT_NAME,
    }


def _write_agent_list_json(agent_paths: list[Path]) -> None:
    from invincat_cli.io.output import write_json

    write_json(
        "list",
        [_agent_metadata(agent_path) for agent_path in agent_paths if agent_path.is_dir()],
    )


def list_agents(*, output_format: OutputFormat = "text") -> None:
    """List user-level agent data directories."""
    from rich.markup import escape as escape_markup

    from invincat_cli import agent as _agent

    agents_dir = _agent.settings.user_deepagents_dir

    if not agents_dir.exists() or not any(agents_dir.iterdir()):
        if output_format == "json":
            from invincat_cli.io.output import write_json

            write_json("list", [])
            return
        _agent.console.print("[yellow]No agents found.[/yellow]")
        _agent.console.print(
            "[dim]Agents will be created in ~/.invincat/ "
            "when you first use them.[/dim]",
            style=_agent.theme.MUTED,
        )
        return

    agent_paths = sorted(agents_dir.iterdir())
    if output_format == "json":
        _write_agent_list_json(agent_paths)
        return

    _agent.console.print("\n[bold]Available Agents:[/bold]\n", style=_agent.theme.PRIMARY)

    for agent_path in agent_paths:
        if agent_path.is_dir():
            agent_name = escape_markup(agent_path.name)
            is_default = agent_path.name == _agent.DEFAULT_AGENT_NAME
            default_label = " [dim](default)[/dim]" if is_default else ""
            bullet = _agent.get_glyphs().bullet
            markers = []
            if (agent_path / "memory_user.json").exists():
                markers.append("memory")
            if (agent_path / "skills").is_dir():
                markers.append("skills")
            marker_label = f" [dim]({', '.join(markers)})[/dim]" if markers else ""
            _agent.console.print(
                f"  {bullet} [bold]{agent_name}[/bold]{default_label}{marker_label}",
                style=_agent.theme.PRIMARY,
            )
            _agent.console.print(
                f"    {escape_markup(str(agent_path))}",
                style=_agent.theme.MUTED,
            )

    _agent.console.print()
