"""Update-stream interrupt parsing for Textual streamed execution."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from invincat_cli import textual_adapter as adapter_mod

logger = logging.getLogger(__name__)


async def handle_update_stream_chunk(
    *,
    adapter: Any,
    data: Any,
    interrupt_occurred: bool,
    hitl_request_adapter: Any,
    ask_user_adapter: Any,
    pending_interrupts: dict[str, Any],
    pending_ask_user: dict[str, Any],
    pending_approve_plan: dict[str, Any],
    error_ask_user_ids: dict[str, str],
) -> tuple[bool, bool]:
    if not isinstance(data, dict):
        return False, interrupt_occurred

    # Check for interrupts
    if "__interrupt__" in data:
        interrupts: list[Any] = data["__interrupt__"]
        if interrupts:
            for interrupt_obj in interrupts:
                iv = interrupt_obj.value
                if (
                    isinstance(iv, dict)
                    and iv.get("type") == "ask_user"
                ):
                    try:
                        validated_ask_user = (
                            ask_user_adapter.validate_python(iv)
                        )
                        pending_ask_user[interrupt_obj.id] = (
                            validated_ask_user
                        )
                        interrupt_occurred = True
                        await adapter_mod.dispatch_hook(
                            "input.required", {}
                        )
                    except ValidationError:
                        logger.exception(
                            "Invalid ask_user interrupt payload; "
                            "resuming with error so the agent can recover"
                        )
                        error_ask_user_ids[interrupt_obj.id] = (
                            "invalid ask_user payload"
                        )
                        interrupt_occurred = True
                elif (
                    isinstance(iv, dict)
                    and iv.get("type") == "approve_plan"
                ):
                    try:
                        approve_plan_adapter = (
                            adapter_mod._get_approve_plan_adapter()
                        )
                        validated_approve_plan = approve_plan_adapter.validate_python(
                            iv
                        )
                        pending_approve_plan[
                            interrupt_obj.id
                        ] = validated_approve_plan
                        interrupt_occurred = True
                        await adapter_mod.dispatch_hook(
                            "input.required", {}
                        )
                    except ValidationError:
                        logger.exception(
                            "Invalid approve_plan interrupt payload; "
                            "resuming with error so the agent can recover"
                        )
                        error_ask_user_ids[interrupt_obj.id] = (
                            "invalid approve_plan payload"
                        )
                        interrupt_occurred = True
                else:
                    try:
                        validated_request = hitl_request_adapter.validate_python(
                            iv
                        )
                        pending_interrupts[interrupt_obj.id] = (
                            validated_request
                        )
                        interrupt_occurred = True
                        await adapter_mod.dispatch_hook(
                            "input.required", {}
                        )
                    except ValidationError:
                        logger.exception(
                            "Invalid HITL interrupt payload; "
                            "aborting turn cleanly"
                        )
                        if adapter._set_spinner:
                            await adapter._set_spinner(None)
                        await adapter._mount_message(
                            adapter_mod.AppMessage(
                                "Internal error: could not parse tool approval request. "
                                "Please try again."
                            )
                        )
                        return True, interrupt_occurred

    # Check for todo updates (not yet implemented in Textual UI)
    chunk_data = next(iter(data.values())) if data else None
    if (
        chunk_data
        and isinstance(chunk_data, dict)
        and "todos" in chunk_data
    ):
        pass  # Future: render todo list widget
    return False, interrupt_occurred
