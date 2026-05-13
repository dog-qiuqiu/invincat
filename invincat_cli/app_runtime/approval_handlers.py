"""App-bound approval and ask-user interaction handlers."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import suppress
from typing import Any

from textual.app import ScreenStackError
from textual.containers import Container
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Static

from invincat_cli.app_runtime.approval import (
    APPROVAL_PLACEHOLDER_CLASS,
    APPROVAL_PLACEHOLDER_TEXT,
    INTERACTION_POLL_SECONDS,
    DEFERRED_APPROVAL_POLL_SECONDS,
    DEFERRED_APPROVAL_TIMEOUT_SECONDS,
    build_approve_plan_action_request,
    build_auto_approved_shell_message,
    build_interaction_widget_id,
    deadline_expired,
    map_raw_approval_to_plan_decision,
    plan_interrupt_guard_disallowed_tools,
    plan_todos_fingerprint,
    pending_interaction_timeout_log,
    pending_widget_deadline,
    resolve_auto_approved_shell_commands,
    should_cancel_detached_placeholder,
)
from invincat_cli.core.ask_user_types import AskUserWidgetResult, Question
from invincat_cli.widgets.messages import AppMessage

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


async def handle_plan_guard_auto_reject(
    app: Any,  # noqa: ANN401
    disallowed_tool_names: list[str],
) -> None:
    """Mount the `/plan` guard rejection notice and approval prompt."""
    try:
        await app._maybe_approve_current_planner_todos()
    except Exception:
        logger.debug(
            "Failed to trigger immediate /plan approval before rejecting tool call",
            exc_info=True,
        )

    denied = ", ".join(disallowed_tool_names)
    try:
        from invincat_cli.i18n import t

        messages = app.query_one("#messages", Container)
        await app._mount_before_queued(
            messages,
            AppMessage(t("plan.auto_reject_non_plan_tool").format(tools=denied)),
        )
    except Exception:  # noqa: BLE001  # best-effort status message
        logger.debug(
            "Failed to mount /plan auto-reject notice",
            exc_info=True,
        )


async def mount_auto_approval_messages(
    app: Any,  # noqa: ANN401
    commands: list[str],
) -> None:
    """Mount system messages for shell commands approved by allow-list."""
    try:
        messages = app.query_one("#messages", Container)
        for command in commands:
            auto_msg = AppMessage(build_auto_approved_shell_message(command))
            await app._mount_before_queued(messages, auto_msg)
        with suppress(NoMatches, ScreenStackError):
            app.query_one("#chat", VerticalScroll).anchor()
    except Exception:  # noqa: BLE001  # Resilient auto-message display
        logger.debug("Failed to display auto-approval message", exc_info=True)


async def wait_for_pending_approval_widget(app: Any) -> None:  # noqa: ANN401
    """Wait briefly for any active approval widget before showing another."""
    if app._pending_approval_widget is None:
        return

    queue_deadline = pending_widget_deadline(now=_monotonic())
    while app._pending_approval_widget is not None:  # noqa: ASYNC110
        if deadline_expired(now=_monotonic(), deadline=queue_deadline):
            logger.warning(pending_interaction_timeout_log(kind="approval"))
            break
        await asyncio.sleep(INTERACTION_POLL_SECONDS)


async def mount_approval_widget(
    app: Any,  # noqa: ANN401
    menu: Any,  # noqa: ANN401
    result_future: asyncio.Future[dict[str, str]],
) -> None:
    """Mount the approval menu widget inline in the messages area."""
    try:
        messages = app.query_one("#messages", Container)
        await app._mount_before_queued(messages, menu)
        app.call_after_refresh(menu.scroll_visible)
        app.call_after_refresh(menu.focus)
    except Exception as exc:
        logger.exception(
            "Failed to mount approval menu (id=%s) in messages container",
            menu.id,
        )
        app._pending_approval_widget = None
        if not result_future.done():
            result_future.set_exception(exc)


async def remove_approval_placeholder(
    app: Any,  # noqa: ANN401
    *,
    context: str,
) -> None:
    """Remove any mounted deferred approval placeholder."""
    placeholder = app._approval_placeholder
    if placeholder is None:
        return
    app._approval_placeholder = None
    if not placeholder.is_attached:
        return
    try:
        await placeholder.remove()
    except Exception:
        logger.warning(
            "Failed to remove approval placeholder during %s",
            context,
            exc_info=True,
        )


def enable_auto_approve(app: Any) -> None:  # noqa: ANN401
    """Sync auto-approve enabled state across app, status bar, and session."""
    app._auto_approve = True
    if app._status_bar:
        app._status_bar.set_auto_approve(enabled=True)
    if app._session_state:
        app._session_state.auto_approve = True


async def request_approve_plan(
    app: Any,  # noqa: ANN401
    todos: list[dict[str, Any]],
) -> asyncio.Future[dict[str, Any]]:
    """Display plan approval using the standard approval menu."""
    loop = asyncio.get_running_loop()
    mapped_future: asyncio.Future[dict[str, Any]] = loop.create_future()

    action_request = build_approve_plan_action_request(todos)
    app._planner_prompted_todos_fingerprint = plan_todos_fingerprint(todos)

    raw_future = await app._request_approval(
        [action_request],
        app._assistant_id,
        bypass_plan_guard=True,
        allow_auto_approve=False,
    )

    async def _map_plan_decision() -> None:
        try:
            raw = await raw_future
            mapped = map_raw_approval_to_plan_decision(raw)
            if not mapped_future.done():
                mapped_future.set_result(mapped)
        except Exception as exc:
            if not mapped_future.done():
                mapped_future.set_exception(exc)

    app.run_worker(_map_plan_decision(), exclusive=False)
    return mapped_future


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


async def remove_ask_user_widget(
    widget: Any,  # noqa: ANN401
    *,
    context: str,
) -> None:
    """Remove an ask_user widget without surfacing cleanup races."""
    try:
        await widget.remove()
    except Exception:
        logger.debug(
            "Failed to remove ask-user widget during %s",
            context,
            exc_info=True,
        )


async def wait_for_pending_ask_user_widget(app: Any) -> None:  # noqa: ANN401
    """Wait for an active ask_user widget, forcing cleanup on timeout."""
    if app._pending_ask_user_widget is None:
        return

    deadline = pending_widget_deadline(now=_monotonic())
    while app._pending_ask_user_widget is not None:
        if deadline_expired(now=_monotonic(), deadline=deadline):
            logger.error(pending_interaction_timeout_log(kind="ask_user"))
            old_widget = app._pending_ask_user_widget
            if old_widget is not None:
                old_widget.action_cancel()
                app._pending_ask_user_widget = None
                await app._remove_ask_user_widget(
                    old_widget,
                    context="ask-user timeout cleanup",
                )
            break
        await asyncio.sleep(INTERACTION_POLL_SECONDS)


async def mount_ask_user_widget(
    app: Any,  # noqa: ANN401
    menu: Any,  # noqa: ANN401
    result_future: asyncio.Future[AskUserWidgetResult],
) -> None:
    """Mount the ask_user widget and focus the active field."""
    try:
        messages = app.query_one("#messages", Container)
        await app._mount_before_queued(messages, menu)
        app.call_after_refresh(menu.scroll_visible)
        app.call_after_refresh(menu.focus_active)
    except Exception as exc:
        logger.exception(
            "Failed to mount ask-user menu (id=%s)",
            menu.id,
        )
        app._pending_ask_user_widget = None
        if not result_future.done():
            result_future.set_exception(exc)


async def handle_ask_user_menu_answered(app: Any) -> None:  # noqa: ANN401
    """Handle ask_user menu answers: remove widget and refocus input."""
    if app._pending_ask_user_widget:
        widget = app._pending_ask_user_widget
        app._pending_ask_user_widget = None
        await app._remove_ask_user_widget(widget, context="ask-user answered")

    if app._chat_input:
        app.call_after_refresh(app._chat_input.focus_input)


async def handle_ask_user_menu_cancelled(app: Any) -> None:  # noqa: ANN401
    """Handle ask_user menu cancellation: remove widget and refocus input."""
    if app._pending_ask_user_widget:
        widget = app._pending_ask_user_widget
        app._pending_ask_user_widget = None
        await app._remove_ask_user_widget(widget, context="ask-user cancelled")

    if app._chat_input:
        app.call_after_refresh(app._chat_input.focus_input)


async def handle_approve_widget_approved(app: Any) -> None:  # noqa: ANN401
    """Handle approve widget approval."""
    from invincat_cli.i18n import t

    await app._mount_message(AppMessage(t("approve.approved")))
    if app._chat_input:
        app.call_after_refresh(app._chat_input.focus_input)


async def handle_approve_widget_rejected(app: Any) -> None:  # noqa: ANN401
    """Handle approve widget rejection."""
    from invincat_cli.i18n import t

    await app._mount_message(AppMessage(t("approve.rejected")))
    if app._chat_input:
        app.call_after_refresh(app._chat_input.focus_input)
