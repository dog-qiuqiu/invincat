"""Thread-history conversion helpers for the Textual app."""

from __future__ import annotations

import logging
from typing import Any

from invincat_cli.app_runtime.state import ThreadHistoryPayload
from invincat_cli.core.session_stats import format_token_count
from invincat_cli.widgets.message_store import MessageData, MessageType, ToolStatus

logger = logging.getLogger(__name__)


def merge_thread_state_with_fallback(
    values: dict[str, Any],
    fallback_values: dict[str, Any],
) -> dict[str, Any]:
    """Merge direct checkpointer values into an empty remote thread state."""
    merged = dict(values)
    existing_messages = merged.get("messages")
    fallback_messages = fallback_values.get("messages")
    if (
        not (isinstance(existing_messages, list) and existing_messages)
        and isinstance(fallback_messages, list)
        and fallback_messages
    ):
        merged["messages"] = fallback_messages
    for key in ("_summarization_event", "_context_tokens"):
        if merged.get(key) is None and key in fallback_values:
            merged[key] = fallback_values[key]
    return merged


def thread_history_payload_from_state_values(
    state_values: dict[str, Any],
) -> ThreadHistoryPayload:
    """Build a thread-history payload from raw checkpoint channel values."""
    raw_tokens = state_values.get("_context_tokens")
    context_tokens = (
        raw_tokens if isinstance(raw_tokens, int) and raw_tokens >= 0 else 0
    )
    messages = state_values.get("messages", [])

    if not messages:
        return ThreadHistoryPayload([], context_tokens)

    if messages and isinstance(messages[0], dict):
        from langchain_core.messages.utils import convert_to_messages

        messages = convert_to_messages(messages)

    return ThreadHistoryPayload(convert_messages_to_data(messages), context_tokens)


def convert_messages_to_data(messages: list[Any]) -> list[MessageData]:
    """Convert LangChain messages into lightweight message-store data."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    result: list[MessageData] = []
    pending_tool_indices: dict[str, int] = {}

    for msg in messages:
        if isinstance(msg, HumanMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content.startswith("[SYSTEM]"):
                continue

            skill_meta = (msg.additional_kwargs or {}).get("__skill")
            if isinstance(skill_meta, dict) and skill_meta.get("name"):
                result.append(
                    MessageData(
                        type=MessageType.SKILL,
                        content="",
                        skill_name=skill_meta["name"],
                        skill_description=str(skill_meta.get("description", "")),
                        skill_source=str(skill_meta.get("source", "")),
                        skill_args=str(skill_meta.get("args", "")),
                        skill_body=content,
                    )
                )
            else:
                result.append(MessageData(type=MessageType.USER, content=content))

        elif isinstance(msg, AIMessage):
            text = _extract_ai_text(msg.content)
            if text:
                result.append(MessageData(type=MessageType.ASSISTANT, content=text))

            for tool_call in getattr(msg, "tool_calls", []):
                tool_call_id = tool_call.get("id")
                data = MessageData(
                    type=MessageType.TOOL,
                    content="",
                    tool_name=tool_call.get("name", "unknown"),
                    tool_args=tool_call.get("args", {}),
                    tool_status=ToolStatus.PENDING,
                )
                result.append(data)
                if tool_call_id is not None:
                    pending_tool_indices[str(tool_call_id)] = len(result) - 1
                else:
                    data.tool_status = ToolStatus.REJECTED

        elif isinstance(msg, ToolMessage):
            raw_tool_call_id = getattr(msg, "tool_call_id", None)
            tool_call_id = str(raw_tool_call_id) if raw_tool_call_id is not None else None
            if tool_call_id and tool_call_id in pending_tool_indices:
                idx = pending_tool_indices.pop(tool_call_id)
                data = result[idx]
                status = getattr(msg, "status", "success")
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                data.tool_status = (
                    ToolStatus.SUCCESS if status == "success" else ToolStatus.ERROR
                )
                data.tool_output = content
            else:
                logger.debug(
                    "ToolMessage with tool_call_id=%r could not be matched to a "
                    "pending tool call",
                    tool_call_id,
                )

        else:
            logger.debug(
                "Skipping unsupported message type %s during history conversion",
                type(msg).__name__,
            )

    for idx in pending_tool_indices.values():
        result[idx].tool_status = ToolStatus.REJECTED

    return result


def build_resume_summary(messages: list[MessageData], context_tokens: int) -> str:
    """Build a one-line session summary shown when a thread is resumed."""
    user_messages = [m for m in messages if m.type == MessageType.USER]
    if not user_messages:
        return ""

    parts: list[str] = []

    first_content = user_messages[0].content.strip()
    if first_content:
        parts.append(f"Started with: “{_preview(first_content)}”")

    if len(user_messages) > 1:
        last_content = user_messages[-1].content.strip()
        if last_content and last_content != first_content:
            parts.append(f"Last topic: “{_preview(last_content)}”")

    total = len(messages)
    token_str = (
        f", {format_token_count(context_tokens)} tokens" if context_tokens > 0 else ""
    )
    parts.insert(0, f"{total} messages{token_str}")

    return " · ".join(parts)


def is_in_flight_tool_widget(
    widget: object,
    active_tool_widgets: set[object],
) -> bool:
    """Return whether a widget is still tracked as an active tool call."""
    return widget in active_tool_widgets


def tool_tracking_keys_for_widget(
    tracking: dict[str, object],
    widget: object,
) -> list[str]:
    """Return all tool tracking keys that currently point to a widget."""
    return [key for key, tracked_widget in tracking.items() if tracked_widget is widget]


def should_mark_missing_widget_pruned(*, is_streaming: bool) -> bool:
    """Return whether missing DOM widget data can be safely marked pruned."""
    return not is_streaming


def _extract_ai_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    text = ""
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text += str(block.get("text", ""))
        elif isinstance(block, str):
            text += block
    return text.strip()


def _preview(content: str, *, limit: int = 80) -> str:
    preview = content[:limit]
    if len(content) > limit:
        preview += "…"
    return preview
