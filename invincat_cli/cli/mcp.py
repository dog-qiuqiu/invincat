"""MCP preload and trust checks used by the CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from invincat_cli.mcp.tools import MCPServerInfo


async def _preload_session_mcp_server_info(
    *,
    mcp_config_path: str | None,
    no_mcp: bool,
    trust_project_mcp: bool | None,
) -> list[MCPServerInfo] | None:
    """Load MCP metadata for the interactive TUI in server mode."""
    if no_mcp:
        return None

    from invincat_cli import main as _main
    from invincat_cli.mcp.tools import resolve_and_load_mcp_tools
    from invincat_cli.project_utils import ProjectContext

    session_manager = None
    try:
        try:
            project_context = ProjectContext.from_user_cwd(_main.Path.cwd())
        except OSError:
            _main.logger.warning("Could not determine working directory for MCP preload")
            project_context = None
        _tools, session_manager, server_info = await resolve_and_load_mcp_tools(
            explicit_config_path=mcp_config_path,
            no_mcp=no_mcp,
            trust_project_mcp=trust_project_mcp,
            project_context=project_context,
        )
        return server_info
    finally:
        if session_manager is not None:
            try:
                await session_manager.cleanup()
            except Exception:
                _main.logger.warning(
                    "MCP metadata preload cleanup failed",
                    exc_info=True,
                )


def _check_mcp_project_trust(*, trust_flag: bool = False) -> bool | None:
    """Check whether project-level MCP servers should be trusted."""
    from invincat_cli import main as _main
    from invincat_cli.mcp.tools import (
        classify_discovered_configs,
        discover_mcp_configs,
        extract_server_summaries,
        load_mcp_config_lenient,
    )
    from invincat_cli.project_utils import ProjectContext

    try:
        project_context = ProjectContext.from_user_cwd(_main.Path.cwd())
        config_paths = discover_mcp_configs(project_context=project_context)
    except (OSError, RuntimeError):
        return None

    _, project_configs = classify_discovered_configs(config_paths)
    if not project_configs:
        return None

    all_servers: list[tuple[str, str, str]] = []
    for path in project_configs:
        cfg = load_mcp_config_lenient(path)
        if cfg is not None:
            all_servers.extend(extract_server_summaries(cfg))

    if not all_servers:
        return None
    if trust_flag:
        return True

    from invincat_cli.mcp.trust import (
        compute_config_fingerprint,
        is_project_mcp_trusted,
        trust_project_mcp,
    )

    project_root = str(
        (project_context.project_root or project_context.user_cwd).resolve()
    )
    fingerprint = compute_config_fingerprint(project_configs)

    if is_project_mcp_trusted(project_root, fingerprint):
        return True

    from rich.console import Console as _Console

    prompt_console = _Console(stderr=True)
    prompt_console.print()
    prompt_console.print(
        "[bold yellow]Project MCP servers require approval:[/bold yellow]"
    )
    for name, transport, detail in all_servers:
        prompt_console.print(f'  [bold]"{name}"[/bold] ({transport}):  {detail}')
    prompt_console.print()

    try:
        answer = input("Allow? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""

    if answer == "y":
        trust_project_mcp(project_root, fingerprint)
        return True
    return False
