"""Validation helpers for textual adapter interrupts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from invincat_cli.core.ask_user_types import AskUserRequest

if TYPE_CHECKING:
    from langchain_core.messages import AIMessage
    from pydantic import TypeAdapter

_hitl_adapter_cache: TypeAdapter | None = None
"""Lazy singleton for the HITL request validator."""


def _get_hitl_request_adapter(hitl_request_type: type) -> TypeAdapter:
    """Return a cached `TypeAdapter(HITLRequest)`.

    Avoids re-compiling the pydantic schema on every `execute_task_textual` call.

    Args:
        hitl_request_type: The `HITLRequest` class (passed in because
            it is imported locally by the caller).

    Returns:
        Shared `TypeAdapter` instance.
    """
    global _hitl_adapter_cache  # noqa: PLW0603
    if _hitl_adapter_cache is None:
        from pydantic import TypeAdapter

        _hitl_adapter_cache = TypeAdapter(hitl_request_type)
    return _hitl_adapter_cache


_ask_user_adapter_cache: TypeAdapter | None = None
"""Lazy singleton for the `ask_user` interrupt validator."""

_approve_plan_adapter_cache: TypeAdapter | None = None
"""Lazy singleton for the `approve_plan` interrupt validator."""


def _get_ask_user_adapter() -> TypeAdapter:
    """Return a cached `TypeAdapter(AskUserRequest)`.

    Returns:
        Shared `TypeAdapter` instance.
    """
    global _ask_user_adapter_cache  # noqa: PLW0603
    if _ask_user_adapter_cache is None:
        from pydantic import TypeAdapter

        _ask_user_adapter_cache = TypeAdapter(AskUserRequest)
    return _ask_user_adapter_cache


def _get_approve_plan_adapter() -> TypeAdapter:
    """Return a cached `TypeAdapter(ApprovePlanRequest)`.

    Returns:
        Shared `TypeAdapter` instance.
    """
    global _approve_plan_adapter_cache  # noqa: PLW0603
    if _approve_plan_adapter_cache is None:
        from pydantic import TypeAdapter

        from invincat_cli.middleware.approve_plan import ApprovePlanRequest

        _approve_plan_adapter_cache = TypeAdapter(ApprovePlanRequest)
    return _approve_plan_adapter_cache




def _build_interrupted_ai_message(
    pending_text_by_namespace: dict[tuple, str],
    current_tool_messages: dict[str, Any],
) -> AIMessage | None:
    """Build an AIMessage capturing interrupted state (text + tool calls).

    Args:
        pending_text_by_namespace: Dict of accumulated text by namespace
        current_tool_messages: Dict of tool_id -> ToolCallMessage widget

    Returns:
        AIMessage with accumulated content and tool calls, or None if empty.
    """
    from langchain_core.messages import AIMessage

    main_ns_key = ()
    accumulated_text = pending_text_by_namespace.get(main_ns_key, "").strip()

    # Reconstruct tool_calls from displayed tool messages
    tool_calls = []
    for tool_id, tool_widget in list(current_tool_messages.items()):
        tool_calls.append(
            {
                "id": tool_id,
                "name": tool_widget._tool_name,
                "args": tool_widget._args,
            }
        )

    if not accumulated_text and not tool_calls:
        return None

    return AIMessage(
        content=accumulated_text,
        tool_calls=tool_calls or [],
    )
