"""App-bound WeCom bridge handlers."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from invincat_cli.app_runtime.wecom import (
    WeComTurnContext,
    create_wecom_message_responder,
    load_wecom_bot_config,
    resolve_wecom_bot_command_decision,
    resolve_wecom_bridge_availability,
    should_clear_wecom_bridge,
    wecom_bot_is_running,
    wecom_bot_missing_config_message,
    wecom_bridge_offline_error,
    wecom_turn_is_busy,
)
from invincat_cli.wecom.media import build_wecom_agent_input_with_media_downloads
from invincat_cli.wecom.bridge import WeComBridge
from invincat_cli.wecom.session import WECOM_AGENT_TIMEOUT
from invincat_cli.wecom.turn import WeComTurnRunner
from invincat_cli.widgets.messages import AppMessage, ErrorMessage, UserMessage

logger = logging.getLogger(__name__)


async def handle_wecombot_command(
    app: Any,  # noqa: ANN401
    command: str,
    *,
    action: str,
) -> None:
    """Manage WeCom bridge lifecycle in current CLI session."""
    await app._mount_message(UserMessage(command))

    decision = resolve_wecom_bot_command_decision(
        action=action,
        running=wecom_bot_is_running(app._wecom_task),
        auto_approve_enabled=app._auto_approve,
    )

    if decision.should_start_bridge:
        app._on_auto_approve_enabled()
        app._wecom_task = asyncio.create_task(app._run_wecombot_bridge())
    elif decision.should_stop_bridge:
        if app._wecom_bridge is not None:
            app._wecom_bridge.stop()
        if wecom_bot_is_running(app._wecom_task):
            app._wecom_task.cancel()
            with suppress(asyncio.CancelledError):
                await app._wecom_task
        app._wecom_task = None
        app._wecom_bridge = None

    await app._mount_message(AppMessage(decision.message))


async def run_wecombot_bridge(app: Any) -> None:  # noqa: ANN401
    """Run WeCom long-connection client and bridge to current session."""
    config = load_wecom_bot_config(os.environ)
    if not config.is_complete:
        await app._mount_message(ErrorMessage(wecom_bot_missing_config_message()))
        return

    async def _on_status(message: str) -> None:
        await app._mount_message(AppMessage(message))

    async def _on_error(message: str) -> None:
        await app._mount_message(ErrorMessage(message))

    async def _on_message(frame: dict[str, Any]) -> None:
        await app._wecom_handle_inbound_message(frame=frame)

    bridge = WeComBridge(
        on_status=_on_status,
        on_error=_on_error,
        on_message=_on_message,
        should_exit=lambda: app._exit,
    )
    app._wecom_bridge = bridge
    try:
        await bridge.run(
            bot_id=config.bot_id,
            secret=config.secret,
            ws_url=config.ws_url,
        )
    finally:
        if should_clear_wecom_bridge(
            current_bridge=app._wecom_bridge,
            bridge=bridge,
        ):
            app._wecom_bridge = None


async def wecom_handle_inbound_message(
    app: Any,  # noqa: ANN401
    *,
    frame: dict[str, Any],
) -> None:
    """Process one inbound WeCom message and deliver a streaming reply."""

    async def _build_agent_input(inbound_frame: dict[str, Any]) -> str:
        return await build_wecom_agent_input_with_media_downloads(
            inbound_frame,
            cwd=app._cwd,
        )

    async def _run_turn(
        text: str,
        inbound_frame: dict[str, Any],
        on_content: Callable[[str], Awaitable[None]],
    ) -> str:
        return await app._process_wecom_message_via_cli(
            text,
            inbound_frame=inbound_frame,
            on_content=on_content,
        )

    responder = create_wecom_message_responder(
        enqueue=app._wecom_enqueue,
        flush=app._wecom_flush_outbox,
        build_agent_input=_build_agent_input,
        run_turn=_run_turn,
        report_error=lambda message: app._mount_message(ErrorMessage(message)),
    )
    await responder.handle(frame)


def wecom_enqueue(app: Any, payload: dict[str, Any]) -> None:  # noqa: ANN401
    availability = resolve_wecom_bridge_availability(app._wecom_bridge)
    if not availability.online:
        logger.debug("Skipping WeCom enqueue while bridge is offline")
        return
    app._wecom_bridge.enqueue(payload)


async def wecom_flush_outbox(app: Any) -> bool:  # noqa: ANN401
    """Flush pending outbound replies using the current live WS connection."""
    availability = resolve_wecom_bridge_availability(app._wecom_bridge)
    if not availability.online:
        return False
    return await app._wecom_bridge.flush_outbox()


async def wecom_send_request(
    app: Any,  # noqa: ANN401
    payload: dict[str, Any],
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Send a WeCom request frame and wait for its matching response."""
    availability = resolve_wecom_bridge_availability(app._wecom_bridge)
    if not availability.online:
        raise wecom_bridge_offline_error()
    return await app._wecom_bridge.send_request(payload, timeout=timeout)


async def process_wecom_message_via_cli(
    app: Any,  # noqa: ANN401
    text: str,
    *,
    inbound_frame: dict[str, Any],
    on_content: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Inject one WeCom message into the current session and return the answer."""

    async def _handle_user_message(
        message: str,
        on_text_delta: Callable[[str, str], Awaitable[None]],
        on_wecom_file_request: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        await app._handle_user_message(
            message,
            on_text_delta=on_text_delta,
            on_wecom_file_request=on_wecom_file_request,
        )

    turn_context = WeComTurnContext(
        get_current_frame=lambda: app._current_wecom_inbound_frame,
        set_current_frame=lambda frame: setattr(
            app,
            "_current_wecom_inbound_frame",
            frame,
        ),
        inbound_frame=inbound_frame,
    )

    runner = WeComTurnRunner(
        lock=app._wecom_lock,
        cwd=app._cwd,
        is_busy=lambda: wecom_turn_is_busy(
            connecting=app._connecting,
            thread_switching=app._thread_switching,
            model_switching=app._model_switching,
            agent_running=app._agent_running,
            shell_running=app._shell_running,
        ),
        get_messages=app._message_store.get_all_messages,
        handle_user_message=_handle_user_message,
        send_request=app._wecom_send_request,
        cancel_timed_out_turn=lambda: cancel_timed_out_turn(app),
        on_content=on_content,
        enter_turn_context=turn_context.enter,
        exit_turn_context=turn_context.exit,
    )
    return await runner.run(text, inbound_frame=inbound_frame)


def cancel_timed_out_turn(app: Any) -> None:  # noqa: ANN401
    """Cancel a WeCom-injected turn after its bridge timeout."""
    if app._shell_worker is not None:
        app._shell_worker.cancel()
    if app._agent_worker is not None:
        app._agent_worker.cancel()
    app._shell_running = False
    app._shell_worker = None
    app._agent_running = False
    app._agent_worker = None
    app._active_turn_is_planner = False
    logger.warning(
        "wecom turn timed out after %.1fs; cancelled active agent/shell worker",
        WECOM_AGENT_TIMEOUT,
    )
