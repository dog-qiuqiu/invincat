"""Agent middleware used by the CLI graph."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain_core.messages import ToolMessage
    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command

logger = logging.getLogger(__name__)


class ShellAllowListMiddleware(AgentMiddleware):
    """Validate shell commands against an allow-list without HITL interrupts.

    When the agent invokes a shell tool (any tool in `SHELL_TOOL_NAMES`),
    this middleware checks the command against the configured allow-list
    **before execution**. Rejected commands are returned as error `ToolMessage`
    objects — the graph never pauses, so LangSmith traces stay as a single
    continuous run.

    Use this middleware in non-interactive mode to avoid the
    interrupt/resume cycle that fragments traces.
    """

    def __init__(self, allow_list: list[str], *, cwd: str | Path | None = None) -> None:
        """Initialize with the shell allow-list to validate commands against.

        Args:
            allow_list: Allowed command names (e.g. `["ls", "cat", "grep"]`).
                Must be a non-empty restrictive list — not `SHELL_ALLOW_ALL`.

        Raises:
            ValueError: If `allow_list` is empty.
            TypeError: If `allow_list` is the `SHELL_ALLOW_ALL` sentinel.
        """
        from invincat_cli.config import SHELL_ALLOW_ALL

        super().__init__()
        if not allow_list:
            msg = "allow_list must not be empty; disable shell access instead"
            raise ValueError(msg)
        if isinstance(allow_list, type(SHELL_ALLOW_ALL)):
            msg = (
                "SHELL_ALLOW_ALL should not be used with "
                "ShellAllowListMiddleware; use auto_approve=True instead"
            )
            raise TypeError(msg)
        self._allow_list = list(allow_list)
        self._cwd = Path(cwd).expanduser().resolve() if cwd else None

    def _validate_tool_call(self, request: ToolCallRequest) -> ToolMessage | None:
        """Return an error tool message when a shell command is not allowed.

        Args:
            request: The tool call request being processed.

        Returns:
            An error `ToolMessage` when the shell command should be rejected,
            otherwise `None`.
        """
        from langchain_core.messages import ToolMessage as LCToolMessage

        from invincat_cli.config import SHELL_TOOL_NAMES, is_shell_command_allowed

        tool_name = request.tool_call["name"]
        if tool_name not in SHELL_TOOL_NAMES:
            return None

        args = request.tool_call.get("args") or {}
        command = args.get("command", "")
        if is_shell_command_allowed(command, self._allow_list, cwd=self._cwd):
            logger.debug("Shell command allowed: %r", command)
            return None

        logger.warning("Shell command rejected by allow-list: %r", command)
        allowed_str = ", ".join(self._allow_list)
        return LCToolMessage(
            content=(
                f"Shell command rejected: `{command}` is not in the allow-list. "
                f"Allowed commands: {allowed_str}. "
                f"Please use an allowed command or try another approach."
            ),
            name=tool_name,
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Reject disallowed shell commands; pass everything else through.

        Args:
            request: The tool call request being processed.
            handler: The next handler in the middleware chain.

        Returns:
            The tool execution result, or an error `ToolMessage` for rejected
            shell commands.
        """
        if (rejection := self._validate_tool_call(request)) is not None:
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Reject disallowed shell commands; pass everything else through.

        Args:
            request: The tool call request being processed.
            handler: The next handler in the middleware chain.

        Returns:
            The tool execution result, or an error `ToolMessage` for rejected
            shell commands.
        """
        if (rejection := self._validate_tool_call(request)) is not None:
            return rejection
        return await handler(request)


_MEMORY_FILE_NAMES: frozenset[str] = frozenset(
    {"memory_user.json", "memory_project.json"}
)
# Tools that accept a file path as their first argument.
_FILE_PATH_TOOLS: frozenset[str] = frozenset({"read_file", "write_file", "edit_file"})
# Shell tools whose command string might reference memory files.
_SHELL_TOOLS: frozenset[str] = frozenset({"bash", "execute", "shell"})


def _path_targets_memory_file(path_str: str) -> bool:
    """Return True when *path_str* resolves to a memory store file."""
    from pathlib import PurePosixPath

    name = PurePosixPath(path_str.strip()).name
    return name in _MEMORY_FILE_NAMES


class MemoryFileGuardMiddleware(AgentMiddleware):
    """Block the main agent from directly reading or writing memory store files.

    Memory is injected exclusively through ``RefreshableMemoryMiddleware`` and
    updated through ``MemoryAgentMiddleware``. Any attempt by the main agent to
    touch ``memory_user.json`` / ``memory_project.json`` via a file or shell
    tool is rejected with an explanatory error message.
    """

    def _check(self, request: ToolCallRequest) -> ToolMessage | None:
        from langchain_core.messages import ToolMessage as LCToolMessage

        tool_name: str = request.tool_call.get("name", "")
        args: dict = request.tool_call.get("args") or {}

        reject = False
        if tool_name in _FILE_PATH_TOOLS:
            path = args.get("path") or args.get("file_path") or ""
            reject = _path_targets_memory_file(str(path))
        elif tool_name in _SHELL_TOOLS:
            command = str(args.get("command", ""))
            reject = any(name in command for name in _MEMORY_FILE_NAMES)

        if not reject:
            return None

        logger.warning("MemoryFileGuard: blocked %r targeting memory store", tool_name)
        return LCToolMessage(
            content=(
                "Access denied: memory store files (memory_user.json / memory_project.json) "
                "are managed exclusively by the memory subsystem. "
                "Do not read or write them directly."
            ),
            name=tool_name,
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if (rejection := self._check(request)) is not None:
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        if (rejection := self._check(request)) is not None:
            return rejection
        return await handler(request)
