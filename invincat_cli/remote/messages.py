"""Message conversion helpers for remote LangGraph streams."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def convert_ai_message(data: dict[str, Any]) -> Any:  # noqa: ANN401
    """Convert a server AI message dict to an `AIMessageChunk`."""
    from langchain_core.messages import AIMessageChunk

    content = data.get("content", "")
    tool_call_chunks = data.get("tool_call_chunks", [])
    tool_calls = data.get("tool_calls", [])
    additional_kwargs = data.get("additional_kwargs", {})
    usage_metadata = data.get("usage_metadata")
    response_metadata = data.get("response_metadata", {})

    kwargs: dict[str, Any] = {
        "content": content,
        "id": data.get("id"),
        "response_metadata": response_metadata,
    }
    if isinstance(additional_kwargs, dict) and additional_kwargs:
        kwargs["additional_kwargs"] = dict(additional_kwargs)
    reasoning_content = data.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content:
        ak = kwargs.setdefault("additional_kwargs", {})
        if isinstance(ak, dict):
            ak.setdefault("reasoning_content", reasoning_content)

    if tool_call_chunks:
        kwargs["tool_call_chunks"] = [
            {
                "name": tc.get("name"),
                "args": tc.get("args", ""),
                "id": tc.get("id"),
                "index": tc.get("index", i),
            }
            for i, tc in enumerate(tool_call_chunks)
        ]
    elif tool_calls:
        has_str_args = any(isinstance(tc.get("args"), str) for tc in tool_calls)
        if has_str_args:
            kwargs["tool_call_chunks"] = [
                {
                    "name": tc.get("name"),
                    "args": tc.get("args", ""),
                    "id": tc.get("id"),
                    "index": i,
                }
                for i, tc in enumerate(tool_calls)
            ]
        else:
            kwargs["tool_calls"] = tool_calls

    try:
        chunk = AIMessageChunk(**kwargs)
    except (TypeError, ValueError, KeyError):
        logger.warning(
            "Failed to construct AIMessageChunk from server data (id=%s)",
            data.get("id"),
            exc_info=True,
        )
        return None

    if usage_metadata:
        chunk.usage_metadata = usage_metadata
    return chunk


def convert_human_message(data: dict[str, Any]) -> Any:  # noqa: ANN401
    """Convert a server human message dict to a `HumanMessage`."""
    from langchain_core.messages import HumanMessage

    try:
        return HumanMessage(
            content=data.get("content", ""),
            id=data.get("id"),
        )
    except (TypeError, ValueError, KeyError):
        logger.warning(
            "Failed to construct HumanMessage from server data (id=%s)",
            data.get("id"),
            exc_info=True,
        )
        return None


def convert_tool_message(data: dict[str, Any]) -> Any:  # noqa: ANN401
    """Convert a server tool message dict to a `ToolMessage`."""
    from langchain_core.messages import ToolMessage

    try:
        return ToolMessage(
            content=data.get("content", ""),
            tool_call_id=data.get("tool_call_id", ""),
            name=data.get("name", ""),
            id=data.get("id"),
            status=data.get("status", "success"),
        )
    except (TypeError, ValueError, KeyError):
        logger.warning(
            "Failed to construct ToolMessage from server data (id=%s)",
            data.get("id"),
            exc_info=True,
        )
        return None


MESSAGE_CONVERTERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "ai": convert_ai_message,
    "AIMessage": convert_ai_message,
    "AIMessageChunk": convert_ai_message,
    "human": convert_human_message,
    "HumanMessage": convert_human_message,
    "tool": convert_tool_message,
    "ToolMessage": convert_tool_message,
}


def convert_message_data(data: dict[str, Any]) -> Any:  # noqa: ANN401
    """Convert a server message dict into a LangChain message object."""
    msg_type = data.get("type", "")
    converter = MESSAGE_CONVERTERS.get(msg_type)
    if converter is not None:
        return converter(data)
    logger.warning("Unknown message type in stream: %s", msg_type)
    return None
