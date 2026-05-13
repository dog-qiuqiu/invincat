"""App-bound approval and ask-user interaction handlers."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from textual.containers import Container
from textual.widgets import Static

from invincat_cli.app_runtime.approval import (
    APPROVAL_PLACEHOLDER_CLASS,
    APPROVAL_PLACEHOLDER_TEXT,
    DEFERRED_APPROVAL_POLL_SECONDS,
    DEFERRED_APPROVAL_TIMEOUT_SECONDS,
    build_interaction_widget_id,
    deadline_expired,
    plan_interrupt_guard_disallowed_tools,
    resolve_auto_approved_shell_commands,
    should_cancel_detached_placeholder,
)
from invincat_cli.core.ask_user_types import AskUserWidgetResult, Question

logger = logging.getLogger(__name__)
_monotonic = time.monotonic


async def request_approval(
    app: Any,  # noqa: ANN401
    action_requests: Any,  # noqa: ANN401
    assistant_id: str | None,
    *,
    bypass_plan_guard: bool = False,
    allow_auto_approve: bool = True,
) -> asyncio.Future:
    """Request user approval inline in the messages area."""
    from invincat_cli.config import (
        SHELL_TOOL_NAMES,
        is_shell_command_allowed,
        settings,
    )

    loop = asyncio.get_running_loop()
    result_future: asyncio.Future = loop.create_future()

    disallowed_tool_names = plan_interrupt_guard_disallowed_tools(
        action_requests,
        bypass_plan_guard=bypass_plan_guard,
        plan_mode=bool(app._session_state and app._session_state.plan_mode),
        active_turn_is_planner=app._active_turn_is_planner,
    )
    if disallowed_tool_names:
        result_future.set_result({"type": "reject"})
        await app._handle_plan_guard_auto_reject(disallowed_tool_names)
        return result_future

    approved_commands = resolve_auto_approved_shell_commands(
        action_requests,
        shell_allow_list=settings.shell_allow_list or [],
        shell_tool_names=SHELL_TOOL_NAMES,
        cwd=app._cwd,
        is_shell_command_allowed=is_shell_command_allowed,
    )
    if approved_commands is not None:
        result_future.set_result({"type": "approve"})
        await app._mount_auto_approval_messages(approved_commands)
        return result_future

    await app._wait_for_pending_approval_widget()

    from invincat_cli.widgets.approval import ApprovalMenu

    unique_id = build_interaction_widget_id(
        prefix="approval-menu",
        token=uuid.uuid4().hex[:8],
    )
    menu = ApprovalMenu(
        action_requests,
        assistant_id,
        allow_auto_approve=allow_auto_approve,
        id=unique_id,
    )
    menu.set_future(result_future)

    app._pending_approval_widget = menu

    if app._is_user_typing():
        placeholder = Static(
            APPROVAL_PLACEHOLDER_TEXT,
            classes=APPROVAL_PLACEHOLDER_CLASS,
        )
        app._approval_placeholder = placeholder
        try:
            messages = app.query_one("#messages", Container)
            await app._mount_before_queued(messages, placeholder)
            app.call_after_refresh(placeholder.scroll_visible)
        except Exception:
            logger.exception("Failed to mount approval placeholder")
            app._approval_placeholder = None
            await app._mount_approval_widget(menu, result_future)
            return result_future

        app.run_worker(
            app._deferred_show_approval(placeholder, menu, result_future),
            exclusive=False,
        )
    else:
        await app._mount_approval_widget(menu, result_future)

    return result_future


async def deferred_show_approval(
    app: Any,  # noqa: ANN401
    placeholder: Static,
    menu: Any,  # noqa: ANN401
    result_future: asyncio.Future[dict[str, str]],
) -> None:
    """Wait until the user is idle, then swap the placeholder for the real menu."""
    try:
        deadline = _monotonic() + DEFERRED_APPROVAL_TIMEOUT_SECONDS
        while app._is_user_typing():  # Simple polling
            if deadline_expired(now=_monotonic(), deadline=deadline):
                logger.warning(
                    "Timed out waiting for user to stop typing; showing approval now"
                )
                break
            await asyncio.sleep(DEFERRED_APPROVAL_POLL_SECONDS)

        if should_cancel_detached_placeholder(
            placeholder_attached=placeholder.is_attached
        ):
            logger.warning(
                "Approval placeholder detached before menu shown (id=%s)",
                menu.id,
            )
            app._approval_placeholder = None
            app._pending_approval_widget = None
            if not result_future.done():
                result_future.cancel()
            return

        app._approval_placeholder = None
        try:
            await placeholder.remove()
        except Exception:
            logger.warning(
                "Failed to remove approval placeholder during swap",
                exc_info=True,
            )
        await app._mount_approval_widget(menu, result_future)
    except BaseException:
        if not result_future.done():
            app._pending_approval_widget = None
            app._approval_placeholder = None
            result_future.cancel()
        raise


async def request_ask_user(
    app: Any,  # noqa: ANN401
    questions: list[Question],
) -> asyncio.Future[AskUserWidgetResult]:
    """Display the ask_user widget and return a Future with the user response."""
    loop = asyncio.get_running_loop()
    result_future: asyncio.Future[AskUserWidgetResult] = loop.create_future()

    await app._wait_for_pending_ask_user_widget()

    from invincat_cli.widgets.ask_user import AskUserMenu

    unique_id = build_interaction_widget_id(
        prefix="ask-user-menu",
        token=uuid.uuid4().hex[:8],
    )
    menu = AskUserMenu(questions, id=unique_id)
    menu.set_future(result_future)

    app._pending_ask_user_widget = menu
    await app._mount_ask_user_widget(menu, result_future)

    return result_future
