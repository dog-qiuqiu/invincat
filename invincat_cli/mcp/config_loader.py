"""MCP configuration loading, validation, and discovery."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from invincat_cli.project_utils import ProjectContext

logger = logging.getLogger(__name__)

SUPPORTED_REMOTE_TYPES = {"sse", "http"}


def resolve_server_type(server_config: dict[str, Any]) -> str:
    """Determine the transport type for a server config."""
    t = server_config.get("type")
    if t is not None:
        return t
    return server_config.get("transport", "stdio")


def validate_server_config(server_name: str, server_config: dict[str, Any]) -> None:
    """Validate a single server configuration."""
    if not isinstance(server_config, dict):
        error_msg = f"Server '{server_name}' config must be a dictionary"
        raise TypeError(error_msg)

    server_type = resolve_server_type(server_config)

    if server_type in SUPPORTED_REMOTE_TYPES:
        if "url" not in server_config:
            error_msg = (
                f"Server '{server_name}' with type '{server_type}'"
                " missing required 'url' field"
            )
            raise ValueError(error_msg)
        headers = server_config.get("headers")
        if headers is not None and not isinstance(headers, dict):
            error_msg = f"Server '{server_name}' 'headers' must be a dictionary"
            raise TypeError(error_msg)
    elif server_type == "stdio":
        if "command" not in server_config:
            error_msg = f"Server '{server_name}' missing required 'command' field"
            raise ValueError(error_msg)
        if "args" in server_config and not isinstance(server_config["args"], list):
            error_msg = f"Server '{server_name}' 'args' must be a list"
            raise TypeError(error_msg)
        if "env" in server_config and not isinstance(server_config["env"], dict):
            error_msg = f"Server '{server_name}' 'env' must be a dictionary"
            raise TypeError(error_msg)
    else:
        error_msg = (
            f"Server '{server_name}' has unsupported transport type '{server_type}'. "
            "Supported types: stdio, sse, http"
        )
        raise ValueError(error_msg)


def load_mcp_config(config_path: str) -> dict[str, Any]:
    """Load and validate MCP configuration from JSON file."""
    path = Path(config_path)

    if not path.exists():
        error_msg = f"MCP config file not found: {config_path}"
        raise FileNotFoundError(error_msg)

    try:
        with path.open(encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        error_msg = f"Invalid JSON in MCP config file: {e.msg}"
        raise json.JSONDecodeError(error_msg, e.doc, e.pos) from e

    if "mcpServers" not in config:
        error_msg = (
            "MCP config must contain 'mcpServers' field. "
            'Expected format: {"mcpServers": {"server-name": {...}}}'
        )
        raise ValueError(error_msg)

    if not isinstance(config["mcpServers"], dict):
        error_msg = "'mcpServers' field must be a dictionary"
        raise TypeError(error_msg)

    if not config["mcpServers"]:
        error_msg = "'mcpServers' field is empty - no servers configured"
        raise ValueError(error_msg)

    for server_name, server_config in config["mcpServers"].items():
        validate_server_config(server_name, server_config)

    return config


def resolve_project_config_base(project_context: ProjectContext | None) -> Path:
    """Resolve the base directory for project-level MCP configuration lookup."""
    if project_context is not None:
        return project_context.project_root or project_context.user_cwd

    from invincat_cli.project_utils import find_project_root

    return find_project_root() or Path.cwd()


def discover_mcp_configs(
    *, project_context: ProjectContext | None = None
) -> list[Path]:
    """Find MCP config files from standard locations."""
    user_dir = Path.home() / ".invincat"
    project_root = resolve_project_config_base(project_context)

    candidates = [
        user_dir / ".mcp.json",
        project_root / ".invincat" / ".mcp.json",
        project_root / ".mcp.json",
    ]

    found: list[Path] = []
    for path in candidates:
        try:
            if path.is_file():
                found.append(path)
        except OSError:
            logger.warning("Could not check MCP config %s", path, exc_info=True)
    return found


def classify_discovered_configs(
    config_paths: list[Path],
) -> tuple[list[Path], list[Path]]:
    """Split discovered config paths into user-level and project-level."""
    user_dir = Path.home() / ".invincat"
    user: list[Path] = []
    project: list[Path] = []
    for path in config_paths:
        try:
            if path.resolve().is_relative_to(user_dir.resolve()):
                user.append(path)
            else:
                project.append(path)
        except (OSError, ValueError):
            project.append(path)
    return user, project


def extract_stdio_server_commands(
    config: dict[str, Any],
) -> list[tuple[str, str, list[str]]]:
    """Extract stdio server entries from a parsed MCP config."""
    results: list[tuple[str, str, list[str]]] = []
    servers = config.get("mcpServers", {})
    if not isinstance(servers, dict):
        return results
    for name, srv in servers.items():
        if not isinstance(srv, dict):
            continue
        if resolve_server_type(srv) == "stdio":
            results.append((name, srv.get("command", ""), srv.get("args", [])))
    return results


def extract_server_summaries(
    config: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """Extract display summaries for all configured MCP servers."""
    results: list[tuple[str, str, str]] = []
    servers = config.get("mcpServers", {})
    if not isinstance(servers, dict):
        return results
    for name, srv in servers.items():
        if not isinstance(srv, dict):
            continue
        server_type = resolve_server_type(srv)
        if server_type == "stdio":
            args = srv.get("args", [])
            detail = f"{srv.get('command', '')} {' '.join(args)}".strip()
        else:
            detail = str(srv.get("url", ""))
        results.append((name, server_type, detail))
    return results


def empty_project_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return an MCP config with all project-level servers removed."""
    return {"mcpServers": {}}


def merge_mcp_configs(configs: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge multiple MCP config dicts by server name."""
    merged: dict[str, Any] = {}
    for cfg in configs:
        servers = cfg.get("mcpServers")
        if isinstance(servers, dict):
            merged.update(servers)
    return {"mcpServers": merged}


def load_mcp_config_lenient(config_path: Path) -> dict[str, Any] | None:
    """Load an MCP config file, returning None on any error."""
    try:
        return load_mcp_config(str(config_path))
    except FileNotFoundError:
        return None
    except OSError as e:
        logger.warning("Skipping unreadable MCP config %s: %s", config_path, e)
        return None
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("Skipping invalid MCP config %s: %s", config_path, e)
        return None
