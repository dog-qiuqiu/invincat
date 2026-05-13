"""App-bound agent turn cleanup and error handlers."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

from invincat_cli.app_runtime.agent import (
    build_agent_error_detail,
    resolve_agent_cleanup_start_state,
    resolve_agent_task_exception_decision,
    should_continue_after_deferred_actions,
)
from invincat_cli.i18n import t
from invincat_cli.widgets.messages import AppMessage, ErrorMessage

logger = logging.getLogger(__name__)

SCHEDULED_TRANSIENT_RETRY_DELAY_SECONDS = 3.0


async def handle_agent_task_exception(app: Any, exc: BaseException) -> bool:  # noqa: ANN401
    """Handle a failed agent turn and return whether it should retry."""
    decision = resolve_agent_task_exception_decision(
        active_scheduled_run=app._active_scheduled_run,
        retry_used=app._scheduled_turn_retry_used,
        exc=exc,
    )
    if decision.retry:
        app._scheduled_turn_retry_used = True
        logger.warning(
            "Scheduled run transient agent error; retrying once after %.1fs",
            SCHEDULED_TRANSIENT_RETRY_DELAY_SECONDS,
            exc_info=True,
        )
        with suppress(Exception):
            if decision.retry_notice is not None:
                await app._mount_message(AppMessage(decision.retry_notice))
    else:
        app._scheduled_turn_status = decision.scheduled_turn_status or "failed"
        app._scheduled_turn_error = decision.scheduled_turn_error

    logger.exception("Agent execution failed")
    error_detail = agent_error_detail_with_server_log(app, exc)
    if app._ui_adapter:
        app._ui_adapter.finalize_pending_tools_with_error(
            t("agent.error").format(error=error_detail)
        )
    if not decision.retry:
        try:
            await app._mount_message(
                ErrorMessage(t("agent.error").format(error=error_detail))
            )
        except Exception:
            logger.debug(
                "Could not mount error message (app closing?)",
                exc_info=True,
            )
    return decision.retry


def agent_error_detail_with_server_log(app: Any, exc: BaseException) -> str:  # noqa: ANN401
    """Build agent error detail, including server log tail when useful."""
    server_log_tail: str | None = None
    if app._server_proc is not None:
        try:
            server_log_tail = app._server_proc.read_log_tail(max_chars=4000)
        except Exception:
            logger.debug("Failed to read server log tail", exc_info=True)
    return build_agent_error_detail(exc, server_log_tail=server_log_tail)


async def cleanup_agent_task(app: Any, *, generation: int = 0) -> None:  # noqa: ANN401
    """Clean up after agent task completes or is cancelled."""
    cleanup_state = resolve_agent_cleanup_start_state(
        generation=generation,
        current_generation=app._agent_generation,
    )
    if cleanup_state.should_reset_running_state:
        app._agent_running = False
        app._agent_worker = None
        app._active_turn_is_planner = False

    await app._set_spinner(None)

    if cleanup_state.should_restore_input and app._chat_input:
        app._chat_input.set_cursor_active(active=True)

    if cleanup_state.should_restore_tokens:
        app._show_tokens(approximate=app._tokens_approximate)

    if cleanup_state.should_skip_post_cleanup:
        app._handle_stale_agent_cleanup(generation=generation)
        return

    try:
        await app._maybe_drain_deferred()
    except Exception:
        logger.exception("Failed to drain deferred actions during agent cleanup")
        with suppress(Exception):
            await app._mount_message(
                ErrorMessage(
                    "A deferred action failed after task completion. "
                    "You may need to retry the operation."
                )
            )

    if not should_continue_after_deferred_actions(
        agent_running=app._agent_running,
        shell_running=app._shell_running,
    ):
        return

    await app._run_post_agent_cleanup_side_effects()


def handle_stale_agent_cleanup(app: Any, *, generation: int) -> None:  # noqa: ANN401
    """Handle cleanup for an older worker generation."""
    app._finish_active_scheduled_run_as_failed("Interrupted by user")
    logger.debug(
        "Skipping stale cleanup for generation %d (current: %d)",
        generation,
        app._agent_generation,
    )


async def run_post_agent_cleanup_side_effects(app: Any) -> None:  # noqa: ANN401
    """Run cleanup side effects after deferred actions have settled."""
    try:
        await app._maybe_auto_offload()
    except Exception:
        logger.exception("Auto-offload failed during agent cleanup")

    await app._maybe_notify_memory_update()
    await app._complete_active_scheduled_run()
    await app._drain_scheduler_if_idle()
    await app._process_next_from_queue()
