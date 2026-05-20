"""Runtime tool boundaries for built-in subagents."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from langgraph.prebuilt.tool_node import ToolCallRequest


READ_ONLY_SUBAGENT_ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "file_info",
        "ls",
        "glob",
        "grep",
        "web_search",
        "fetch_url",
        "ask_user",
    }
)
"""Tool names allowed for read-only built-in subagents."""


_DOCUMENT_WORKER_MUTATING_FILE_TOOLS: frozenset[str] = frozenset(
    {"write_file", "edit_file", "delete_file", "mkdir", "move_file", "copy_file"}
)
_DOCUMENT_WORKER_SOURCE_DIR_PARTS: frozenset[str] = frozenset(
    {
        ".github",
        "src",
        "test",
        "tests",
        "invincat_cli",
    }
)
_DOCUMENT_WORKER_SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".bash",
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".css",
        ".cxx",
        ".fish",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".php",
        ".ps1",
        ".py",
        ".pyi",
        ".rb",
        ".rs",
        ".sh",
        ".sql",
        ".swift",
        ".ts",
        ".tsx",
        ".zsh",
    }
)
_DOCUMENT_WORKER_CONFIG_FILENAMES: frozenset[str] = frozenset(
    {
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "package-lock.json",
        "package.json",
        "pnpm-lock.yaml",
        "pyproject.toml",
        "readme.md",
        "requirements-dev.txt",
        "requirements.txt",
        "uv.lock",
        "yarn.lock",
    }
)
"""Project source/config paths document workers should not modify."""


def _normal_path(path: str) -> PurePosixPath:
    return PurePosixPath(path.strip().replace("\\", "/"))


def document_worker_path_block_reason(path: str) -> str | None:
    """Return why document-worker must not mutate *path*, if applicable."""
    if not path or not path.strip():
        return None

    parsed = _normal_path(path)
    parts = {part.lower() for part in parsed.parts if part not in {".", ""}}
    if parts & _DOCUMENT_WORKER_SOURCE_DIR_PARTS:
        return "source or project-control directory"

    name = parsed.name.lower()
    if name in _DOCUMENT_WORKER_CONFIG_FILENAMES:
        return "project configuration or primary README"

    suffix = parsed.suffix.lower()
    if suffix in _DOCUMENT_WORKER_SOURCE_EXTENSIONS:
        return "source-code file"
    return None


def _document_worker_target_paths(tool_name: str, args: dict[str, Any]) -> list[str]:
    if tool_name in {"write_file", "edit_file", "delete_file", "mkdir"}:
        path = args.get("path") or args.get("file_path")
        return [str(path)] if path else []
    if tool_name in {"move_file", "copy_file"}:
        paths: list[str] = []
        for key in ("source", "destination"):
            path = args.get(key)
            if path:
                paths.append(str(path))
        return paths
    return []


class DocumentWorkerFileGuardMiddleware(AgentMiddleware):
    """Runtime guard for document-worker file mutations.

    Document workers may generate document outputs, but source-code and project
    configuration mutations belong to the main agent or worker subagent.
    """

    def _reject_if_blocked(self, request: ToolCallRequest) -> ToolMessage | None:
        tool_name = str(request.tool_call.get("name", "")).strip()
        if tool_name not in _DOCUMENT_WORKER_MUTATING_FILE_TOOLS:
            return None

        args = request.tool_call.get("args") or {}
        for path in _document_worker_target_paths(tool_name, args):
            reason = document_worker_path_block_reason(path)
            if reason is None:
                continue
            return ToolMessage(
                content=(
                    f"Document-worker file operation rejected for `{path}`: {reason} "
                    "changes belong to the main agent or worker subagent. Return "
                    "document findings or write to an explicit document output path."
                ),
                name=tool_name,
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage],
    ) -> ToolMessage:
        if (rejection := self._reject_if_blocked(request)) is not None:
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> ToolMessage:
        if (rejection := self._reject_if_blocked(request)) is not None:
            return rejection
        return await handler(request)


_WORKER_BLOCKED_SHELL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bgit\s+(?:commit|push|tag)\b"), "git commit/push/tag"),
    (re.compile(r"\bgh\s+release\b"), "GitHub release"),
    (re.compile(r"\bnpm\s+publish\b"), "npm publish"),
    (re.compile(r"\bpnpm\s+publish\b"), "pnpm publish"),
    (re.compile(r"\byarn\s+(?:npm\s+)?publish\b"), "yarn publish"),
    (re.compile(r"\btwine\s+upload\b"), "twine upload"),
    (
        re.compile(r"\bpython(?:\d+(?:\.\d+)?)?\s+-m\s+twine\s+upload\b"),
        "twine upload",
    ),
    (re.compile(r"\buv\s+publish\b"), "uv publish"),
    (re.compile(r"\bpoetry\s+publish\b"), "poetry publish"),
    (re.compile(r"\bcargo\s+publish\b"), "cargo publish"),
    (re.compile(r"\bvercel(?:\s+deploy)?(?:\s+[^;&|]*)?\s+--prod\b"), "vercel deploy"),
    (re.compile(r"\b(?:fly|firebase|wrangler)\s+deploy\b"), "deploy"),
    (re.compile(r"\bkubectl\s+(?:apply|delete|rollout|scale|set)\b"), "kubectl deploy"),
    (re.compile(r"\bhelm\s+(?:install|upgrade|uninstall)\b"), "helm release"),
)
"""Shell command patterns blocked for implementation worker subagents."""


def worker_shell_block_reason(command: str) -> str | None:
    """Return a short reason when *command* is unsafe for worker subagents."""
    normalized = " ".join(command.strip().split())
    for pattern, reason in _WORKER_BLOCKED_SHELL_PATTERNS:
        if pattern.search(normalized):
            return reason
    return None


class WorkerShellGuardMiddleware(AgentMiddleware):
    """Runtime guard for implementation worker shell commands.

    The worker can edit files and run focused verification, but repository
    ownership operations such as commits, pushes, releases, publishing, and
    deployments must remain under explicit main-agent/user control.
    """

    def _reject_if_blocked(self, request: ToolCallRequest) -> ToolMessage | None:
        from invincat_cli.config import SHELL_TOOL_NAMES

        tool_name = str(request.tool_call.get("name", "")).strip()
        if tool_name not in SHELL_TOOL_NAMES:
            return None

        args = request.tool_call.get("args") or {}
        command = str(args.get("command", ""))
        reason = worker_shell_block_reason(command)
        if reason is None:
            return None

        return ToolMessage(
            content=(
                f"Worker shell command rejected: {reason} is reserved for the "
                "main agent or explicit user-controlled release flow. Report the "
                "needed action instead of running it from a worker subagent."
            ),
            name=tool_name,
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage],
    ) -> ToolMessage:
        if (rejection := self._reject_if_blocked(request)) is not None:
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> ToolMessage:
        if (rejection := self._reject_if_blocked(request)) is not None:
            return rejection
        return await handler(request)


class ReadOnlySubagentToolMiddleware(AgentMiddleware):
    """Runtime guard for read-only subagents.

    Prompt instructions are useful but insufficient for hard safety boundaries.
    This middleware also hides disallowed tool schemas and rejects accidental
    calls before they execute.
    """

    def __init__(self, allowed_tools: set[str] | frozenset[str] | None = None) -> None:
        super().__init__()
        self._allowed_tools = set(allowed_tools or READ_ONLY_SUBAGENT_ALLOWED_TOOLS)

    @staticmethod
    def _tool_name(tool: Any) -> str:  # noqa: ANN401
        if hasattr(tool, "name"):
            return str(getattr(tool, "name", "")).strip()
        if isinstance(tool, dict):
            return str(tool.get("name", "")).strip()
        return ""

    def _filter_tools(self, tools: list[Any]) -> list[Any]:  # noqa: ANN401
        return [tool for tool in tools if self._tool_name(tool) in self._allowed_tools]

    def _reject_if_disallowed(self, request: ToolCallRequest) -> ToolMessage | None:
        tool_name = str(request.tool_call.get("name", "")).strip()
        if tool_name in self._allowed_tools:
            return None
        allowed = ", ".join(sorted(self._allowed_tools))
        return ToolMessage(
            content=(
                f"Tool '{tool_name}' is not allowed for this read-only subagent. "
                f"Allowed tools: {allowed}."
            ),
            name=tool_name,
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage],
    ) -> ToolMessage:
        if (rejection := self._reject_if_disallowed(request)) is not None:
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> ToolMessage:
        if (rejection := self._reject_if_disallowed(request)) is not None:
            return rejection
        return await handler(request)

    def wrap_model_call(
        self,
        request: Any,  # noqa: ANN401
        handler: Callable[[Any], Any],  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        filtered_tools = self._filter_tools(list(getattr(request, "tools", [])))
        return handler(request.override(tools=filtered_tools))

    async def awrap_model_call(
        self,
        request: Any,  # noqa: ANN401
        handler: Callable[[Any], Any],  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        filtered_tools = self._filter_tools(list(getattr(request, "tools", [])))
        return await handler(request.override(tools=filtered_tools))
