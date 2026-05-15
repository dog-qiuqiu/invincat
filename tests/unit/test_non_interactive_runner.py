"""Tests for non-interactive runner orchestration and exit paths."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from rich.console import Console

import invincat_cli.config as config_mod
import invincat_cli.non_interactive as ni
from invincat_cli.model_config import ModelConfigError
from invincat_cli.non_interactive.state import ThreadUrlLookupState


def test_build_non_interactive_header_includes_default_model_and_thread_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ni.settings, "model_name", "gpt-test")
    monkeypatch.setattr(
        ni,
        "build_langsmith_thread_url",
        lambda thread_id: f"https://example.test/{thread_id}",
    )

    header = ni._build_non_interactive_header(
        ni.DEFAULT_AGENT_NAME,
        "thread-1",
        include_thread_link=True,
    )

    assert "CLI: v" in header.plain
    assert "Agent: agent (default)" in header.plain
    assert "Model: gpt-test" in header.plain
    assert "Thread: thread-1" in header.plain


def test_build_non_interactive_header_avoids_thread_lookup_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fail_lookup(_thread_id: str) -> str:
        nonlocal called
        called = True
        raise AssertionError("should not resolve links by default")

    monkeypatch.setattr(ni, "build_langsmith_thread_url", fail_lookup)

    header = ni._build_non_interactive_header("custom", "thread-2")

    assert "Agent: custom" in header.plain
    assert "Thread: thread-2" in header.plain
    assert not called


def test_run_agent_loop_writes_buffered_response_and_completion_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hook_events: list[tuple[str, dict[str, object]]] = []
    writes: list[str] = []
    usage_calls: list[float] = []

    async def fake_dispatch(name: str, payload: dict[str, object]) -> None:
        hook_events.append((name, payload))

    async def fake_stream_agent(
        _agent: object,
        _stream_input: object,
        _config: object,
        state: ni.StreamState,
        _console: Console,
        _file_op_tracker: object,
    ) -> None:
        state.full_response.append("final")

    monkeypatch.setattr(ni, "dispatch_hook", fake_dispatch)
    monkeypatch.setattr(ni, "_stream_agent", fake_stream_agent)
    monkeypatch.setattr(ni, "_write_text", writes.append)
    monkeypatch.setattr(ni, "_write_newline", lambda: writes.append("\n"))
    monkeypatch.setattr(
        ni,
        "print_usage_table",
        lambda _stats, wall_time, _console: usage_calls.append(wall_time),
    )

    lookup = ThreadUrlLookupState(url="https://example.test/thread-1")
    lookup.done.set()
    console = Console(record=True)

    asyncio.run(
        ni._run_agent_loop(
            object(),
            "task",
            {"configurable": {"thread_id": "thread-1"}},
            console,
            object(),
            stream=False,
            thread_url_lookup=lookup,
        )
    )

    assert writes == ["final", "\n"]
    assert hook_events == [
        ("session.start", {"thread_id": "thread-1"}),
        ("task.complete", {"thread_id": "thread-1"}),
        ("session.end", {"thread_id": "thread-1"}),
    ]
    assert usage_calls and usage_calls[0] >= 0
    exported = console.export_text()
    assert "View in LangSmith" in exported
    assert "Task completed" in exported


def test_run_agent_loop_caps_repeated_hitl_interrupts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_dispatch(_name: str, _payload: dict[str, object]) -> None:
        return None

    async def fake_stream_agent(
        _agent: object,
        _stream_input: object,
        _config: object,
        state: ni.StreamState,
        _console: Console,
        _file_op_tracker: object,
    ) -> None:
        state.interrupt_occurred = True

    monkeypatch.setattr(ni, "dispatch_hook", fake_dispatch)
    monkeypatch.setattr(ni, "_stream_agent", fake_stream_agent)
    monkeypatch.setattr(ni, "_process_hitl_interrupts", lambda *_args: None)
    monkeypatch.setattr(ni, "_MAX_HITL_ITERATIONS", 1)

    with pytest.raises(ni.HITLIterationLimitError, match="Exceeded 1"):
        asyncio.run(
            ni._run_agent_loop(
                object(),
                "task",
                {"configurable": {"thread_id": "thread-1"}},
                Console(record=True),
                object(),
                quiet=True,
            )
        )


def test_run_non_interactive_returns_model_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ni,
        "create_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ModelConfigError("bad model")),
    )

    assert asyncio.run(ni.run_non_interactive("task", quiet=True)) == 1


def test_run_non_interactive_success_configures_server_and_agent_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied: list[str] = []
    loop_calls: list[dict[str, object]] = []
    session_kwargs: list[dict[str, object]] = []

    class ModelResult:
        def apply_to_settings(self) -> None:
            applied.append("settings")

    @asynccontextmanager
    async def fake_server_session(**kwargs: object):
        session_kwargs.append(kwargs)
        yield object(), object()

    async def fake_run_agent_loop(
        agent: object,
        message: str,
        config: object,
        console: Console,
        file_op_tracker: object,
        **kwargs: object,
    ) -> None:
        loop_calls.append(
            {
                "agent": agent,
                "message": message,
                "config": config,
                "quiet": kwargs["quiet"],
                "stream": kwargs["stream"],
            }
        )

    monkeypatch.setattr(ni, "create_model", lambda *_args, **_kwargs: ModelResult())
    monkeypatch.setattr(ni, "generate_thread_id", lambda: "generated-thread")
    monkeypatch.setattr(
        config_mod,
        "build_stream_config",
        lambda thread_id, assistant_id, **kwargs: {
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            **kwargs,
        },
    )
    import invincat_cli.server.manager as server_manager

    monkeypatch.setattr(server_manager, "server_session", fake_server_session)
    monkeypatch.setattr(ni, "_run_agent_loop", fake_run_agent_loop)
    monkeypatch.setattr(ni.settings, "shell_allow_list", ["git"])

    code = asyncio.run(
        ni.run_non_interactive(
            "task",
            assistant_id="agent-x",
            model_name="model-x",
            sandbox_type="none",
            quiet=True,
            stream=False,
            no_mcp=True,
        )
    )

    assert code == 0
    assert applied == ["settings"]
    assert loop_calls == [
        {
            "agent": loop_calls[0]["agent"],
            "message": "task",
            "config": {
                "thread_id": "generated-thread",
                "assistant_id": "agent-x",
                "sandbox_type": "none",
            },
            "quiet": True,
            "stream": False,
        }
    ]
    assert session_kwargs[0]["auto_approve"] is False
    assert session_kwargs[0]["interrupt_shell_only"] is True
    assert session_kwargs[0]["shell_allow_list"] == ["git"]
    assert session_kwargs[0]["enable_ask_user"] is False
    assert session_kwargs[0]["interactive"] is False


@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (KeyboardInterrupt(), 130),
        (ni.HITLIterationLimitError("too many"), 1),
        (ValueError("bad value"), 1),
        (RuntimeError("boom"), 1),
    ],
)
def test_run_non_interactive_error_exit_paths(
    monkeypatch: pytest.MonkeyPatch,
    exc: BaseException,
    expected_code: int,
) -> None:
    class ModelResult:
        def apply_to_settings(self) -> None:
            return None

    @asynccontextmanager
    async def fake_server_session(**_kwargs: object):
        yield object(), object()

    async def fail_loop(*_args: object, **_kwargs: object) -> None:
        raise exc

    monkeypatch.setattr(ni, "create_model", lambda *_args, **_kwargs: ModelResult())
    monkeypatch.setattr(ni, "generate_thread_id", lambda: "thread-1")
    monkeypatch.setattr(
        config_mod,
        "build_stream_config",
        lambda thread_id, assistant_id, **kwargs: {},
    )
    import invincat_cli.server.manager as server_manager

    monkeypatch.setattr(server_manager, "server_session", fake_server_session)
    monkeypatch.setattr(ni, "_run_agent_loop", fail_loop)
    monkeypatch.setattr(ni.settings, "shell_allow_list", None)

    assert asyncio.run(ni.run_non_interactive("task", quiet=True, no_mcp=True)) == (
        expected_code
    )


def test_run_non_interactive_unrestricted_shell_auto_approves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ModelResult:
        def apply_to_settings(self) -> None:
            return None

    session_kwargs: list[dict[str, object]] = []

    @asynccontextmanager
    async def fake_server_session(**kwargs: object):
        session_kwargs.append(kwargs)
        yield object(), object()

    async def fake_run_agent_loop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(ni, "create_model", lambda *_args, **_kwargs: ModelResult())
    monkeypatch.setattr(ni, "generate_thread_id", lambda: "thread-1")
    monkeypatch.setattr(
        config_mod,
        "build_stream_config",
        lambda thread_id, assistant_id, **kwargs: {},
    )
    import invincat_cli.server.manager as server_manager

    monkeypatch.setattr(server_manager, "server_session", fake_server_session)
    monkeypatch.setattr(ni, "_run_agent_loop", fake_run_agent_loop)
    monkeypatch.setattr(ni.settings, "shell_allow_list", ni.SHELL_ALLOW_ALL)

    assert asyncio.run(ni.run_non_interactive("task", quiet=True, no_mcp=True)) == 0
    assert session_kwargs[0]["auto_approve"] is True
    assert session_kwargs[0]["interrupt_shell_only"] is False
    assert session_kwargs[0]["shell_allow_list"] is None
