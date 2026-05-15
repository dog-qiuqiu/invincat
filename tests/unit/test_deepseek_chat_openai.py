"""Tests for DeepSeek OpenAI-compatible model behavior."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    HumanMessageChunk,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from invincat_cli.models import deepseek_chat_openai as deepseek
from invincat_cli.models.deepseek_chat_openai import DeepSeekChatOpenAICompat


def test_deepseek_payload_strips_reasoning_effort_when_thinking_disabled() -> None:
    with patch(
        "langchain_openai.ChatOpenAI._get_request_payload",
        return_value={
            "messages": [],
            "reasoning_effort": "medium",
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    ):
        model = DeepSeekChatOpenAICompat(model="deepseek-chat", api_key="test")

        payload = model._get_request_payload([])

    assert payload["extra_body"]["thinking"]["type"] == "disabled"
    assert "reasoning_effort" not in payload


def test_deepseek_payload_keeps_reasoning_effort_when_thinking_enabled() -> None:
    with patch(
        "langchain_openai.ChatOpenAI._get_request_payload",
        return_value={
            "messages": [],
            "reasoning_effort": "high",
            "extra_body": {"thinking": {"type": "enabled"}},
        },
    ):
        model = DeepSeekChatOpenAICompat(model="deepseek-chat", api_key="test")

        payload = model._get_request_payload([])

    assert payload["reasoning_effort"] == "high"


def test_deepseek_payload_replays_reasoning_content_for_ai_messages() -> None:
    input_messages = [
        HumanMessage(content="hello"),
        AIMessage(content="answer", additional_kwargs={"reasoning_content": "why"}),
        AIMessage(content="answer without reasoning"),
    ]
    payload_messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "answer"},
        {"role": "assistant", "content": "answer without reasoning"},
    ]

    with patch(
        "langchain_openai.ChatOpenAI._get_request_payload",
        return_value={"messages": payload_messages},
    ):
        model = DeepSeekChatOpenAICompat(model="deepseek-chat", api_key="test")

        payload = model._get_request_payload(input_messages)

    assert payload["messages"][1]["reasoning_content"] == "why"
    assert payload["messages"][2]["reasoning_content"] == ""


def test_private_helpers_handle_unexpected_shapes() -> None:
    assert deepseek._is_reasoning_required_error(
        RuntimeError("reasoning_content must be passed back")
    )
    assert not deepseek._is_reasoning_required_error(RuntimeError("other"))
    assert deepseek._extract_response_dict({"choices": []}) == {"choices": []}
    assert (
        deepseek._extract_response_dict(SimpleNamespace(model_dump=lambda: [])) is None
    )
    assert deepseek._extract_response_dict(object()) is None

    class BadDump:
        def model_dump(self) -> dict:
            raise ValueError("bad")

    assert deepseek._extract_response_dict(BadDump()) is None
    assert deepseek._iter_messages("not messages") is None
    assert deepseek._thinking_disabled({"extra_body": {"thinking": {}}}) is False


def test_convert_chunk_to_generation_chunk_preserves_reasoning_delta() -> None:
    returned = ChatGenerationChunk(message=AIMessageChunk(content="partial"))

    with patch(
        "langchain_openai.ChatOpenAI._convert_chunk_to_generation_chunk",
        return_value=returned,
    ):
        model = DeepSeekChatOpenAICompat(model="deepseek-chat", api_key="test")

        generation = model._convert_chunk_to_generation_chunk(
            {"choices": [{"delta": {"reasoning_content": "because"}}]},
            AIMessageChunk,
            None,
        )

    assert generation is returned
    assert generation.message.additional_kwargs["reasoning_content"] == "because"


def test_convert_chunk_to_generation_chunk_ignores_missing_or_empty_reasoning() -> None:
    returned = ChatGenerationChunk(message=AIMessageChunk(content="partial"))
    non_ai = ChatGenerationChunk(message=HumanMessageChunk(content="partial"))

    with patch(
        "langchain_openai.ChatOpenAI._convert_chunk_to_generation_chunk",
        side_effect=[None, non_ai, returned, returned],
    ):
        model = DeepSeekChatOpenAICompat(model="deepseek-chat", api_key="test")

        assert (
            model._convert_chunk_to_generation_chunk({}, AIMessageChunk, None) is None
        )
        assert (
            model._convert_chunk_to_generation_chunk(
                {"choices": [{"delta": {"reasoning_content": "ignored"}}]},
                AIMessageChunk,
                None,
            )
            is non_ai
        )
        no_choices = model._convert_chunk_to_generation_chunk(
            {"choices": []},
            AIMessageChunk,
            None,
        )
        empty_reasoning = model._convert_chunk_to_generation_chunk(
            {"choices": [{"delta": {"reasoning_content": ""}}]},
            AIMessageChunk,
            None,
        )

    assert no_choices is returned
    assert empty_reasoning is returned
    assert "reasoning_content" not in returned.message.additional_kwargs


def test_convert_chunk_to_generation_chunk_handles_nested_choices_and_bad_delta() -> (
    None
):
    nested = ChatGenerationChunk(message=AIMessageChunk(content="nested"))
    bad_delta = ChatGenerationChunk(message=AIMessageChunk(content="bad"))

    with patch(
        "langchain_openai.ChatOpenAI._convert_chunk_to_generation_chunk",
        side_effect=[nested, bad_delta],
    ):
        model = DeepSeekChatOpenAICompat(model="deepseek-chat", api_key="test")

        assert (
            model._convert_chunk_to_generation_chunk(
                {"chunk": {"choices": [{"delta": {"reasoning_content": "nested"}}]}},
                AIMessageChunk,
                None,
            )
            is nested
        )
        assert (
            model._convert_chunk_to_generation_chunk(
                {"choices": [{"delta": []}]},
                AIMessageChunk,
                None,
            )
            is bad_delta
        )

    assert nested.message.additional_kwargs["reasoning_content"] == "nested"
    assert "reasoning_content" not in bad_delta.message.additional_kwargs


def test_create_chat_result_preserves_reasoning_content_from_dict_and_model_dump() -> (
    None
):
    result = ChatResult(
        generations=[
            ChatGeneration(message=AIMessage(content="answer")),
            ChatGeneration(message=AIMessage(content="second")),
        ]
    )

    with patch(
        "langchain_openai.ChatOpenAI._create_chat_result",
        return_value=result,
    ):
        model = DeepSeekChatOpenAICompat(model="deepseek-chat", api_key="test")

        converted = model._create_chat_result(
            {
                "choices": [
                    {"message": {"reasoning_content": "first reasoning"}},
                    {"message": {"reasoning_content": ""}},
                    {"message": {"reasoning_content": "out of range"}},
                ]
            }
        )

    assert converted is result
    assert (
        result.generations[0].message.additional_kwargs["reasoning_content"]
        == "first reasoning"
    )
    assert "reasoning_content" not in result.generations[1].message.additional_kwargs

    dump_result = ChatResult(
        generations=[ChatGeneration(message=AIMessage(content="a"))]
    )
    response = SimpleNamespace(
        model_dump=lambda: {
            "choices": [{"message": {"reasoning_content": "dump reasoning"}}]
        }
    )
    with patch(
        "langchain_openai.ChatOpenAI._create_chat_result",
        return_value=dump_result,
    ):
        assert model._create_chat_result(response) is dump_result
    assert (
        dump_result.generations[0].message.additional_kwargs["reasoning_content"]
        == "dump reasoning"
    )


def test_create_chat_result_ignores_unexpected_response_shapes() -> None:
    result = ChatResult(
        generations=[ChatGeneration(message=AIMessage(content="answer"))]
    )

    with patch(
        "langchain_openai.ChatOpenAI._create_chat_result",
        return_value=result,
    ):
        model = DeepSeekChatOpenAICompat(model="deepseek-chat", api_key="test")

        assert model._create_chat_result(object()) is result
        assert model._create_chat_result({"choices": {}}) is result
        assert (
            model._create_chat_result({"choices": ["bad", {"message": "bad"}]})
            is result
        )

    assert "reasoning_content" not in result.generations[0].message.additional_kwargs


def test_streaming_and_generation_success_paths_yield_super_results() -> None:
    chunk = ChatGenerationChunk(message=AIMessageChunk(content="ok"))
    result = ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])

    def stream(*_args: object, **_kwargs: object):
        yield chunk

    async def astream(*_args: object, **_kwargs: object):
        yield chunk

    async def agenerate(*_args: object, **_kwargs: object) -> ChatResult:
        return result

    model = DeepSeekChatOpenAICompat(model="deepseek-chat", api_key="test")

    with patch("langchain_openai.ChatOpenAI._stream", stream):
        assert list(model._stream([])) == [chunk]

    async def run_async_paths() -> None:
        with patch("langchain_openai.ChatOpenAI._astream", astream):
            assert [item async for item in model._astream([])] == [chunk]
        with patch("langchain_openai.ChatOpenAI._agenerate", agenerate):
            assert await model._agenerate([]) is result

    asyncio.run(run_async_paths())


def test_streaming_and_generation_errors_are_propagated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def raise_stream(*_args: object, **_kwargs: object):
        raise RuntimeError("reasoning_content must be passed back")
        yield

    async def raise_astream(*_args: object, **_kwargs: object):
        raise RuntimeError("reasoning_content must be passed back")
        yield

    async def raise_agenerate(*_args: object, **_kwargs: object):
        raise RuntimeError("reasoning_content must be passed back")

    model = DeepSeekChatOpenAICompat(model="deepseek-chat", api_key="test")

    with patch("langchain_openai.ChatOpenAI._stream", raise_stream):
        with pytest.raises(RuntimeError, match="reasoning_content"):
            list(model._stream([]))

    async def run_async_paths() -> None:
        with patch("langchain_openai.ChatOpenAI._astream", raise_astream):
            with pytest.raises(RuntimeError, match="reasoning_content"):
                async for _chunk in model._astream([]):
                    pass
        with patch("langchain_openai.ChatOpenAI._agenerate", raise_agenerate):
            with pytest.raises(RuntimeError, match="reasoning_content"):
                await model._agenerate([])

    asyncio.run(run_async_paths())

    assert "propagating error" in caplog.text
