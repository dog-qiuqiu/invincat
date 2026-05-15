"""MCP metadata and session manager models."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_mcp_adapters.client import MultiServerMCPClient


@dataclass
class MCPToolInfo:
    """Metadata for a single MCP tool."""

    name: str
    description: str


@dataclass
class MCPServerInfo:
    """Metadata for a connected MCP server and its tools."""

    name: str
    transport: str
    tools: list[MCPToolInfo] = field(default_factory=list)


class MCPSessionManager:
    """Manages persistent MCP sessions for stateful stdio servers."""

    def __init__(self) -> None:
        self.client: MultiServerMCPClient | None = None
        self.exit_stack = AsyncExitStack()

    async def cleanup(self) -> None:
        """Clean up all managed sessions and close connections."""
        await self.exit_stack.aclose()
