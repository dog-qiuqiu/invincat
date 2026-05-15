"""MCP inventory formatting for local context prompts."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from invincat_cli.mcp.tools import MCPServerInfo

_TOOL_NAME_DISPLAY_LIMIT = 10


def build_mcp_context(servers: list[MCPServerInfo]) -> str:
    """Format MCP server/tool inventory for the system prompt."""
    if not servers:
        return ""

    total_tools = sum(len(s.tools) for s in servers)
    lines = [f"**MCP Servers** ({len(servers)} servers, {total_tools} tools):"]

    for server in servers:
        if not server.tools:
            lines.append(f"- **{server.name}** ({server.transport}): (no tools)")
            continue

        names = [t.name for t in server.tools]
        if len(names) > _TOOL_NAME_DISPLAY_LIMIT:
            shown = ", ".join(names[:_TOOL_NAME_DISPLAY_LIMIT])
            remaining = len(names) - _TOOL_NAME_DISPLAY_LIMIT
            lines.append(
                f"- **{server.name}** ({server.transport}): "
                f"{shown}, and {remaining} more"
            )
        else:
            lines.append(
                f"- **{server.name}** ({server.transport}): {', '.join(names)}"
            )

    return "\n".join(lines)
