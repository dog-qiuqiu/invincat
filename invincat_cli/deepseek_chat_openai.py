"""DeepSeek compatibility wrapper for ChatOpenAI.

This wrapper keeps the default OpenAI-compatible path unchanged for all other
providers/models, while patching DeepSeek-specific thinking-mode behavior:

1) Preserve `reasoning_content` from responses.
2) Replay `reasoning_content` in subsequent assistant messages.
3) Propagate `reasoning_content` contract errors directly (no implicit
   thinking-mode downgrade).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any, cast

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

_REASONING_REQUIRED_SNIPPET = "reasoning_content"
_REASONING_REQUIRED_SNIPPET_2 = "must be passed back"


def _is_reasoning_required_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        _REASONING_REQUIRED_SNIPPET in text
        and _REASONING_REQUIRED_SNIPPET_2 in text
    )


def _extract_response_dict(response: Any) -> dict[str, Any] | None:
    if isinstance(response, dict):
        return response
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            return None
        return dumped if isinstance(dumped, dict) else None
    return None


def _iter_messages(input_: LanguageModelInput) -> list[BaseMessage] | None:
    if isinstance(input_, Sequence) and all(
        isinstance(m, BaseMessage) for m in input_
    ):
        return cast("list[BaseMessage]", list(input_))
    return None


class DeepSeekChatOpenAICompat(ChatOpenAI):
    """Compatibility wrapper for DeepSeek through OpenAI-compatible APIs."""

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        messages = _iter_messages(input_)
        payload_messages = payload.get("messages")
        if not messages or not isinstance(payload_messages, list):
            return payload

        for src, dst in zip(messages, payload_messages, strict=False):
            if not (isinstance(src, AIMessage) and isinstance(dst, dict)):
                continue
            reasoning_content = src.additional_kwargs.get("reasoning_content")
            if isinstance(reasoning_content, str) and reasoning_content:
                # DeepSeek expects assistant.reasoning_content in follow-up rounds
                # for thinking-mode tool-call loops.
                dst["reasoning_content"] = reasoning_content

        return payload

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is None:
            return None
        message = generation_chunk.message
        if not isinstance(message, AIMessageChunk):
            return generation_chunk

        choices = chunk.get("choices", []) or chunk.get("chunk", {}).get("choices", [])
        if not choices:
            return generation_chunk
        delta = choices[0].get("delta")
        if not isinstance(delta, dict):
            return generation_chunk
        reasoning_delta = delta.get("reasoning_content")
        if isinstance(reasoning_delta, str) and reasoning_delta:
            message.additional_kwargs["reasoning_content"] = reasoning_delta
        return generation_chunk

    def _create_chat_result(
        self,
        response: Any,
        generation_info: dict | None = None,
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info=generation_info)
        response_dict = _extract_response_dict(response)
        if response_dict is None:
            return result

        choices = response_dict.get("choices")
        if not isinstance(choices, list):
            return result
        for idx, choice in enumerate(choices):
            if not isinstance(choice, dict):
                continue
            msg = choice.get("message")
            if not isinstance(msg, dict):
                continue
            reasoning_content = msg.get("reasoning_content")
            if not (isinstance(reasoning_content, str) and reasoning_content):
                continue
            if idx >= len(result.generations):
                continue
            result.generations[idx].message.additional_kwargs["reasoning_content"] = (
                reasoning_content
            )
        return result

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        *,
        stream_usage: bool | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        try:
            async for chunk in super()._astream(
                messages,
                stop=stop,
                run_manager=run_manager,
                stream_usage=stream_usage,
                **kwargs,
            ):
                yield chunk
            return
        except Exception as exc:
            if _is_reasoning_required_error(exc):
                logger.error(
                    "DeepSeek requires reasoning_content replay in thinking mode; "
                    "propagating error without automatic think-disable fallback."
                )
            raise

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        try:
            for chunk in super()._stream(
                messages,
                stop=stop,
                run_manager=run_manager,
                **kwargs,
            ):
                yield chunk
            return
        except Exception as exc:
            if _is_reasoning_required_error(exc):
                logger.error(
                    "DeepSeek requires reasoning_content replay in thinking mode; "
                    "propagating error without automatic think-disable fallback."
                )
            raise

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            return await super()._agenerate(
                messages,
                stop=stop,
                run_manager=run_manager,
                **kwargs,
            )
        except Exception as exc:
            if _is_reasoning_required_error(exc):
                logger.error(
                    "DeepSeek requires reasoning_content replay in thinking mode; "
                    "propagating error without automatic think-disable fallback."
                )
            raise
