"""Interrupt resume handling for Textual streamed execution."""

from __future__ import annotations

import logging
from typing import Any

from invincat_cli import textual_adapter as adapter_mod
from invincat_cli.i18n import t
from invincat_cli.textual_adapter.utils import normalize_tool_id as _normalize_tool_id

logger = logging.getLogger(__name__)


async def build_interrupt_resume_payload(
    *,
    adapter: Any,
    session_state: Any,
    assistant_id: str | None,
    file_op_tracker: Any,
    pending_ask_user: dict[str, Any],
    pending_approve_plan: dict[str, Any],
    pending_interrupts: dict[str, Any],
    error_ask_user_ids: dict[str, str],
    approve_decision_cls: type,
    reject_decision_cls: type,
) -> tuple[dict[str, Any], bool, bool]:
    any_rejected = False
    suppress_resumed_output = False
    resume_payload: dict[str, Any] = {}

    # Inject error resumes for ask_user interrupts that failed validation.
    # This unblocks the graph so the agent can handle the error gracefully
    # rather than leaving the interrupt unresolved.
    for interrupt_id, error_msg in error_ask_user_ids.items():
        resume_payload[interrupt_id] = {
            "status": "error",
            "error": error_msg,
        }

    for interrupt_id, ask_req in list(pending_ask_user.items()):
        questions = ask_req["questions"]

        if adapter._request_ask_user:
            if adapter._set_spinner:
                await adapter._set_spinner(None)
            result: dict[str, Any] = {
                "type": "error",
                "error": "ask_user callback returned no response",
            }
            try:
                future = await adapter._request_ask_user(questions)
            except Exception:
                logger.exception("Failed to mount ask_user widget")
                result = {
                    "type": "error",
                    "error": "failed to display ask_user prompt",
                }
                future = None

            if future is None:
                logger.error(
                    "ask_user callback returned no Future; "
                    "reporting as error"
                )
            else:
                try:
                    future_result = await future
                    if isinstance(future_result, dict):
                        result = future_result
                    else:
                        logger.error(
                            "ask_user future returned non-dict result: %s",
                            type(future_result).__name__,
                        )
                        result = {
                            "type": "error",
                            "error": "invalid ask_user widget result",
                        }
                except Exception:
                    logger.exception(
                        "ask_user future resolution failed; "
                        "reporting as error"
                    )
                    result = {
                        "type": "error",
                        "error": "failed to receive ask_user response",
                    }

            result_type = result.get("type")
            if result_type == "answered":
                answers = result.get("answers", [])
                if isinstance(answers, list):
                    resume_payload[interrupt_id] = {"answers": answers}
                    tool_id = ask_req["tool_call_id"]
                    norm_tool_id = _normalize_tool_id(tool_id)
                    if (
                        norm_tool_id
                        and norm_tool_id in adapter._current_tool_messages
                    ):
                        tool_msg = adapter._current_tool_messages[
                            norm_tool_id
                        ]
                        tool_msg.set_success(
                            t("ask_user.tool_result_answered")
                        )
                        adapter._current_tool_messages.pop(
                            norm_tool_id, None
                        )
                else:
                    logger.error(
                        "ask_user answered payload had non-list "
                        "answers: %s",
                        type(answers).__name__,
                    )
                    resume_payload[interrupt_id] = {
                        "status": "error",
                        "error": "invalid ask_user answers payload",
                        "answers": ["" for _ in questions],
                    }
                    any_rejected = True
            elif result_type == "cancelled":
                resume_payload[interrupt_id] = {
                    "status": "cancelled",
                    "answers": ["" for _ in questions],
                }
                any_rejected = True
            else:
                error_text = result.get("error")
                if not isinstance(error_text, str) or not error_text:
                    error_text = "ask_user interaction failed"
                resume_payload[interrupt_id] = {
                    "status": "error",
                    "error": error_text,
                    "answers": ["" for _ in questions],
                }
                any_rejected = True
        else:
            logger.warning(
                "ask_user interrupt received but no UI callback is "
                "registered; reporting as error"
            )
            resume_payload[interrupt_id] = {
                "status": "error",
                "error": "ask_user not supported by this UI",
                "answers": ["" for _ in questions],
            }

    for interrupt_id, approve_req in list(pending_approve_plan.items()):
        todos = approve_req["todos"]

        if adapter._request_approve_plan:
            if adapter._set_spinner:
                await adapter._set_spinner(None)
            result: dict[str, Any] = {
                "type": "error",
                "error": "approve_plan callback returned no response",
            }
            try:
                future = await adapter._request_approve_plan(todos)
            except Exception:
                logger.exception("Failed to mount approve_plan widget")
                result = {
                    "type": "error",
                    "error": "failed to display approve_plan prompt",
                }
                future = None

            if future is None:
                logger.error(
                    "approve_plan callback returned no Future; "
                    "reporting as error"
                )
            else:
                try:
                    future_result = await future
                    if isinstance(future_result, dict):
                        result = future_result
                    else:
                        logger.error(
                            "approve_plan future returned non-dict result: %s",
                            type(future_result).__name__,
                        )
                        result = {
                            "type": "error",
                            "error": "invalid approve_plan widget result",
                        }
                except Exception:
                    logger.exception(
                        "approve_plan future resolution failed; "
                        "reporting as error"
                    )
                    result = {
                        "type": "error",
                        "error": "failed to receive approve_plan response",
                    }

            result_type = result.get("type")
            if result_type == "approved":
                resume_payload[interrupt_id] = {"type": "approved"}
                tool_id = approve_req["tool_call_id"]
                norm_tool_id = _normalize_tool_id(tool_id)
                if (
                    norm_tool_id
                    and norm_tool_id in adapter._current_tool_messages
                ):
                    tool_msg = adapter._current_tool_messages[norm_tool_id]
                    tool_msg.set_success(t("approve.tool_result_approved"))
                    adapter._current_tool_messages.pop(norm_tool_id, None)
            elif result_type == "rejected":
                resume_payload[interrupt_id] = {"type": "rejected"}
                any_rejected = True
            else:
                error_text = result.get("error")
                if not isinstance(error_text, str) or not error_text:
                    error_text = "approve_plan interaction failed"
                resume_payload[interrupt_id] = {
                    "type": "error",
                    "error": error_text,
                }
                any_rejected = True
        else:
            logger.warning(
                "approve_plan interrupt received but no UI callback is "
                "registered; reporting as error"
            )
            resume_payload[interrupt_id] = {
                "type": "error",
                "error": "approve_plan not supported by this UI",
            }

    if any_rejected:
        pending_interrupts = {}

    for interrupt_id, hitl_request in list(pending_interrupts.items()):
        action_requests = hitl_request["action_requests"]

        if session_state.auto_approve:
            decisions: list[Any] = [
                approve_decision_cls(type="approve") for _ in action_requests
            ]
            resume_payload[interrupt_id] = {"decisions": decisions}
            for tool_msg in list(adapter._current_tool_messages.values()):
                tool_msg.set_running()
        else:
            # Batch approval - one dialog for all parallel tool calls
            await adapter_mod.dispatch_hook(
                "permission.request",
                {
                    "tool_names": [
                        r.get("name", "") for r in action_requests
                    ]
                },
            )
            future = await adapter._request_approval(
                action_requests, assistant_id
            )
            decision = await future

            if isinstance(decision, dict):
                decision_type = decision.get("type")

                if decision_type == "auto_approve_all":
                    session_state.auto_approve = True
                    if adapter._on_auto_approve_enabled:
                        adapter._on_auto_approve_enabled()
                    decisions = [
                        approve_decision_cls(type="approve")
                        for _ in action_requests
                    ]
                    tool_msgs = list(
                        adapter._current_tool_messages.values()
                    )
                    for tool_msg in tool_msgs:
                        tool_msg.set_running()
                    for action_request in action_requests:
                        tool_name = action_request.get("name")
                        if tool_name in {
                            "write_file",
                            "edit_file",
                        }:
                            args = action_request.get("args", {})
                            if isinstance(args, dict):
                                file_op_tracker.mark_hitl_approved(
                                    tool_name, args
                                )

                elif decision_type == "approve":
                    decisions = [
                        approve_decision_cls(type="approve")
                        for _ in action_requests
                    ]
                    tool_msgs = list(
                        adapter._current_tool_messages.values()
                    )
                    for tool_msg in tool_msgs:
                        tool_msg.set_running()
                    for action_request in action_requests:
                        tool_name = action_request.get("name")
                        if tool_name in {
                            "write_file",
                            "edit_file",
                        }:
                            args = action_request.get("args", {})
                            if isinstance(args, dict):
                                file_op_tracker.mark_hitl_approved(
                                    tool_name, args
                                )

                elif decision_type == "reject":
                    decisions = [
                        reject_decision_cls(type="reject")
                        for _ in action_requests
                    ]
                    tool_msgs = list(
                        adapter._current_tool_messages.values()
                    )
                    for tool_msg in tool_msgs:
                        tool_msg.set_rejected()
                    adapter.cancel_all_tool_watchdogs()
                    adapter._current_tool_messages.clear()
                    any_rejected = True
                    suppress_resumed_output = True
                else:
                    logger.warning(
                        "Unexpected HITL decision type: %s",
                        decision_type,
                    )
                    decisions = [
                        reject_decision_cls(type="reject")
                        for _ in action_requests
                    ]
                    for tool_msg in list(
                        adapter._current_tool_messages.values()
                    ):
                        tool_msg.set_rejected()
                    adapter.cancel_all_tool_watchdogs()
                    adapter._current_tool_messages.clear()
                    any_rejected = True
                    suppress_resumed_output = True
            else:
                logger.warning(
                    "HITL decision was not a dict: %s",
                    type(decision).__name__,
                )
                decisions = [
                    reject_decision_cls(type="reject") for _ in action_requests
                ]
                for tool_msg in list(
                    adapter._current_tool_messages.values()
                ):
                    tool_msg.set_rejected()
                adapter.cancel_all_tool_watchdogs()
                adapter._current_tool_messages.clear()
                any_rejected = True
                suppress_resumed_output = True

            resume_payload[interrupt_id] = {"decisions": decisions}

            if any_rejected:
                break
    return resume_payload, any_rejected, suppress_resumed_output
