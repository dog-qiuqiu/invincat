"""Tests for built-in subagent runtime tool boundaries."""

from __future__ import annotations

import asyncio

from langchain_core.messages import ToolMessage

from invincat_cli.agent.subagents.tool_boundary import (
    READ_ONLY_SUBAGENT_ALLOWED_TOOLS,
    DocumentWorkerFileGuardMiddleware,
    ReadOnlySubagentToolMiddleware,
    WorkerShellGuardMiddleware,
    document_worker_path_block_reason,
    worker_shell_block_reason,
)


def _request(name: str, args: dict[str, object] | None = None):
    return type(
        "Req",
        (),
        {"tool_call": {"name": name, "id": f"tc-{name}", "args": args or {}}},
    )()


def test_read_only_subagent_allowed_tools_contract() -> None:
    assert set(READ_ONLY_SUBAGENT_ALLOWED_TOOLS) == {
        "read_file",
        "file_info",
        "ls",
        "glob",
        "grep",
        "web_search",
        "fetch_url",
        "ask_user",
    }


def test_read_only_subagent_rejects_mutating_tool() -> None:
    middleware = ReadOnlySubagentToolMiddleware()
    rejection = middleware._reject_if_disallowed(_request("write_file"))

    assert rejection is not None
    assert rejection.status == "error"
    assert "read-only subagent" in str(rejection.content)


def test_read_only_subagent_allows_read_tool() -> None:
    middleware = ReadOnlySubagentToolMiddleware()

    assert middleware._reject_if_disallowed(_request("read_file")) is None


def test_read_only_subagent_wrap_tool_call_sync_and_async() -> None:
    middleware = ReadOnlySubagentToolMiddleware({"read_file"})

    def handler(_request):  # noqa: ANN001
        return ToolMessage("ok", tool_call_id="tc-read_file", name="read_file")

    async def async_handler(_request):  # noqa: ANN001
        return ToolMessage("ok", tool_call_id="tc-read_file", name="read_file")

    assert middleware.wrap_tool_call(_request("read_file"), handler).content == "ok"
    assert middleware.wrap_tool_call(_request("edit_file"), handler).status == "error"
    assert (
        asyncio.run(middleware.awrap_tool_call(_request("read_file"), async_handler))
        .content
        == "ok"
    )
    assert (
        asyncio.run(middleware.awrap_tool_call(_request("edit_file"), async_handler))
        .status
        == "error"
    )


def test_read_only_subagent_filters_visible_tools_sync_and_async() -> None:
    middleware = ReadOnlySubagentToolMiddleware({"read_file", "grep"})

    class Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    class Req:
        def __init__(self) -> None:
            self.tools = [
                {"name": "read_file"},
                {"name": "write_file"},
                Tool("grep"),
                Tool("execute"),
            ]

        def override(self, **kwargs):  # noqa: ANN003
            nxt = Req()
            nxt.tools = kwargs.get("tools", self.tools)
            return nxt

    captured: list[str] = []

    def handler(req):  # noqa: ANN001
        captured.extend(middleware._tool_name(tool) for tool in req.tools)
        return req

    async def async_handler(req):  # noqa: ANN001
        captured.extend(middleware._tool_name(tool) for tool in req.tools)
        return req

    middleware.wrap_model_call(Req(), handler)
    assert captured == ["read_file", "grep"]
    captured.clear()

    asyncio.run(middleware.awrap_model_call(Req(), async_handler))
    assert captured == ["read_file", "grep"]


def test_worker_shell_block_reason_identifies_release_commands() -> None:
    assert worker_shell_block_reason("git status") is None
    assert worker_shell_block_reason("pytest -q") is None
    assert worker_shell_block_reason("git commit -m fix") == "git commit/push/tag"
    assert worker_shell_block_reason("git status && git push") == "git commit/push/tag"
    assert worker_shell_block_reason("python -m twine upload dist/*") == "twine upload"
    assert worker_shell_block_reason("npm publish") == "npm publish"
    assert worker_shell_block_reason("vercel --prod") == "vercel deploy"


def test_worker_shell_guard_rejects_release_commands_sync_and_async() -> None:
    middleware = WorkerShellGuardMiddleware()

    def handler(_request):  # noqa: ANN001
        return ToolMessage("ok", tool_call_id="tc-shell", name="shell")

    async def async_handler(_request):  # noqa: ANN001
        return ToolMessage("ok", tool_call_id="tc-shell", name="shell")

    allowed = middleware.wrap_tool_call(
        _request("shell", {"command": "pytest -q"}), handler
    )
    assert allowed.content == "ok"

    rejected = middleware.wrap_tool_call(
        _request("shell", {"command": "git commit -m fix"}), handler
    )
    assert rejected.status == "error"
    assert "reserved for the main agent" in str(rejected.content)

    async_rejected = asyncio.run(
        middleware.awrap_tool_call(
            _request("execute", {"command": "uv publish"}),
            async_handler,
        )
    )
    assert async_rejected.status == "error"


def test_document_worker_path_block_reason_identifies_source_and_config() -> None:
    assert document_worker_path_block_reason("reports/summary.md") is None
    assert document_worker_path_block_reason("exports/table.csv") is None
    assert document_worker_path_block_reason("src/app.py") == (
        "source or project-control directory"
    )
    assert document_worker_path_block_reason("tests/test_app.py") == (
        "source or project-control directory"
    )
    assert document_worker_path_block_reason(".github/workflows/ci.yml") == (
        "source or project-control directory"
    )
    assert document_worker_path_block_reason("README.md") == (
        "project configuration or primary README"
    )
    assert document_worker_path_block_reason("pyproject.toml") == (
        "project configuration or primary README"
    )
    assert document_worker_path_block_reason("scripts/build.sh") == "source-code file"


def test_document_worker_file_guard_rejects_source_mutations_sync_and_async() -> None:
    middleware = DocumentWorkerFileGuardMiddleware()

    def handler(_request):  # noqa: ANN001
        return ToolMessage("ok", tool_call_id="tc-write_file", name="write_file")

    async def async_handler(_request):  # noqa: ANN001
        return ToolMessage("ok", tool_call_id="tc-write_file", name="write_file")

    allowed = middleware.wrap_tool_call(
        _request("write_file", {"path": "reports/summary.md"}), handler
    )
    assert allowed.content == "ok"

    rejected = middleware.wrap_tool_call(
        _request("write_file", {"path": "src/app.py"}), handler
    )
    assert rejected.status == "error"
    assert "Document-worker file operation rejected" in str(rejected.content)

    move_rejected = asyncio.run(
        middleware.awrap_tool_call(
            _request(
                "move_file",
                {"source": "notes/input.md", "destination": "README.md"},
            ),
            async_handler,
        )
    )
    assert move_rejected.status == "error"
