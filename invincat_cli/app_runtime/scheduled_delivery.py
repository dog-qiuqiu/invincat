"""Scheduled run integration helpers for the Textual app."""

from __future__ import annotations

import logging
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from invincat_cli.app_runtime.scheduler import (
    active_scheduled_task_id,
    remove_scheduled_messages,
    resolve_scheduled_wecom_file_path,
    scheduled_run_matches,
    should_deliver_scheduled_result,
    wecom_daemon_claims_scheduled_task,
)
from invincat_cli.app_runtime.state import QueuedMessage
from invincat_cli.app_runtime.wecom import (
    wecom_bridge_is_online,
    wecom_bridge_offline_message,
)
from invincat_cli.widgets.messages import AppMessage, ErrorMessage

logger = logging.getLogger(__name__)


def start_scheduler(app: Any) -> None:  # noqa: ANN401
    """Create SchedulerRunner and start the tick interval."""
    from invincat_cli.scheduler.runner import SchedulerRunner
    from invincat_cli.scheduler.store import FilteredSchedulerStore

    runner_store = FilteredSchedulerStore(
        db_path=getattr(app._scheduler_store, "_db_path", None),
        exclude_task=lambda task: wecom_daemon_claims_scheduled_task(
            task, app._cwd
        ),
    )

    app._scheduler_runner = SchedulerRunner(
        runner_store,
        inject_message=lambda task_id, run_id, prompt: inject_scheduled_message(
            app,
            task_id,
            run_id,
            prompt,
        ),
        notify=lambda msg: app.notify(msg, timeout=6),
        is_busy=lambda: app._agent_running or app._shell_running,
        on_timeout=app._handle_scheduled_timeout,
        cwd=app._cwd,
    )
    app._scheduler_interval_handle = app.set_interval(
        60, app._scheduler_tick, pause=False
    )
    # Fire once immediately after startup for misfire recovery.
    app.set_timer(3, app._scheduler_tick)


async def scheduler_tick(app: Any) -> None:  # noqa: ANN401
    if app._scheduler_runner is not None:
        await app._scheduler_runner.tick()


async def handle_scheduled_timeout(
    app: Any,  # noqa: ANN401
    run_id: str,
    task_id: str,
) -> None:
    cancel_timed_out_scheduled_turn(app, run_id, task_id)
    await deliver_scheduled_result_to_wecom(
        app,
        task_id=task_id,
        run_id=run_id,
        status="timeout",
        error="Scheduled task timed out",
    )


async def complete_active_scheduled_run(
    app: Any,  # noqa: ANN401
    *,
    deliver_result: Any | None = None,  # noqa: ANN401
) -> None:
    """Record completion and WeCom delivery for the active scheduled run."""
    if app._active_scheduled_run is None:
        return

    run_id, task_id = app._active_scheduled_run
    app._active_scheduled_run = None
    try:
        if app._scheduler_runner is not None:
            await deliver_active_scheduled_result_if_needed(
                app,
                run_id=run_id,
                task_id=task_id,
                deliver_result=deliver_result or deliver_scheduled_result_to_wecom,
            )
            finish_scheduled_run(app, run_id=run_id, task_id=task_id)
    finally:
        reset_scheduled_turn_state(app)


async def deliver_active_scheduled_result_if_needed(
    app: Any,  # noqa: ANN401
    *,
    run_id: str,
    task_id: str,
    deliver_result: Any,  # noqa: ANN401
) -> None:
    """Deliver scheduled result to WeCom unless the run already finished."""
    run = app._scheduler_store.load_run(run_id)
    if not should_deliver_scheduled_result(run):
        return
    try:
        await deliver_result(
            app,
            task_id=task_id,
            run_id=run_id,
            status=app._scheduled_turn_status,
            error=app._scheduled_turn_error,
        )
    except Exception:
        logger.exception("Failed to deliver scheduled run %r to WeCom", run_id)


def finish_scheduled_run(app: Any, *, run_id: str, task_id: str) -> None:  # noqa: ANN401
    """Mark a scheduled run as finished in the scheduler runner."""
    try:
        app._scheduler_runner.finish_run(
            run_id,
            task_id,
            status=app._scheduled_turn_status,
            error=app._scheduled_turn_error,
        )
    except Exception:
        logger.exception("Failed to finish scheduled run %r", run_id)


def reset_scheduled_turn_state(app: Any) -> None:  # noqa: ANN401
    """Reset per-turn scheduled-run result bookkeeping."""
    app._scheduled_turn_error = None
    app._scheduled_turn_retry_used = False


def cancel_timed_out_scheduled_turn(
    app: Any,  # noqa: ANN401
    run_id: str,
    task_id: str,
) -> None:
    """Cancel or dequeue a scheduled turn after SchedulerRunner timeout."""
    app._pending_messages = remove_scheduled_messages(
        app._pending_messages,
        run_id=run_id,
        task_id=task_id,
    )
    if not scheduled_run_matches(
        app._active_scheduled_run,
        run_id=run_id,
        task_id=task_id,
    ):
        return

    if app._pending_approval_widget is not None:
        with suppress(Exception):
            app._pending_approval_widget.action_select_reject()
    if app._pending_ask_user_widget is not None:
        with suppress(Exception):
            app._pending_ask_user_widget.action_cancel()
    if app._shell_worker is not None:
        app._shell_worker.cancel()
    if app._agent_worker is not None:
        app._agent_worker.cancel()
    app._shell_running = False
    app._shell_worker = None
    app._agent_running = False
    app._agent_worker = None
    app._active_turn_is_planner = False
    app._active_scheduled_run = None
    app._scheduled_turn_status = "timeout"
    app._scheduled_turn_error = "Scheduled task timed out"
    logger.warning(
        "scheduled run timed out; cancelled active worker run_id=%s task_id=%s",
        run_id,
        task_id,
    )


async def deliver_scheduled_result_to_wecom(
    app: Any,  # noqa: ANN401
    *,
    task_id: str,
    run_id: str,
    status: str,
    error: str | None,
) -> None:
    """Best-effort active WeCom delivery for a completed scheduled run."""
    from invincat_cli.scheduler.wecom_delivery import (
        build_scheduled_wecom_text,
        latest_assistant_summary,
        scheduled_report_path_for_wecom,
        scheduled_wecom_delivery_target,
        should_send_scheduled_report_file,
    )

    task = app._scheduler_store.load_task(task_id)
    run = app._scheduler_store.load_run(run_id)
    if task is None or run is None:
        return

    has_wecom_channel, chatid = scheduled_wecom_delivery_target(task)
    if not has_wecom_channel:
        app._scheduler_store.update_run_delivery(
            run_id,
            status="none",
            error=None,
            attempts_delta=0,
        )
        return
    if chatid is None:
        app._scheduler_store.update_run_delivery(
            run_id,
            status="failed",
            error="missing chatid",
        )
        await app._mount_message(
            ErrorMessage("Scheduled task WeCom delivery skipped: missing chatid.")
        )
        return

    report_path = scheduled_report_path_for_wecom(task, run)

    all_messages = app._message_store.get_all_messages()
    run_messages = all_messages[app._scheduled_run_message_offset:]
    content = build_scheduled_wecom_text(
        title=task.title,
        status=status,
        summary=latest_assistant_summary(run_messages),
        report_path=report_path,
        error=error,
    )

    try:
        text_sent = await send_scheduled_wecom_text(
            app,
            chatid=chatid,
            content=content,
            run_id=run_id,
        )
        if not text_sent:
            return
        if should_send_scheduled_report_file(
            status=status,
            report_path=report_path,
        ):
            await send_scheduled_wecom_report_file(
                app,
                chatid=chatid,
                report_path=report_path,
            )
    except Exception as exc:
        app._scheduler_store.update_run_delivery(
            run_id,
            status="failed",
            error=str(exc),
        )
        logger.warning("Scheduled task WeCom delivery failed: %s", exc, exc_info=True)
        await app._mount_message(
            ErrorMessage(f"Scheduled task WeCom delivery failed: {exc}")
        )


async def send_scheduled_wecom_text(
    app: Any,  # noqa: ANN401
    *,
    chatid: str,
    content: str,
    run_id: str,
) -> bool:
    """Send scheduled WeCom text and update delivery status."""
    from invincat_cli.wecom.protocol import build_wecom_text_frame

    if not wecom_bridge_is_online(app._wecom_bridge):
        app._scheduler_store.update_run_delivery(
            run_id,
            status="failed",
            error=wecom_bridge_offline_message(),
        )
        await app._mount_message(
            ErrorMessage(
                "Scheduled task WeCom delivery failed: WeCom bridge is offline."
            )
        )
        return False
    app._wecom_enqueue(build_wecom_text_frame(chatid, content))
    flushed = await app._wecom_flush_outbox()
    if not flushed:
        app._scheduler_store.update_run_delivery(
            run_id,
            status="queued",
            error="waiting for bridge reconnect",
        )
        await app._mount_message(
            AppMessage(
                "Scheduled task WeCom delivery queued; waiting for bridge reconnect."
            )
        )
        return False

    app._scheduler_store.update_run_delivery(
        run_id,
        status="success",
        error=None,
        delivered_at=datetime.now(timezone.utc).isoformat(),
    )
    return True


async def send_scheduled_wecom_report_file(
    app: Any,  # noqa: ANN401
    *,
    chatid: str,
    report_path: str | None,
) -> None:
    """Send the scheduled report file to WeCom when available."""
    from invincat_cli.wecom.media import upload_wecom_outbound_media
    from invincat_cli.wecom.protocol import build_wecom_file_frame_for_chat

    if report_path is None:
        return
    if not wecom_bridge_is_online(app._wecom_bridge):
        await app._mount_message(
            ErrorMessage(
                "Scheduled task WeCom file delivery skipped: WeCom bridge is offline."
            )
        )
        return
    media_id = await upload_wecom_outbound_media(
        Path(report_path),
        send_request=app._wecom_send_request,
    )
    await app._wecom_send_request(build_wecom_file_frame_for_chat(chatid, media_id))


def active_scheduled_wecom_chat_id(app: Any) -> str | None:  # noqa: ANN401
    """Return the WeCom chat id for the active scheduled run, if any."""
    task_id = active_scheduled_task_id(app._active_scheduled_run)
    if task_id is None:
        return None
    task = app._scheduler_store.load_task(task_id)
    if task is None:
        return None
    from invincat_cli.scheduler.wecom_delivery import scheduled_wecom_chat_id

    return scheduled_wecom_chat_id(task)


async def send_scheduled_wecom_file_request(
    app: Any,  # noqa: ANN401
    payload: dict[str, Any],
) -> None:
    """Send a file requested by send_wecom_file during a scheduled WeCom run."""
    from invincat_cli.wecom.media import upload_wecom_outbound_media
    from invincat_cli.wecom.protocol import build_wecom_file_frame_for_chat

    chatid = active_scheduled_wecom_chat_id(app)
    if not chatid:
        raise RuntimeError("Scheduled task has no WeCom delivery target")
    if not wecom_bridge_is_online(app._wecom_bridge):
        raise RuntimeError(wecom_bridge_offline_message())

    path = resolve_scheduled_wecom_file_path(
        payload.get("path"),
        cwd=app._cwd,
    )

    media_id = await upload_wecom_outbound_media(
        path,
        send_request=app._wecom_send_request,
    )
    await app._wecom_send_request(build_wecom_file_frame_for_chat(chatid, media_id))


async def inject_scheduled_message(
    app: Any,  # noqa: ANN401
    task_id: str,
    run_id: str,
    prompt: str,
) -> None:
    """Inject a scheduled task prompt into the TUI message queue."""
    from invincat_cli.i18n import t

    task = app._scheduler_store.load_task(task_id)
    title = task.title if task else task_id
    await app._mount_message(AppMessage(t("schedule.running").format(title=title)))
    app._pending_messages.append(
        QueuedMessage(
            text=prompt,
            mode="normal",
            scheduled_run_id=run_id,
            scheduled_task_id=task_id,
        )
    )
    if not (app._agent_running or app._shell_running):
        await app._process_next_from_queue()
