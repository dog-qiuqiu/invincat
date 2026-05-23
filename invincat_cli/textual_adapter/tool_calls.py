"""Streaming tool-call chunk handling for the Textual adapter."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from invincat_cli import textual_adapter as adapter_mod
from invincat_cli.i18n import t
from invincat_cli.textual_adapter.utils import normalize_tool_id as _normalize_tool_id

logger = logging.getLogger(__name__)


async def handle_tool_call_block(
    *,
    adapter: Any,
    block: dict[str, Any],
    displayed_tool_ids: set[str],
    tool_call_buffers: dict[str | int, dict],
    planner_mode_enforced: bool,
    planner_allowed_tool_set: frozenset[str],
    file_op_tracker: Any,
    pending_text_by_namespace: dict[tuple, str],
    ns_key: tuple,
    assistant_message_by_namespace: dict[tuple, Any],
) -> None:
    chunk_name = block.get("name")
    chunk_args = block.get("args")
    chunk_id = block.get("id")
    chunk_index = block.get("index")

    # Normalize buffer key — always str for consistent
    # lookup in _current_tool_messages.
    raw_buffer_key: str | int
    if chunk_index is not None:
        raw_buffer_key = chunk_index
    elif chunk_id is not None:
        raw_buffer_key = chunk_id
    else:
        # Use a UUID so parallel chunks without id/index
        # don't collide — f"unknown-{len(buffers)}" would
        # repeat when a previous buffer was already popped.
        raw_buffer_key = (
            f"unknown-{uuid.uuid4().hex[:8]}"
        )

    buffer = tool_call_buffers.setdefault(
        raw_buffer_key,
        {
            "name": None,
            "id": None,
            "args": None,
            "args_parts": [],
            "args_finalized": False,
        },
    )

    if chunk_name:
        buffer["name"] = chunk_name
    if chunk_id:
        buffer["id"] = chunk_id

    if isinstance(chunk_args, dict):
        buffer["args"] = chunk_args
        buffer["args_parts"] = []
    elif isinstance(chunk_args, str):
        if chunk_args:
            parts: list[str] = buffer.setdefault(
                "args_parts", []
            )
            if not parts or chunk_args != parts[-1]:
                parts.append(chunk_args)
            buffer["args"] = "".join(parts)
    elif chunk_args is not None:
        buffer["args"] = chunk_args

    buffer_name = buffer.get("name")
    buffer_id = buffer.get("id")

    # Need at least a name before doing anything
    if buffer_name is None:
        return

    # Normalize display_key to str for consistent map keys
    raw_display_key = (
        buffer_id
        if buffer_id is not None
        else raw_buffer_key
    )
    display_key = _normalize_tool_id(
        raw_display_key
    ) or str(raw_display_key)

    tool_blocked_in_plan_mode = (
        planner_mode_enforced
        and buffer_name not in planner_allowed_tool_set
    )

    # --- EARLY MOUNT: show widget as soon as name is known,
    # before args have finished streaming.  This eliminates
    # the blank gap between the assistant text message and
    # the tool call widget appearing in the UI.
    if display_key not in displayed_tool_ids:
        # Check if this buffer was previously mounted under the
        # index-based key (when buffer_id was not yet available).
        # If so, re-key the existing widget instead of creating a
        # new one — this prevents zombie widget accumulation.
        index_key = _normalize_tool_id(
            raw_buffer_key
        ) or str(raw_buffer_key)
        if (
            display_key != index_key
            and index_key
            in adapter._current_tool_messages
        ):
            # Re-key: move existing widget to the real ID key
            existing = (
                adapter._current_tool_messages.pop(
                    index_key
                )
            )
            existing._tool_call_id = display_key
            adapter._current_tool_messages[
                display_key
            ] = existing
            displayed_tool_ids.add(display_key)
            # Also update the store so later result matching uses the canonical
            # tool_call_id even if the widget was first mounted with an index key.
            if existing.id and adapter._message_store:
                try:
                    adapter._message_store.update_message(
                        existing.id,
                        tool_call_id=display_key,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Failed to update tool_call_id in store "
                        "for message id=%s display_key=%s",
                        existing.id,
                        display_key,
                        exc_info=True,
                    )
            # Also re-key the file op tracker so that
            # complete_with_message() can match by UUID
            # when the ToolMessage arrives.  Without this,
            # the tracker still holds the index-based key
            # (e.g. "0") and the UUID lookup fails —
            # causing DiffMessage to never be shown when
            # multiple concurrent file ops are in flight.
            old_record = file_op_tracker.active.pop(
                index_key, None
            )
            if old_record is not None:
                old_record.tool_call_id = display_key
                file_op_tracker.active[display_key] = (
                    old_record
                )
                logger.debug(
                    "Re-keyed file_op_tracker record "
                    "from index key=%s to id=%s",
                    index_key,
                    display_key,
                )
            logger.debug(
                "Re-keyed ToolCallMessage from index key=%s to id=%s",
                index_key,
                display_key,
            )
        else:
            displayed_tool_ids.add(display_key)

            # Flush any pending assistant text first
            pending_text = (
                pending_text_by_namespace.get(
                    ns_key, ""
                )
            )
            if pending_text:
                await adapter_mod._flush_assistant_text_ns(
                    adapter,
                    pending_text,
                    ns_key,
                    assistant_message_by_namespace,
                )
                pending_text_by_namespace[ns_key] = ""
                assistant_message_by_namespace.pop(
                    ns_key, None
                )

            # Hide spinner before showing tool call widget
            if adapter._set_spinner:
                await adapter._set_spinner(None)

            # Mount immediately with empty args — args will be
            # filled in via update_args() once fully parsed.
            logger.debug(
                "Early-mounting ToolCallMessage: name=%s key=%s",
                buffer_name,
                display_key,
            )
            tool_msg = adapter_mod.ToolCallMessage(
                buffer_name,
                {},
                tool_call_id=display_key,
                args_finalized=False,
            )
            await adapter._mount_message(tool_msg)
            adapter._current_tool_messages[
                display_key
            ] = tool_msg

    if tool_blocked_in_plan_mode:
        tool_msg = adapter._current_tool_messages.get(
            display_key
        )
        if tool_msg is not None:
            tool_msg.set_error(
                t("plan.blocked_tool_error")
            )
        buffer["args_finalized"] = True
        tool_call_buffers.pop(raw_buffer_key, None)
        return

    # --- ARGS UPDATE: once args are fully parseable, update
    # the already-visible widget and register the file op.
    if not buffer.get("args_finalized"):
        raw_args = buffer.get("args")
        parsed_args = None

        if isinstance(raw_args, dict):
            parsed_args = raw_args
        elif isinstance(raw_args, str) and raw_args:
            try:
                parsed_args = json.loads(raw_args)
            except json.JSONDecodeError:
                pass  # Still streaming — will retry next chunk

        if parsed_args is not None:
            if not isinstance(parsed_args, dict):
                parsed_args = {"value": parsed_args}

            buffer["args_finalized"] = True

            logger.debug(
                "Args finalized for tool key=%s args=%s",
                display_key,
                repr(parsed_args)[:200],
            )

            # Update the widget with real args now that
            # they have fully streamed in.
            tool_msg = (
                adapter._current_tool_messages.get(
                    display_key
                )
            )
            if tool_msg is not None:
                tool_msg.update_args(parsed_args)
                if buffer_name == "execute":
                    adapter.start_execute_watchdog(
                        display_key,
                        tool_msg,
                        parsed_args,
                    )
                if buffer_name == "task":
                    adapter._subagent_activity.register_task(
                        tool_call_id=display_key,
                        widget=tool_msg,
                        args=parsed_args,
                    )
                if (
                    tool_msg.id
                    and adapter._message_store
                ):
                    adapter._message_store.update_message(
                        tool_msg.id,
                        tool_args=parsed_args,
                    )

            # Register file op only once args are final.
            # Use display_key (always a str) rather than
            # buffer_id (which may still be None when the
            # streaming chunk carries args but not yet an
            # id).  complete_with_message reconciles the
            # key via tool-name fallback when the ToolMessage
            # arrives with its canonical UUID.
            logger.debug(
                "Starting file op: name=%s, display_key=%s, active_keys=%s",
                buffer_name,
                display_key,
                list(file_op_tracker.active.keys()),
            )
            file_op_tracker.start_operation(
                buffer_name, parsed_args, display_key
            )

            tool_call_buffers.pop(raw_buffer_key, None)
