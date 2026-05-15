"""MCP connection health checks and tool loading."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Any

from invincat_cli.mcp.models import MCPServerInfo, MCPSessionManager, MCPToolInfo

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool
    from langchain_mcp_adapters.client import Connection


def check_stdio_server(server_name: str, server_config: dict[str, Any]) -> None:
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


async def check_remote_server(
    server_name: str, server_config: dict[str, Any]
) -> None:
    """Check network connectivity to a remote MCP server URL."""
    import httpx

    url = server_config.get("url")
    if url is None:
        msg = f"MCP server '{server_name}': missing 'url' in config."
        raise RuntimeError(msg)
    try:
        async with httpx.AsyncClient() as client:
            await client.head(url, timeout=2)
    except (httpx.TransportError, httpx.InvalidURL, OSError) as exc:
        msg = (
            f"MCP server '{server_name}': URL '{url}' is unreachable: {exc}. "
            "Check that the URL is correct and the server is running."
        )
        raise RuntimeError(msg) from exc


async def load_tools_from_config(
    config: dict[str, Any],
) -> tuple[list[BaseTool], MCPSessionManager, list[MCPServerInfo]]:
    """Build MCP connections from a validated config and load tools."""
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_mcp_adapters.sessions import (
        SSEConnection,
        StdioConnection,
        StreamableHttpConnection,
    )
    from langchain_mcp_adapters.tools import load_mcp_tools

    from invincat_cli.mcp import tools as _tools

    errors: list[str] = []
    for server_name, server_config in config["mcpServers"].items():
        server_type = _tools._resolve_server_type(server_config)
        try:
            if server_type in _tools._SUPPORTED_REMOTE_TYPES:
                await _tools._check_remote_server(server_name, server_config)
            elif server_type == "stdio":
                _tools._check_stdio_server(server_name, server_config)
        except RuntimeError as exc:
            errors.append(str(exc))
    if errors:
        msg = "Pre-flight health check(s) failed:\n" + "\n".join(
            f"  - {e}" for e in errors
        )
        raise RuntimeError(msg)

    connections: dict[str, Connection] = {}
    for server_name, server_config in config["mcpServers"].items():
        server_type = _tools._resolve_server_type(server_config)

        if server_type in _tools._SUPPORTED_REMOTE_TYPES:
            if server_type == "http":
                conn: Connection = StreamableHttpConnection(
                    transport="streamable_http",
                    url=server_config["url"],
                )
            else:
                conn = SSEConnection(
                    transport="sse",
                    url=server_config["url"],
                )
            if "headers" in server_config:
                conn["headers"] = server_config["headers"]
            connections[server_name] = conn
        else:
            connections[server_name] = StdioConnection(
                command=server_config["command"],
                args=server_config.get("args", []),
                env=server_config.get("env") or None,
                transport="stdio",
            )

    manager = MCPSessionManager()

    try:
        client = MultiServerMCPClient(connections=connections)
        manager.client = client
    except Exception as e:
        await manager.cleanup()
        error_msg = f"Failed to initialize MCP client: {e}"
        raise RuntimeError(error_msg) from e

    try:
        all_tools: list[BaseTool] = []
        server_infos: list[MCPServerInfo] = []
        for server_name, server_config in config["mcpServers"].items():
            session = await manager.exit_stack.enter_async_context(
                client.session(server_name)
            )
            tools = await load_mcp_tools(
                session, server_name=server_name, tool_name_prefix=True
            )
            all_tools.extend(tools)
            server_infos.append(
                MCPServerInfo(
                    name=server_name,
                    transport=_tools._resolve_server_type(server_config),
                    tools=[
                        MCPToolInfo(name=t.name, description=t.description or "")
                        for t in tools
                    ],
                )
            )
    except Exception as e:
        await manager.cleanup()
        error_msg = (
            f"Failed to load tools from MCP server '{server_name}': {e}\n"
            "For stdio servers: Check that the command and args are correct,"
            " and that the MCP server is installed"
            " (e.g., run 'npx -y <package>' manually to test).\n"
            "For sse/http servers: Check that the URL is correct"
            " and the server is running."
        )
        raise RuntimeError(error_msg) from e

    return all_tools, manager, server_infos
