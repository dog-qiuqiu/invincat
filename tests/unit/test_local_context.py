from __future__ import annotations

import asyncio
from types import SimpleNamespace

from invincat_cli import local_context


class ExecuteResult:
    def __init__(self, output: str | None, exit_code: int | None) -> None:
        self.output = output
        self.exit_code = exit_code


class SyncBackend:
    def __init__(self, result: ExecuteResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[str, int | None]] = []

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResult:
        self.calls.append((command, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class AsyncBackend:
    def __init__(self, result: ExecuteResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[str, int | None]] = []

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResult:
        self.calls.append((command, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeRequest:
    def __init__(self, state: dict[str, object], system_prompt: str | None) -> None:
        self.state = state
        self.system_prompt = system_prompt

    def override(self, **kwargs: object) -> FakeRequest:
        return FakeRequest(self.state, kwargs.get("system_prompt"))  # type: ignore[arg-type]


def test_build_mcp_context_formats_servers_and_limits_tool_names() -> None:
    many_tools = [SimpleNamespace(name=f"tool-{i}") for i in range(12)]
    servers = [
        SimpleNamespace(name="empty", transport="stdio", tools=[]),
        SimpleNamespace(name="full", transport="http", tools=many_tools),
        SimpleNamespace(
            name="short",
            transport="sse",
            tools=[SimpleNamespace(name="lookup"), SimpleNamespace(name="read")],
        ),
    ]

    text = local_context._build_mcp_context(servers)

    assert "3 servers, 14 tools" in text
    assert "**empty** (stdio): (no tools)" in text
    assert "tool-0" in text
    assert "and 2 more" in text
    assert "**short** (sse): lookup, read" in text
    assert local_context._build_mcp_context([]) == ""


def test_build_detect_script_contains_parallel_sections() -> None:
    script = local_context.build_detect_script()

    assert script.startswith("bash <<'__DETECT_CONTEXT_EOF__'")
    assert "## Local Context" in script
    assert "wait" in script
    assert '"$_DCT/02_pkgmgr"' in script


def test_handle_detect_result_requires_successful_output() -> None:
    assert (
        local_context.LocalContextMiddleware._handle_detect_result(
            ExecuteResult(" context \n", 0)
        )
        == "context"
    )
    assert (
        local_context.LocalContextMiddleware._handle_detect_result(ExecuteResult("", 0))
        is None
    )
    assert (
        local_context.LocalContextMiddleware._handle_detect_result(
            ExecuteResult("bad", 1)
        )
        is None
    )
    assert (
        local_context.LocalContextMiddleware._handle_detect_result(
            ExecuteResult("bad", None)
        )
        is None
    )


def test_before_agent_detects_initial_context_and_refreshes_after_summary() -> None:
    backend = SyncBackend(ExecuteResult("detected", 0))
    middleware = local_context.LocalContextMiddleware(backend)

    assert middleware.before_agent({}, runtime=None) == {"local_context": "detected"}  # type: ignore[arg-type]
    assert middleware.before_agent({"local_context": "existing"}, runtime=None) is None  # type: ignore[arg-type]
    assert middleware.before_agent(
        {
            "local_context": "old",
            "_summarization_event": {"cutoff_index": 4},
            "_local_context_refreshed_at_cutoff": 3,
        },
        runtime=None,  # type: ignore[arg-type]
    ) == {"local_context": "detected", "_local_context_refreshed_at_cutoff": 4}
    assert backend.calls
    assert backend.calls[-1][1] == local_context._DETECT_SCRIPT_TIMEOUT


def test_before_agent_records_cutoff_when_refresh_fails() -> None:
    backend = SyncBackend(ExecuteResult("", 0))
    middleware = local_context.LocalContextMiddleware(backend)

    assert middleware.before_agent({}, runtime=None) is None  # type: ignore[arg-type]

    assert middleware.before_agent(
        {
            "local_context": "old",
            "_summarization_event": {"cutoff_index": 5},
        },
        runtime=None,  # type: ignore[arg-type]
    ) == {"_local_context_refreshed_at_cutoff": 5}


def test_run_detect_script_handles_sync_backend_errors() -> None:
    assert (
        local_context.LocalContextMiddleware(
            SyncBackend(NotImplementedError())
        )._run_detect_script()
        is None
    )
    assert (
        local_context.LocalContextMiddleware(
            SyncBackend(RuntimeError("boom"))
        )._run_detect_script()
        is None
    )
    assert (
        local_context.LocalContextMiddleware(
            AsyncBackend(ExecuteResult("async", 0))
        )._run_detect_script()
        is None
    )


def test_abefore_agent_uses_async_backend_and_sync_fallback() -> None:
    async def run() -> None:
        async_backend = AsyncBackend(ExecuteResult("async context", 0))
        async_middleware = local_context.LocalContextMiddleware(async_backend)
        assert await async_middleware.abefore_agent({}, runtime=None) == {  # type: ignore[arg-type]
            "local_context": "async context"
        }
        assert (
            await async_middleware.abefore_agent(
                {"local_context": "existing"},
                runtime=None,  # type: ignore[arg-type]
            )
            is None
        )
        assert await async_middleware.abefore_agent(
            {
                "local_context": "old",
                "_summarization_event": {"cutoff_index": 2},
                "_local_context_refreshed_at_cutoff": 1,
            },
            runtime=None,  # type: ignore[arg-type]
        ) == {"local_context": "async context", "_local_context_refreshed_at_cutoff": 2}

        sync_middleware = local_context.LocalContextMiddleware(
            SyncBackend(ExecuteResult("sync context", 0))
        )
        assert await sync_middleware.abefore_agent({}, runtime=None) == {  # type: ignore[arg-type]
            "local_context": "sync context"
        }

        failing = local_context.LocalContextMiddleware(AsyncBackend(RuntimeError()))
        assert await failing.abefore_agent({}, runtime=None) is None  # type: ignore[arg-type]
        assert await failing.abefore_agent(
            {
                "local_context": "old",
                "_summarization_event": {"cutoff_index": 9},
            },
            runtime=None,  # type: ignore[arg-type]
        ) == {"_local_context_refreshed_at_cutoff": 9}

    asyncio.run(run())


def test_arun_detect_script_handles_sync_fallback_errors(monkeypatch) -> None:
    async def run() -> None:
        middleware = local_context.LocalContextMiddleware(object())  # type: ignore[arg-type]

        async def fail_to_thread(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("thread failed")

        monkeypatch.setattr(local_context.asyncio, "to_thread", fail_to_thread)

        assert await middleware._arun_detect_script() is None

    asyncio.run(run())


def test_wrap_model_call_appends_context_and_mcp_prompt() -> None:
    backend = SyncBackend(ExecuteResult("unused", 0))
    middleware = local_context.LocalContextMiddleware(
        backend,
        mcp_server_info=[SimpleNamespace(name="srv", transport="stdio", tools=[])],
    )
    request = FakeRequest({"local_context": "## Local"}, "base")
    seen: list[FakeRequest] = []

    def handler(value: FakeRequest) -> str:
        seen.append(value)
        return "response"

    assert middleware.wrap_model_call(request, handler) == "response"  # type: ignore[arg-type]
    assert seen[0].system_prompt == (
        "base\n\n## Local\n\n**MCP Servers** (1 servers, 0 tools):\n"
        "- **srv** (stdio): (no tools)"
    )

    empty_middleware = local_context.LocalContextMiddleware(backend)
    empty_request = FakeRequest({}, None)
    seen.clear()
    assert empty_middleware.wrap_model_call(empty_request, handler) == "response"  # type: ignore[arg-type]
    assert seen[0] is empty_request


def test_awrap_model_call_appends_context() -> None:
    async def run() -> None:
        middleware = local_context.LocalContextMiddleware(
            SyncBackend(ExecuteResult("unused", 0))
        )
        request = FakeRequest({"local_context": "ctx"}, None)
        seen: list[FakeRequest] = []

        async def handler(value: FakeRequest) -> str:
            seen.append(value)
            return "response"

        assert await middleware.awrap_model_call(request, handler) == "response"  # type: ignore[arg-type]
        assert seen[0].system_prompt == "\n\nctx"

    asyncio.run(run())
