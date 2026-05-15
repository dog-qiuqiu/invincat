"""Textual UI adapter for agent execution."""

from __future__ import annotations

import asyncio as asyncio
from typing import TYPE_CHECKING, Any

from invincat_cli.core.debug import configure_debug_logging
from invincat_cli.core.session_stats import ModelStats as ModelStats
from invincat_cli.core.session_stats import SessionStats as SessionStats
from invincat_cli.core.session_stats import SpinnerStatus as SpinnerStatus
from invincat_cli.core.session_stats import format_token_count as format_token_count
from invincat_cli.hooks import dispatch_hook as dispatch_hook
from invincat_cli.io.file_ops import FileOpTracker as FileOpTracker
from invincat_cli.io.input import parse_file_mentions as parse_file_mentions
from invincat_cli.io.media_utils import (
    create_multimodal_content as create_multimodal_content,
)
from invincat_cli.textual_adapter.ui_adapter import TextualUIAdapter as TextualUIAdapter
from invincat_cli.textual_adapter.utils import print_usage_table as print_usage_table
from invincat_cli.widgets.messages import AppMessage as AppMessage
from invincat_cli.widgets.messages import AssistantMessage as AssistantMessage
from invincat_cli.widgets.messages import DiffMessage as DiffMessage
from invincat_cli.widgets.messages import SummarizationMessage as SummarizationMessage
from invincat_cli.widgets.messages import ToolCallMessage as ToolCallMessage

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from pydantic import TypeAdapter

import logging

logger = logging.getLogger(__name__)
configure_debug_logging(logger)


def _is_internal_model_chunk(metadata: Any) -> bool:
    from invincat_cli.textual_adapter.utils import is_internal_model_chunk

    return is_internal_model_chunk(metadata)


def _is_summarization_chunk(metadata: Any) -> bool:
    from invincat_cli.textual_adapter.utils import is_summarization_chunk

    return is_summarization_chunk(metadata)


def _is_transient_stream_error(exc: BaseException) -> bool:
    from invincat_cli.textual_adapter.utils import is_transient_stream_error

    return is_transient_stream_error(exc)


def _normalize_tool_id(value: Any) -> str | None:
    from invincat_cli.textual_adapter.utils import normalize_tool_id

    return normalize_tool_id(value)


_hitl_adapter_cache: TypeAdapter | None = None


def _read_mentioned_file(path: Any, max_embed_bytes: int) -> str:
    from invincat_cli.textual_adapter.utils import read_mentioned_file

    return read_mentioned_file(path, max_embed_bytes)


def _get_hitl_request_adapter(hitl_request_type: type) -> TypeAdapter:
    global _hitl_adapter_cache  # noqa: PLW0603
    if _hitl_adapter_cache is None:
        from pydantic import TypeAdapter

        _hitl_adapter_cache = TypeAdapter(hitl_request_type)
    return _hitl_adapter_cache


def _get_ask_user_adapter() -> TypeAdapter:
    from invincat_cli.textual_adapter.validation import _get_ask_user_adapter as impl

    return impl()


def _get_approve_plan_adapter() -> TypeAdapter:
    from invincat_cli.textual_adapter.validation import (
        _get_approve_plan_adapter as impl,
    )

    return impl()


def _build_interrupted_ai_message(
    pending_text_by_namespace: dict[tuple, str],
    current_tool_messages: dict[str, Any],
):
    from invincat_cli.textual_adapter.validation import (
        _build_interrupted_ai_message as impl,
    )

    return impl(pending_text_by_namespace, current_tool_messages)


async def execute_task_textual(*args: Any, **kwargs: Any) -> SessionStats:
    from invincat_cli.textual_adapter.execution import execute_task_textual as impl

    return await impl(*args, **kwargs)


async def _handle_interrupt_cleanup(
    *,
    adapter: TextualUIAdapter,
    agent: Any,
    config: RunnableConfig,
    pending_text_by_namespace: dict[tuple, str],
    captured_input_tokens: int,
    captured_output_tokens: int,
    turn_stats: SessionStats,
    start_time: float,
) -> None:
    from invincat_cli.textual_adapter.reporting import _handle_interrupt_cleanup as impl

    await impl(
        adapter=adapter,
        agent=agent,
        config=config,
        pending_text_by_namespace=pending_text_by_namespace,
        captured_input_tokens=captured_input_tokens,
        captured_output_tokens=captured_output_tokens,
        turn_stats=turn_stats,
        start_time=start_time,
    )


async def _persist_context_tokens(
    agent: Any,
    config: RunnableConfig,
    tokens: int,
) -> None:
    from invincat_cli.textual_adapter.reporting import _persist_context_tokens as impl

    await impl(agent, config, tokens)


async def _report_and_persist_tokens(
    adapter: TextualUIAdapter,
    agent: Any,
    config: RunnableConfig,
    captured_input_tokens: int,
    captured_output_tokens: int,
    *,
    shield: bool = False,
    approximate: bool = False,
) -> None:
    from invincat_cli.textual_adapter.reporting import (
        _report_and_persist_tokens as impl,
    )

    await impl(
        adapter,
        agent,
        config,
        captured_input_tokens,
        captured_output_tokens,
        shield=shield,
        approximate=approximate,
    )


async def _flush_assistant_text_ns(
    adapter: TextualUIAdapter,
    text: str,
    ns_key: tuple,
    assistant_message_by_namespace: dict[tuple, Any],
) -> None:
    from invincat_cli.textual_adapter.reporting import _flush_assistant_text_ns as impl

    await impl(adapter, text, ns_key, assistant_message_by_namespace)
