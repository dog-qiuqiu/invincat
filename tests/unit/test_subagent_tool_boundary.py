"""Tests for built-in subagent runtime tool boundaries."""

from __future__ import annotations

import asyncio

from langchain_core.messages import ToolMessage

from invincat_cli.agent.subagents.tool_boundary import (
    READ_ONLY_SUBAGENT_ALLOWED_TOOLS,
    ReadOnlySubagentToolMiddleware,
)


def _request(name: str):
    return type(
        "Req",
        (),
        {"tool_call": {"name": name, "id": f"tc-{name}", "args": {}}},
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
