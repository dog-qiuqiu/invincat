"""Tests for remote agent client adaptation helpers."""

from __future__ import annotations

import asyncio
import logging

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
from langgraph.types import Interrupt

from invincat_cli.remote_client import (
    RemoteAgent,
    _convert_interrupts,
    _convert_message_data,
    _prepare_config,
    _require_thread_id,
)


def _config() -> dict:
    return {"configurable": {"thread_id": "thread-1"}, "metadata": {"a": 1}}


def test_require_thread_id_validates_config() -> None:
    assert _require_thread_id(_config()) == "thread-1"

    with pytest.raises(ValueError, match="thread_id"):
        _require_thread_id({"configurable": {}})


def test_prepare_config_shallow_copies_configurable() -> None:
    raw = _config()

    prepared = _prepare_config(raw)
    prepared["configurable"]["thread_id"] = "changed"

    assert raw["configurable"]["thread_id"] == "thread-1"


def test_remote_agent_get_graph_lazily_constructs_remote_graph(monkeypatch) -> None:
    import langgraph.pregel.remote as remote_module

    created: list[tuple] = []

    class FakeRemoteGraph:
        def __init__(self, *args, **kwargs) -> None:
            created.append((args, kwargs))

    monkeypatch.setattr(remote_module, "RemoteGraph", FakeRemoteGraph)
    agent = RemoteAgent(
        "http://example.test",
        graph_name="custom",
        api_key="key",
        headers={"x-test": "1"},
    )

    graph = agent._get_graph()

    assert graph is agent._get_graph()
    assert created == [
        (
            ("custom",),
            {
                "url": "http://example.test",
                "api_key": "key",
                "headers": {"x-test": "1"},
            },
        )
    ]


def test_convert_interrupts_handles_dicts_objects_and_invalid_shapes() -> None:
    existing = Interrupt(value="already", id="i0")
    converted = _convert_interrupts(
        [{"value": "pause", "id": "i1"}, existing, {"other": "value"}]
    )

    assert converted[0].value == "pause"
    assert converted[0].id == "i1"
    assert converted[1] is existing
    assert converted[2] == {"other": "value"}
    assert _convert_interrupts(None) == []
    assert _convert_interrupts("not-list") == ["not-list"]


def test_convert_message_data_supports_known_message_types() -> None:
    ai = _convert_message_data(
        {
            "type": "ai",
            "content": "hello",
            "reasoning_content": "because",
            "tool_calls": [{"name": "tool", "args": '{"x": 1}', "id": "tc1"}],
            "usage_metadata": {"input_tokens": 1, "output_tokens": 2},
        }
    )
    human = _convert_message_data({"type": "human", "content": "hi", "id": "h1"})
    tool = _convert_message_data(
        {
            "type": "tool",
            "content": "done",
            "tool_call_id": "tc1",
            "name": "tool",
        }
    )

    assert isinstance(ai, AIMessageChunk)
    assert ai.additional_kwargs["reasoning_content"] == "because"
    assert ai.usage_metadata == {"input_tokens": 1, "output_tokens": 2}
    assert isinstance(human, HumanMessage)
    assert isinstance(tool, ToolMessage)
    assert _convert_message_data({"type": "unknown"}) is None


def test_convert_message_data_supports_aliases_and_tool_call_variants() -> None:
    chunked = _convert_message_data(
        {
            "type": "AIMessageChunk",
            "content": "",
            "additional_kwargs": {"provider": "test"},
            "tool_call_chunks": [{"name": "tool", "args": "{", "id": "tc1"}],
        }
    )
    parsed = _convert_message_data(
        {
            "type": "AIMessage",
            "content": "",
            "tool_calls": [{"name": "tool", "args": {"x": 1}, "id": "tc1"}],
        }
    )
    human = _convert_message_data({"type": "HumanMessage", "content": "hi", "id": "h1"})
    tool = _convert_message_data(
        {
            "type": "ToolMessage",
            "content": "done",
            "tool_call_id": "tc1",
            "status": "error",
        }
    )

    assert isinstance(chunked, AIMessageChunk)
    assert chunked.additional_kwargs["provider"] == "test"
    assert chunked.tool_call_chunks[0]["index"] == 0
    assert isinstance(parsed, AIMessageChunk)
    assert parsed.tool_calls[0]["args"] == {"x": 1}
    assert isinstance(human, HumanMessage)
    assert isinstance(tool, ToolMessage)
    assert tool.status == "error"


def test_convert_message_data_returns_none_when_message_construction_fails(
    monkeypatch,
) -> None:
    import langchain_core.messages as messages

    class FailingMessage:
        def __init__(self, **_kwargs) -> None:
            raise TypeError("bad message")

    monkeypatch.setattr(messages, "AIMessageChunk", FailingMessage)
    assert _convert_message_data({"type": "ai", "id": "ai1"}) is None

    monkeypatch.setattr(messages, "HumanMessage", FailingMessage)
    assert _convert_message_data({"type": "human", "id": "h1"}) is None

    monkeypatch.setattr(messages, "ToolMessage", FailingMessage)
    assert _convert_message_data({"type": "tool", "id": "t1"}) is None


def test_remote_agent_stream_converts_messages_and_updates() -> None:
    class Graph:
        async def astream(self, *_args, **_kwargs):
            yield (), "messages", ({"type": "human", "content": "hi"}, {"m": 1})
            yield (), "updates", {"__interrupt__": [{"value": "pause", "id": "i1"}]}
            yield ("child",), "custom", {"value": 1}

    agent = RemoteAgent("http://example.test")
    agent._graph = Graph()

    async def _collect() -> list:
        return [
            event
            async for event in agent.astream(
                {"messages": []},
                config=_config(),
            )
        ]

    events = asyncio.run(_collect())

    assert isinstance(events[0][2][0], HumanMessage)
    assert events[0][2][1] == {"m": 1}
    assert events[1][2]["__interrupt__"][0].value == "pause"
    assert events[2] == (("child",), "custom", {"value": 1})


def test_remote_agent_stream_handles_dropped_preconverted_and_unexpected_messages(
    caplog,
) -> None:
    message = HumanMessage(content="ready")

    class Graph:
        async def astream(self, *_args, **_kwargs):
            yield (), "messages", ({"type": "unknown"}, None)
            yield (), "messages", (message, None)
            yield (), "messages", (123, None)
            yield (), "updates", {"value": 1}

    agent = RemoteAgent("http://example.test")
    agent._graph = Graph()

    async def _collect() -> list:
        with caplog.at_level(logging.WARNING):
            return [
                event
                async for event in agent.astream({"messages": []}, config=_config())
            ]

    events = asyncio.run(_collect())

    assert events == [
        ((), "messages", (message, {})),
        ((), "updates", {"value": 1}),
    ]
    assert "Unexpected message data type" in caplog.text
    assert "Dropped 1 message(s)" in caplog.text


def test_remote_agent_get_state_returns_none_for_missing_thread(monkeypatch) -> None:
    import langgraph_sdk.errors as errors

    class MissingThreadError(Exception):
        pass

    monkeypatch.setattr(errors, "NotFoundError", MissingThreadError)

    class Graph:
        async def aget_state(self, _config):
            raise MissingThreadError("missing")

    agent = RemoteAgent("http://example.test")
    agent._graph = Graph()

    assert asyncio.run(agent.aget_state(_config())) is None


def test_remote_agent_state_methods_log_and_reraise_errors(caplog) -> None:
    class Graph:
        async def aget_state(self, _config):
            raise RuntimeError("get failed")

        async def aupdate_state(self, _config, _values):
            raise RuntimeError("update failed")

    agent = RemoteAgent("http://example.test")
    agent._graph = Graph()

    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError, match="get"):
        asyncio.run(agent.aget_state(_config()))
    assert "Failed to get state for thread thread-1" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError, match="update"):
        asyncio.run(agent.aupdate_state(_config(), {"x": 1}))
    assert "Failed to update state for thread thread-1" in caplog.text


def test_remote_agent_update_and_ensure_thread_delegate_to_graph() -> None:
    calls: list[tuple] = []

    class Threads:
        async def create(self, **kwargs):
            calls.append(("create", kwargs))

    class Client:
        threads = Threads()

    class Graph:
        async def aupdate_state(self, config, values):
            calls.append(("update", config, values))

        def _validate_client(self):
            return Client()

    agent = RemoteAgent("http://example.test", graph_name="graph")
    agent._graph = Graph()

    asyncio.run(agent.aupdate_state(_config(), {"x": 1}))
    asyncio.run(agent.aensure_thread(_config()))

    assert calls[0] == ("update", _config(), {"x": 1})
    assert calls[1] == (
        "create",
        {
            "thread_id": "thread-1",
            "if_exists": "do_nothing",
            "metadata": {"a": 1},
            "graph_id": "graph",
        },
    )


def test_remote_agent_ensure_thread_uses_none_for_non_dict_metadata() -> None:
    calls: list[dict] = []

    class Threads:
        async def create(self, **kwargs):
            calls.append(kwargs)

    class Client:
        threads = Threads()

    class Graph:
        def _validate_client(self):
            return Client()

    agent = RemoteAgent("http://example.test")
    agent._graph = Graph()

    asyncio.run(
        agent.aensure_thread(
            {"configurable": {"thread_id": "thread-1"}, "metadata": "bad"}
        )
    )

    assert calls[0]["metadata"] is None


def test_remote_agent_ensure_thread_logs_and_reraises_errors(caplog) -> None:
    class Graph:
        def _validate_client(self):
            raise RuntimeError("client failed")

    agent = RemoteAgent("http://example.test")
    agent._graph = Graph()

    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError, match="client"):
        asyncio.run(agent.aensure_thread(_config()))

    assert "Failed to ensure thread thread-1 exists on remote server" in caplog.text


def test_remote_agent_with_config_returns_self() -> None:
    agent = RemoteAgent("http://example.test")

    assert agent.with_config({"configurable": {}}) is agent
