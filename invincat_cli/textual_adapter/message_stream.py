"""Message-stream handling for Textual streamed execution."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage

from invincat_cli import textual_adapter as adapter_mod
from invincat_cli.core.session_stats import SessionStats
from invincat_cli.i18n import t
from invincat_cli.middleware.plan_agent import PLANNER_ALLOWED_TOOLS
from invincat_cli.textual_adapter.utils import (
    is_internal_model_chunk as _is_internal_model_chunk,
)
from invincat_cli.textual_adapter.utils import (
    is_summarization_chunk as _is_summarization_chunk,
)

logger = logging.getLogger(__name__)
_PLANNER_ALLOWED_TOOL_SET: frozenset[str] = frozenset(PLANNER_ALLOWED_TOOLS)


async def handle_message_stream_chunk(
    *,
    adapter: Any,
    data: Any,
    ns_key: tuple,
    is_main_agent: bool,
    file_op_tracker: Any,
    processed_wecom_file_tool_ids: set[str],
    pending_text_by_namespace: dict[tuple, str],
    assistant_message_by_namespace: dict[tuple, Any],
    displayed_tool_ids: set[str],
    tool_call_buffers: dict[str | int, dict],
    planner_mode_enforced: bool,
    turn_stats: SessionStats,
    captured_input_tokens: int,
    summarization_in_progress: bool,
    memory_update_in_progress: bool,
    on_text_delta: Callable[[str, str], Awaitable[None]] | None,
    on_wecom_file_request: Callable[[dict[str, Any]], Awaitable[None]] | None,
    on_schedule_payload: Callable[[dict[str, Any]], Awaitable[None]] | None,
) -> tuple[int, bool, bool]:
    # Skip subagent outputs - only render main agent content in chat
    if not is_main_agent:
        logger.debug("Skipping subagent message ns=%s", ns_key)
        return captured_input_tokens, summarization_in_progress, memory_update_in_progress

    if not isinstance(data, tuple) or len(data) != 2:  # noqa: PLR2004  # message stream data is a 2-tuple (message, metadata)
        logger.debug(
            "Skipping non-2-tuple message data: type=%s",
            type(data).__name__,
        )
        return captured_input_tokens, summarization_in_progress, memory_update_in_progress

    message, metadata = data
    has_content_blocks = hasattr(message, "content_blocks")
    content_blocks = (
        getattr(message, "content_blocks", None)
        if has_content_blocks
        else None
    )
    if not (has_content_blocks and content_blocks == []):
        logger.debug(
            "Processing message: type=%s id=%s has_content_blocks=%s",
            type(message).__name__,
            getattr(message, "id", None),
            has_content_blocks,
        )

    # Filter out summarization model output, but keep UI feedback.
    # The summarization model streams AIMessage chunks tagged
    # with lc_source="summarization" in the callback metadata.
    # These are hidden from the user; only the spinner and a
    # notification widget provide feedback.
    if _is_summarization_chunk(metadata):
        if not summarization_in_progress:
            summarization_in_progress = True
            if adapter._set_spinner:
                await adapter._set_spinner(
                    t("status.offloading")
                )
        return captured_input_tokens, summarization_in_progress, memory_update_in_progress
    if _is_internal_model_chunk(metadata):
        if not memory_update_in_progress:
            memory_update_in_progress = True
            if adapter._set_spinner:
                await adapter._set_spinner(
                    t("status.memory_updating")
                )
        return captured_input_tokens, summarization_in_progress, memory_update_in_progress

    # Regular (non-summarization) chunks resumed — summarization
    # has finished. Mount the notification and reset the spinner.
    if summarization_in_progress:
        summarization_in_progress = False
        try:
            await adapter._mount_message(adapter_mod.SummarizationMessage())
        except Exception:
            logger.debug(
                "Failed to mount summarization notification",
                exc_info=True,
            )
        if (
            adapter._set_spinner
            and not adapter._current_tool_messages
        ):
            await adapter._set_spinner(t("status.thinking"))

    if memory_update_in_progress:
        memory_update_in_progress = False
        if (
            adapter._set_spinner
            and not adapter._current_tool_messages
        ):
            await adapter._set_spinner(t("status.thinking"))

    if isinstance(message, HumanMessage):
        content = message.text
        # Flush pending text for this namespace
        pending_text = pending_text_by_namespace.get(ns_key, "")
        if content and pending_text:
            await adapter_mod._flush_assistant_text_ns(
                adapter,
                pending_text,
                ns_key,
                assistant_message_by_namespace,
            )
            pending_text_by_namespace[ns_key] = ""
        return captured_input_tokens, summarization_in_progress, memory_update_in_progress

    if isinstance(message, ToolMessage):
        from invincat_cli.textual_adapter.tool_results import (
            handle_tool_message,
        )

        await handle_tool_message(
            adapter=adapter,
            message=message,
            file_op_tracker=file_op_tracker,
            processed_wecom_file_tool_ids=processed_wecom_file_tool_ids,
            on_wecom_file_request=on_wecom_file_request,
            on_schedule_payload=on_schedule_payload,
            pending_text_by_namespace=pending_text_by_namespace,
            ns_key=ns_key,
            assistant_message_by_namespace=assistant_message_by_namespace,
        )
        return captured_input_tokens, summarization_in_progress, memory_update_in_progress

    # Extract token usage (before content_blocks check
    # - usage may be on any chunk)
    if hasattr(message, "usage_metadata"):
        usage = message.usage_metadata
        if usage:
            input_toks = usage.get("input_tokens", 0)
            output_toks = usage.get("output_tokens", 0)
            total_toks = usage.get("total_tokens", 0)
            from invincat_cli.config import settings

            active_model = settings.model_name or ""
            if input_toks or output_toks:
                # Model gives split counts — preferred path
                turn_stats.record_request(
                    active_model, input_toks, output_toks
                )
                captured_input_tokens = max(
                    captured_input_tokens,
                    input_toks + output_toks,
                )
            elif total_toks:
                # Fallback: model gives only total (no split)
                turn_stats.record_request(
                    active_model, total_toks, 0
                )
                captured_input_tokens = max(
                    captured_input_tokens, total_toks
                )

            # Immediately update UI with current token count
            if adapter._on_tokens_update:
                adapter._on_tokens_update(captured_input_tokens)

    # Check if this is an AIMessageChunk with content
    if not has_content_blocks:
        logger.debug(
            "Message has no content_blocks: type=%s",
            type(message).__name__,
        )
        return captured_input_tokens, summarization_in_progress, memory_update_in_progress

    # Process content blocks
    blocks = content_blocks or []
    if blocks:
        logger.debug(
            "content_blocks count=%d blocks=%s",
            len(blocks),
            repr(blocks)[:500],
        )
    for block in blocks:
        block_type = block.get("type")

        if block_type == "text":
            text = block.get("text", "")
            if text:
                # Track accumulated text for reference
                pending_text = pending_text_by_namespace.get(
                    ns_key, ""
                )
                pending_text += text
                pending_text_by_namespace[ns_key] = pending_text
                if on_text_delta is not None:
                    try:
                        await on_text_delta(text, pending_text)
                    except Exception:
                        logger.warning(
                            "External text-delta callback failed",
                            exc_info=True,
                        )

                # Get or create assistant message for this namespace
                current_msg = (
                    assistant_message_by_namespace.get(ns_key)
                )
                if current_msg is None:
                    # Hide spinner when assistant starts responding
                    if adapter._set_spinner:
                        await adapter._set_spinner(None)
                    msg_id = f"asst-{uuid.uuid4().hex[:8]}"
                    # Mark active BEFORE mounting so pruning
                    # (triggered by mount) won't remove it
                    # (_mount_message can trigger
                    # _prune_old_messages if the window exceeds
                    # WINDOW_SIZE.)
                    if adapter._set_active_message:
                        adapter._set_active_message(msg_id)
                    current_msg = adapter_mod.AssistantMessage(id=msg_id)
                    await adapter._mount_message(current_msg)
                    assistant_message_by_namespace[ns_key] = (
                        current_msg
                    )

                # Append just the new text chunk for smoother
                # streaming (uses MarkdownStream internally for
                # better performance)
                await current_msg.append_content(text)
                # Keep the store's content field in sync so that
                # consumers polling message_store (e.g. wecombot)
                # can read partial content while streaming is live.
                if adapter._message_store and current_msg.id:
                    adapter._message_store.update_message(
                        current_msg.id,
                        content=current_msg._content,
                    )

        elif block_type in {"reasoning", "non_standard"}:
            pass  # reasoning content is intentionally not displayed

        elif block_type in {"tool_call_chunk", "tool_call"}:
            from invincat_cli.textual_adapter.tool_calls import (
                handle_tool_call_block,
            )

            await handle_tool_call_block(
                adapter=adapter,
                block=block,
                displayed_tool_ids=displayed_tool_ids,
                tool_call_buffers=tool_call_buffers,
                planner_mode_enforced=planner_mode_enforced,
                planner_allowed_tool_set=_PLANNER_ALLOWED_TOOL_SET,
                file_op_tracker=file_op_tracker,
                pending_text_by_namespace=pending_text_by_namespace,
                ns_key=ns_key,
                assistant_message_by_namespace=assistant_message_by_namespace,
            )
    if getattr(message, "chunk_position", None) == "last":
        pending_text = pending_text_by_namespace.get(ns_key, "")
        if pending_text:
            await adapter_mod._flush_assistant_text_ns(
                adapter,
                pending_text,
                ns_key,
                assistant_message_by_namespace,
            )
            pending_text_by_namespace[ns_key] = ""
            assistant_message_by_namespace.pop(ns_key, None)
    return captured_input_tokens, summarization_in_progress, memory_update_in_progress
