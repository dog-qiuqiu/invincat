"""Memory middleware for loading and refreshing persistent memory files."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)

logger = logging.getLogger(__name__)


class RefreshableMemoryMiddleware(AgentMiddleware):
    """Wrapper around MemoryMiddleware that supports memory refresh.

    This class wraps the standard MemoryMiddleware and adds support for
    refreshing memory contents when they are set to None. This allows
    memory to be reloaded during a session after memory files are updated.

    Usage:
        middleware = RefreshableMemoryMiddleware(
            backend=FilesystemBackend(),
            sources=["~/.invincat/agent/AGENTS.md"],
        )
    """

    def __init__(self, *, backend: Any, sources: list[str]) -> None:
        """Initialize the refreshable memory middleware.

        Args:
            backend: Backend instance for file operations.
            sources: List of memory file paths to load.
        """
        from deepagents.middleware.memory import MemoryMiddleware

        self._memory_middleware = MemoryMiddleware(backend=backend, sources=sources)
        self.sources = sources

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to the wrapped MemoryMiddleware.

        Args:
            name: Attribute name to access.

        Returns:
            The attribute value from the wrapped middleware.
        """
        return getattr(self._memory_middleware, name)

    def before_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        """Load memory content before agent execution.

        Reloads when ``memory_contents`` is absent or ``None`` (the sentinel
        set by ``MemoryAgentMiddleware.aafter_agent`` to trigger a refresh).

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update with memory_contents populated, or None to skip.
        """
        if state.get("memory_contents") is None:
            logger.debug("Refreshing memory contents")
            return self._memory_middleware.before_agent(state, runtime, None)
        return None

    async def abefore_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        """Async load memory content before agent execution.

        Reloads when ``memory_contents`` is absent or ``None`` (the sentinel
        set by ``MemoryAgentMiddleware.aafter_agent`` to trigger a refresh).

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update with memory_contents populated, or None to skip.
        """
        if state.get("memory_contents") is None:
            logger.debug("Refreshing memory contents (async)")
            return await self._memory_middleware.abefore_agent(state, runtime, None)
        return None

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        """Delegate to wrapped MemoryMiddleware.

        Args:
            request: The model request being processed.
            handler: The handler function to call.

        Returns:
            The model response from the handler.
        """
        return self._memory_middleware.wrap_model_call(request, handler)

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        """Delegate to wrapped MemoryMiddleware (async).

        Args:
            request: The model request being processed.
            handler: The async handler function to call.

        Returns:
            The model response from the handler.
        """
        return await self._memory_middleware.awrap_model_call(request, handler)


__all__ = [
    "RefreshableMemoryMiddleware",
]
