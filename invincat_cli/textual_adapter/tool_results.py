"""Tool result handling for Textual streamed execution."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from invincat_cli import textual_adapter as adapter_mod
from invincat_cli.i18n import t
from invincat_cli.presentation.tool_display import format_tool_message_content
from invincat_cli.textual_adapter.utils import normalize_tool_id as _normalize_tool_id

logger = logging.getLogger(__name__)


async def handle_tool_message(
    *,
    adapter: Any,
    message: Any,
    file_op_tracker: Any,
    processed_wecom_file_tool_ids: set[str],
    on_wecom_file_request: Callable[[dict[str, Any]], Awaitable[None]] | None,
    on_schedule_payload: Callable[[dict[str, Any]], Awaitable[None]] | None,
    pending_text_by_namespace: dict[tuple, str],
    ns_key: tuple,
    assistant_message_by_namespace: dict[tuple, Any],
) -> None:
    tool_name = getattr(message, "name", "")
    tool_status = getattr(message, "status", "success")
    tool_content = format_tool_message_content(
        message.content
    )
    raw_tool_id = getattr(message, "tool_call_id", None)
    tool_id = _normalize_tool_id(raw_tool_id)
    if (
        tool_name == "send_wecom_file"
        and on_wecom_file_request is not None
    ):
        from invincat_cli.wecom.file import (
            parse_wecom_file_request,
        )

        payload = parse_wecom_file_request(message.content)
        if payload is not None:
            dedupe_id = (
                _normalize_tool_id(
                    payload.get("tool_call_id")
                )
                or tool_id
                or _normalize_tool_id(
                    getattr(message, "id", None)
                )
            )
            if dedupe_id in processed_wecom_file_tool_ids:
                logger.debug(
                    "Skipping duplicate WeCom file request tool_call_id=%s",
                    dedupe_id,
                )
            else:
                if dedupe_id is not None:
                    processed_wecom_file_tool_ids.add(
                        dedupe_id
                    )
                try:
                    await on_wecom_file_request(payload)
                except Exception:
                    logger.warning(
                        "WeCom file request callback failed",
                        exc_info=True,
                    )

    if on_schedule_payload is not None:
        from invincat_cli.scheduler.tool import (
            parse_schedule_tool_result,
        )

        sched_payload = parse_schedule_tool_result(
            message.content
        )
        if sched_payload is not None:
            try:
                await on_schedule_payload(sched_payload)
            except Exception:
                logger.warning(
                    "Schedule payload callback failed",
                    exc_info=True,
                )

    logger.debug(
        "ToolMessage received: name=%s, status=%s, raw_tool_id=%s (type=%s), active_keys=%s",
        tool_name,
        tool_status,
        raw_tool_id,
        type(raw_tool_id).__name__,
        list(file_op_tracker.active.keys()),
    )

    tool_msg = None
    tool_args_for_match: dict[str, Any] | None = None

    # Strategy 1: Direct key match on normalized str ID.
    # Use pop(key, None) defensively even though the preceding
    # `in` check makes KeyError impossible in normal asyncio
    # execution — the guard protects against future refactors
    # that might introduce an await between the check and pop.
    if (
        tool_id
        and tool_id in adapter._current_tool_messages
    ):
        tool_msg = adapter._current_tool_messages.pop(
            tool_id, None
        )
        if tool_msg is None:
            logger.warning(
                "Strategy 1: key disappeared between check and pop "
                "for tool_id=%s; will retry with Strategy 2",
                tool_id,
            )
        else:
            logger.debug(
                "Matched ToolMessage by direct key tool_id=%s",
                tool_id,
            )

    # Strategy 2: Match by widget's _tool_call_id attribute.
    # When the widget was stored under a different (e.g. index-based)
    # key, pop it by that key, then sync:
    #   1. The widget's _tool_call_id attribute → real tool_id
    #   2. The MessageStore entry's tool_call_id → real tool_id
    # Without (2) the store's index still maps the OLD key, so
    # any future prune/hydrate lookup by tool_id would miss it.
    if not tool_msg and tool_id:
        for key, msg in list(
            adapter._current_tool_messages.items()
        ):
            widget_id = _normalize_tool_id(
                getattr(msg, "_tool_call_id", None)
            )
            if widget_id == tool_id:
                tool_msg = (
                    adapter._current_tool_messages.pop(key)
                )
                logger.debug(
                    "Matched ToolMessage by _tool_call_id=%s to key=%s",
                    tool_id,
                    key,
                )
                # Sync widget attribute to the canonical tool_id
                # so any caller inspecting _tool_call_id sees the
                # correct value after this point.
                if key != tool_id:
                    tool_msg._tool_call_id = tool_id
                    # Sync the MessageStore index so that
                    # get_message_by_tool_call_id(tool_id) finds
                    # this record after pruning/hydration.
                    if (
                        tool_msg.id
                        and adapter._message_store
                    ):
                        try:
                            adapter._message_store.update_message(
                                tool_msg.id,
                                tool_call_id=tool_id,
                            )
                        except Exception:  # noqa: BLE001
                            logger.warning(
                                "Strategy 2: failed to sync "
                                "tool_call_id in store for "
                                "msg id=%s tool_id=%s",
                                tool_msg.id,
                                tool_id,
                                exc_info=True,
                            )
                break

    # Strategy 3: Match by tool_name (fallback for missing IDs)
    if (
        not tool_msg
        and tool_name
        and adapter._current_tool_messages
    ):
        candidates = [
            (key, msg)
            for key, msg in list(
                adapter._current_tool_messages.items()
            )
            if getattr(msg, "_tool_name", None) == tool_name
            and getattr(msg, "is_attached", True)
        ]
        if len(candidates) == 1:
            key, msg = candidates[0]
            tool_msg = adapter._current_tool_messages.pop(
                key
            )
            logger.debug(
                "Matched ToolMessage by tool_name=%s to key=%s",
                tool_name,
                key,
            )
        elif len(candidates) > 1:
            for key, msg in candidates:
                status = getattr(msg, "_status", "pending")
                if status in ("pending", "running"):
                    tool_msg = (
                        adapter._current_tool_messages.pop(
                            key
                        )
                    )
                    logger.debug(
                        "Matched ToolMessage by tool_name=%s status=%s to key=%s",
                        tool_name,
                        status,
                        key,
                    )
                    break
            if not tool_msg and candidates:
                key, msg = candidates[0]
                tool_msg = (
                    adapter._current_tool_messages.pop(key)
                )
                logger.debug(
                    "Matched ToolMessage by tool_name=%s (first candidate) to key=%s",
                    tool_name,
                    key,
                )

    # Extract args from matched widget for file_op_tracker matching
    if tool_msg:
        tool_args_for_match = getattr(
            tool_msg, "_args", None
        )

    # Now call complete_with_message with args for better matching
    record = file_op_tracker.complete_with_message(
        message, tool_args_for_match
    )

    logger.debug(
        "File op record lookup result: %s (diff=%s)",
        "found" if record else "not found",
        record.diff[:100] + "..."
        if record and record.diff
        else "empty/N/A",
    )

    if not tool_msg and tool_id:
        logger.warning(
            "ToolMessage unmatched: tool_id=%s name=%s "
            "remaining keys=%s; widget may be pruned or ID mismatch",
            tool_id,
            tool_name,
            [
                (k, type(k).__name__)
                for k in adapter._current_tool_messages.keys()
            ],
        )

    # FIX: normalize output_str — never pass empty string to
    # set_success/set_error; use a placeholder so the result
    # frame is never silently blank.
    output_str = (
        str(tool_content) if tool_content else "(no output)"
    )

    # If the file-op tracker recorded an internal error
    # (e.g. couldn't read back the file after writing) but
    # the ToolMessage status is still "success", surface the
    # internal error in the output so the user isn't silently
    # left without a diff and no indication of why.
    if (
        record is not None
        and record.status == "error"
        and record.error
        and tool_status == "success"
    ):
        logger.warning(
            "File op internal error for tool=%s: %s",
            tool_name,
            record.error,
        )
        output_str = f"{output_str}\n\n[diff unavailable: {record.error}]"
    elif (
        record is None
        and tool_name in ("write_file", "edit_file")
        and tool_status == "success"
    ):
        logger.warning(
            "DiffMessage skipped: no file-op record for "
            "tool=%s tool_call_id=%s (operation not tracked)",
            tool_name,
            tool_id,
        )
        output_str = f"{output_str}\n\n[diff unavailable: operation was not tracked]"

    if tool_msg:
        if tool_status == "success":
            tool_msg.set_success(output_str)
        else:
            tool_msg.set_error(output_str)
            await adapter_mod.dispatch_hook(
                "tool.error",
                {"tool_names": [tool_msg._tool_name]},
            )
        if tool_msg.id and adapter._message_store:
            from invincat_cli.widgets.message_store import (
                ToolStatus as _TS,
            )

            _final = (
                _TS.SUCCESS
                if tool_status == "success"
                else _TS.ERROR
            )
            adapter._message_store.update_message(
                tool_msg.id, tool_status=_final
            )
    elif tool_id:
        # Widget not in current tracking map — it was either
        # pruned before the ToolMessage arrived, or the index
        # was already cleared by mark_pruned() (see #2.3 fix).
        # Either way, look up the stored data and recreate a
        # widget so the result is always visible to the user.
        msg_data = None
        if adapter._message_store:
            # get_message_by_tool_call_id normalises to str
            # internally — a single call is sufficient.
            msg_data = adapter._message_store.get_message_by_tool_call_id(
                tool_id
            )

        if msg_data:
            logger.debug(
                "ToolMessage tool_call_id=%s widget pruned "
                "(found in store); recreating widget",
                tool_id,
            )
            tool_msg = adapter_mod.ToolCallMessage(
                msg_data.tool_name or tool_name,
                msg_data.tool_args or {},
                tool_call_id=tool_id,
            )
            await adapter._mount_message(tool_msg)

            if tool_status == "success":
                tool_msg.set_success(output_str)
                # Any associated DiffMessage is shown via the
                # `if record:` block further below — no extra
                # work is needed here.
            else:
                tool_msg.set_error(output_str)
                await adapter_mod.dispatch_hook(
                    "tool.error",
                    {"tool_names": [tool_msg._tool_name]},
                )
        else:
            # Last-resort: neither the live tracking map nor
            # the message store has a record.  This can happen
            # when there is a race between pruning and the store
            # write, or when the ID was never registered (e.g.
            # a ToolMessage for a tool that was never streamed).
            # Mount a fallback widget so the result is never
            # silently swallowed.
            logger.warning(
                "ToolMessage tool_call_id=%s name=%s not found in "
                "tracking map or store; mounting fallback widget",
                tool_id,
                tool_name,
            )
            # Use args from the file-op record if available,
            # so the fallback widget isn't completely empty.
            fallback_args = (
                record.args
                if record is not None and record.args
                else {}
            )
            fallback_msg = adapter_mod.ToolCallMessage(
                tool_name or "unknown",
                fallback_args,
                tool_call_id=tool_id,
            )
            await adapter._mount_message(fallback_msg)
            if tool_status == "success":
                fallback_msg.set_success(output_str)
            else:
                fallback_msg.set_error(output_str)
                await adapter_mod.dispatch_hook(
                    "tool.error",
                    {
                        "tool_names": [
                            tool_name or "unknown"
                        ]
                    },
                )
            # Also persist to store so future lookups work
            adapter._update_tool_message_in_store(
                tool_id, tool_status, output_str
            )

    if tool_name == "task":
        adapter._subagent_activity.complete_task(tool_id)

    # Reshow spinner only when all in-flight tools have
    # completed (avoids premature "Thinking..." when
    # parallel tool calls are active).
    if (
        adapter._set_spinner
        and not adapter._current_tool_messages
    ):
        await adapter._set_spinner(t("status.thinking"))

    # Show file operation results - always show diffs in chat
    if record:
        pending_text = pending_text_by_namespace.get(
            ns_key, ""
        )
        if pending_text:
            await adapter_mod._flush_assistant_text_ns(
                adapter,
                pending_text,
                ns_key,
                assistant_message_by_namespace,
            )
            pending_text_by_namespace[ns_key] = ""
        if record.diff:
            diff_msg = adapter_mod.DiffMessage(
                record.diff, record.display_path
            )
            await adapter._mount_message(diff_msg)
        else:
            logger.debug(
                "No diff for tool=%s tool_call_id=%s "
                "(content unchanged or after-content unreadable)",
                tool_name,
                tool_id,
            )
    else:
        # record is None — file_op_tracker has no matching entry.
        # For non-file tools this is expected; for write_file /
        # edit_file the warning and output_str annotation were
        # already applied in the elif branch above.
        if tool_name not in ("write_file", "edit_file"):
            logger.debug(
                "No file-op record for tool=%s tool_call_id=%s "
                "(non-file tool, expected)",
                tool_name,
                tool_id,
            )
