"""MCP tools loader facade for deepagents CLI."""

from __future__ import annotations

import logging
import shutil as shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from invincat_cli.mcp import config_loader as _config_loader
from invincat_cli.mcp.models import (
    MCPServerInfo as MCPServerInfo,
)
from invincat_cli.mcp.models import (
    MCPSessionManager as MCPSessionManager,
)
from invincat_cli.mcp.models import (
    MCPToolInfo as MCPToolInfo,
)

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

    from invincat_cli.project_utils import ProjectContext

logger = logging.getLogger(__name__)

_SUPPORTED_REMOTE_TYPES = _config_loader.SUPPORTED_REMOTE_TYPES


def _resolve_server_type(server_config: dict[str, Any]) -> str:
    from invincat_cli.mcp.config_loader import resolve_server_type

    return resolve_server_type(server_config)


def _validate_server_config(server_name: str, server_config: dict[str, Any]) -> None:
    from invincat_cli.mcp.config_loader import validate_server_config

    validate_server_config(server_name, server_config)


def load_mcp_config(config_path: str) -> dict[str, Any]:
    from invincat_cli.mcp.config_loader import load_mcp_config as _load

    return _load(config_path)


def _resolve_project_config_base(project_context: ProjectContext | None) -> Path:
    from invincat_cli.mcp.config_loader import resolve_project_config_base

    return resolve_project_config_base(project_context)


def discover_mcp_configs(
    *, project_context: ProjectContext | None = None
) -> list[Path]:
    from invincat_cli.mcp.config_loader import discover_mcp_configs as _discover

    return _discover(project_context=project_context)


def classify_discovered_configs(
    config_paths: list[Path],
) -> tuple[list[Path], list[Path]]:
    from invincat_cli.mcp.config_loader import classify_discovered_configs as _classify

    return _classify(config_paths)


def extract_stdio_server_commands(
    config: dict[str, Any],
) -> list[tuple[str, str, list[str]]]:
    from invincat_cli.mcp.config_loader import (
        extract_stdio_server_commands as _extract,
    )

    return _extract(config)


def extract_server_summaries(
    config: dict[str, Any],
) -> list[tuple[str, str, str]]:
    from invincat_cli.mcp.config_loader import extract_server_summaries as _extract

    return _extract(config)


def _empty_project_config(config: dict[str, Any]) -> dict[str, Any]:
    from invincat_cli.mcp.config_loader import empty_project_config

    return empty_project_config(config)


def merge_mcp_configs(configs: list[dict[str, Any]]) -> dict[str, Any]:
    from invincat_cli.mcp.config_loader import merge_mcp_configs as _merge

    return _merge(configs)


def load_mcp_config_lenient(config_path: Path) -> dict[str, Any] | None:
    from invincat_cli.mcp.config_loader import load_mcp_config_lenient as _load

    return _load(config_path)


def _check_stdio_server(server_name: str, server_config: dict[str, Any]) -> None:
    """Verify that a stdio server's command exists on PATH."""
    command = server_config.get("command")
    if command is None:
        msg = f"MCP server '{server_name}': missing 'command' in config."
        raise RuntimeError(msg)
    if shutil.which(command) is None:
        msg = (
            f"MCP server '{server_name}': command '{command}' not found on PATH. "
            "Install it or check your MCP config."
        )
        raise RuntimeError(msg)


async def _check_remote_server(server_name: str, server_config: dict[str, Any]) -> None:
    from invincat_cli.mcp.loader import check_remote_server

    await check_remote_server(server_name, server_config)


async def _load_tools_from_config(
    config: dict[str, Any],
) -> tuple[list[BaseTool], MCPSessionManager, list[MCPServerInfo]]:
    from invincat_cli.mcp.loader import load_tools_from_config

    return await load_tools_from_config(config)


async def get_mcp_tools(
    config_path: str,
) -> tuple[list[BaseTool], MCPSessionManager, list[MCPServerInfo]]:
    """Load MCP tools from configuration file with stateful sessions."""
    config = load_mcp_config(config_path)
    return await _load_tools_from_config(config)


async def resolve_and_load_mcp_tools(
    *,
    explicit_config_path: str | None = None,
    no_mcp: bool = False,
    trust_project_mcp: bool | None = None,
    project_context: ProjectContext | None = None,
) -> tuple[list[BaseTool], MCPSessionManager | None, list[MCPServerInfo]]:
    """Resolve MCP config, apply project trust gating, and load tools."""
    if no_mcp:
        return [], None, []

    try:
        config_paths = discover_mcp_configs(project_context=project_context)
    except (OSError, RuntimeError):
        logger.warning("MCP config auto-discovery failed", exc_info=True)
        config_paths = []

    user_configs, project_configs = classify_discovered_configs(config_paths)

    configs: list[dict[str, Any]] = []
    for path in user_configs:
        cfg = load_mcp_config_lenient(path)
        if cfg is not None:
            configs.append(cfg)

    project_root = str(_resolve_project_config_base(project_context).resolve())
    project_fingerprint: str | None = None

    for path in project_configs:
        cfg = load_mcp_config_lenient(path)
        if cfg is None:
            continue

        server_summaries = extract_server_summaries(cfg)
        if not server_summaries:
            continue

        if trust_project_mcp is True:
            configs.append(cfg)
        elif trust_project_mcp is False:
            _append_untrusted_project_placeholder(cfg, configs)
            _log_skipped_project_servers(server_summaries, trusted=False)
        else:
            from invincat_cli.mcp.trust import (
                compute_config_fingerprint,
                is_project_mcp_trusted,
            )

            if project_fingerprint is None:
                project_fingerprint = compute_config_fingerprint(project_configs)
            if is_project_mcp_trusted(project_root, project_fingerprint):
                configs.append(cfg)
            else:
                _append_untrusted_project_placeholder(cfg, configs)
                _log_skipped_project_servers(server_summaries, trusted=None)

    if explicit_config_path:
        config_path = (
            str(project_context.resolve_user_path(explicit_config_path))
            if project_context is not None
            else explicit_config_path
        )
        configs.append(load_mcp_config(config_path))

    if not configs:
        return [], None, []

    merged = merge_mcp_configs(configs)
    if not merged.get("mcpServers"):
        return [], None, []

    try:
        for server_name, server_config in merged["mcpServers"].items():
            _validate_server_config(server_name, server_config)
    except (TypeError, ValueError) as e:
        msg = f"Invalid MCP server configuration: {e}"
        raise RuntimeError(msg) from e

    return await _load_tools_from_config(merged)


def _append_untrusted_project_placeholder(
    cfg: dict[str, Any],
    configs: list[dict[str, Any]],
) -> None:
    filtered = _empty_project_config(cfg)
    if filtered.get("mcpServers"):
        configs.append(filtered)


def _log_skipped_project_servers(
    server_summaries: list[tuple[str, str, str]],
    *,
    trusted: bool | None,
) -> None:
    skipped = [f"{name} ({typ}): {detail}" for name, typ, detail in server_summaries]
    if trusted is False:
        logger.warning(
            "Skipped untrusted project MCP servers: %s",
            "; ".join(skipped),
        )
    else:
        logger.warning(
            "Skipped untrusted project MCP servers "
            "(config changed or not yet approved): %s",
            "; ".join(skipped),
        )
