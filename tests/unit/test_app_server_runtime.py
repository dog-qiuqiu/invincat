"""Tests for server startup runtime helpers."""

from __future__ import annotations

from types import SimpleNamespace

from invincat_cli.app_runtime.server import (
    count_mcp_tools,
    format_similar_threads,
    normalize_server_start_error,
    resolve_mcp_preload_result,
    resolve_most_recent_agent_filter,
    resolve_no_recent_threads_notice,
    resolve_thread_not_found_notice,
    should_drain_deferred_on_server_ready,
    should_drain_queue_on_server_ready,
    should_update_default_agent_from_thread,
)


def test_normalize_server_start_error() -> None:
    exc = ValueError("bad")

    assert normalize_server_start_error(("agent", "proc", None)) is None
    assert normalize_server_start_error(exc) is exc

    base_exc = KeyboardInterrupt("stop")
    normalized = normalize_server_start_error(base_exc)
    assert isinstance(normalized, RuntimeError)
    assert "stop" in str(normalized)


def test_resolve_mcp_preload_result() -> None:
    assert resolve_mcp_preload_result(["server"]).info is None

    ok = resolve_mcp_preload_result(["server", ["mcp"]])
    assert ok.info == ["mcp"]
    assert ok.error is None

    exc = RuntimeError("mcp failed")
    failed = resolve_mcp_preload_result(["server", exc])
    assert failed.info is None
    assert failed.error is exc


def test_count_mcp_tools() -> None:
    assert (
        count_mcp_tools(
            [
                SimpleNamespace(tools=[object(), object()]),
                SimpleNamespace(tools=[object()]),
            ]
        )
        == 3
    )
    assert count_mcp_tools(None) == 0


def test_server_ready_drain_decisions() -> None:
    assert (
        should_drain_deferred_on_server_ready(
            deferred_action_count=1,
            agent_running=False,
        )
        is True
    )
    assert (
        should_drain_deferred_on_server_ready(
            deferred_action_count=1,
            agent_running=True,
        )
        is False
    )

    assert (
        should_drain_queue_on_server_ready(
            pending_message_count=1,
            initial_prompt=None,
        )
        is True
    )
    assert (
        should_drain_queue_on_server_ready(
            pending_message_count=1,
            initial_prompt="hello",
        )
        is False
    )


def test_resume_agent_helpers() -> None:
    assert should_update_default_agent_from_thread(assistant_id="agent") is True
    assert should_update_default_agent_from_thread(assistant_id="custom") is False
    assert resolve_most_recent_agent_filter(assistant_id="agent") is None
    assert resolve_most_recent_agent_filter(assistant_id="custom") == "custom"
    assert format_similar_threads(["a", 2, "c"]) == "a, 2, c"


def test_resume_notices() -> None:
    assert resolve_no_recent_threads_notice(None).key == "app.no_threads"
    agent_notice = resolve_no_recent_threads_notice("custom")
    assert agent_notice.key == "app.no_threads_agent"
    assert agent_notice.params == {"agent": "custom"}

    simple = resolve_thread_not_found_notice(thread_id="abc", similar=[])
    assert simple.key == "app.thread_not_found_simple"
    assert simple.params == {"thread_id": "abc"}

    with_similar = resolve_thread_not_found_notice(
        thread_id="abc",
        similar=["abd", "abe"],
    )
    assert with_similar.key == "app.thread_not_found"
    assert with_similar.params == {"thread_id": "abc", "similar": "abd, abe"}
