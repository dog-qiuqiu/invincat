"""WeCom daemon scheduler and scheduled-delivery helpers."""

from __future__ import annotations

import asyncio
import datetime
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

def scheduled_task_wecom_chatid(task: Any) -> str:
    from invincat_cli.scheduler.delivery import scheduled_task_wecom_chatid

    return scheduled_task_wecom_chatid(task)


def task_visible_to_wecom_daemon(task: Any, cwd: Path) -> bool:
    from invincat_cli.wecom import daemon as daemon_mod

    return getattr(task, "cwd", None) == str(cwd) and bool(
        daemon_mod._scheduled_task_wecom_chatid(task)
    )


async def run_scheduler(
    config: Any,
    handler: Any,
    bridge_holder: list[Any],
    stop_event: asyncio.Event,
    runner_holder: list[Any],
) -> None:
    """Background task: tick the scheduler every 60 s and deliver results via WeCom."""
    from invincat_cli.scheduler.runner import SchedulerRunner
    from invincat_cli.scheduler.store import SchedulerStore
    from invincat_cli.scheduler.tool import SCHEDULE_CONTEXT_FLAG
    from invincat_cli.wecom import daemon as daemon_mod

    class _WeComFilteredStore(SchedulerStore):
        """Only surface WeCom-deliverable tasks for this daemon's project."""

        def list_tasks(self, *, enabled_only: bool = False, cwd: str | None = None):
            return [
                t
                for t in super().list_tasks(enabled_only=enabled_only, cwd=cwd)
                if daemon_mod._task_visible_to_wecom_daemon(t, config.cwd)
            ]

        def try_start_run(self, task_id: str, run: Any, **kwargs: Any) -> bool:
            task = super().load_task(task_id)
            if task is None or not daemon_mod._task_visible_to_wecom_daemon(task, config.cwd):
                return False
            return super().try_start_run(task_id, run, **kwargs)

    store = _WeComFilteredStore()

    # Reconcile stale 'running' runs left behind by dead scheduler processes.
    # Live rows owned by another TUI/daemon process are preserved so starting
    # the daemon cannot falsely fail an in-flight TUI scheduled task.
    try:
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()
        reconciled = store.reconcile_orphan_runs(
            str(config.cwd),
            finished_at=now_iso,
            status="failed",
            error="daemon restart (previous run never finished)",
        )
        if reconciled:
            logger.warning(
                "Scheduler reconciled %d orphan 'running' run(s) from a previous daemon",
                reconciled,
            )
    except Exception:
        logger.exception("reconcile_orphan_runs failed at scheduler startup")

    injection_tasks: dict[str, asyncio.Task[None]] = {}

    async def _run_injected_message(task_id: str, run_id: str, prompt: str) -> None:
        # The whole body runs under a try/finally so that *any* unexpected
        # error (DB failure, delivery exception, programming bug) still calls
        # finish_run().  Without this, SchedulerRunner._running_task_ids would
        # keep the task slot held until daemon restart and the task would
        # never fire again.
        status = "success"
        error_msg: str | None = None

        try:
            task = store.load_task(task_id)
            if task is None:
                # Task was deleted between evaluation and injection.
                status = "failed"
                error_msg = "task not found"
                return

            # Resolve WeCom delivery chatid from task's delivery spec.
            chatid = daemon_mod._scheduled_task_wecom_chatid(task)

            if not chatid:
                logger.warning(
                    "Scheduled task %r is not WeCom-deliverable; "
                    "messages will not be delivered to WeCom",
                    task_id,
                )

            # Send start notification if we have a WeCom target.  Use the same
            # robust-delivery helper as the final result so the start notice can
            # survive a transient reconnect during long agent turns.
            if chatid and bridge_holder:
                try:
                    await daemon_mod._deliver_scheduled_text(
                        bridge_holder[0],
                        chatid,
                        f"⏳ 定时任务开始执行：{task.title}",
                        label="start-notice",
                        task_title=task.title,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("Start-notice delivery failed", exc_info=True)

            # Use a dedicated thread per task (not the user's chat thread) so scheduled
            # runs don't pollute the user's conversation history.  We also embed the
            # real WeCom chatid under a sentinel key so file-send tools triggered by
            # the agent can reach the user instead of the synthetic chatid.
            synthetic_frame: dict[str, Any] = {
                "body": {
                    "chatid": f"__scheduled_{task_id}",
                },
            }
            if chatid:
                synthetic_frame["body"]["_wecom_target_chatid"] = chatid

            result = ""
            try:
                result = await handler.run_turn(
                    prompt,
                    synthetic_frame,
                    daemon_mod._noop_on_content,
                    runtime_context={SCHEDULE_CONTEXT_FLAG: True},
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Scheduled task %r agent turn failed", task_id)
                status = "failed"
                error_msg = str(exc)

            # Push final result to WeCom, retrying transient connection issues but
            # bailing out fast on server-side rejections (bad chatid, permission, ...).
            if chatid and bridge_holder:
                if status == "success":
                    content = f"✅ 定时任务已完成：{task.title}"
                    if result:
                        # Mirror TUI's 1200-char truncation for scheduled results.
                        summary = (
                            result
                            if len(result) <= 1200
                            else result[:1200].rstrip() + "\n\n(摘要过长，已截断)"
                        )
                        content += f"\n\n{summary}"
                else:
                    content = f"❌ 定时任务执行失败：{task.title}"
                    if error_msg:
                        content += f"\n\n{error_msg}"
                try:
                    delivered = await daemon_mod._deliver_scheduled_text(
                        bridge_holder[0],
                        chatid,
                        content,
                        label="final-result",
                        task_title=task.title,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Final-result delivery raised unexpectedly")
                    delivered = False
                    if status == "success":
                        status = "failed"
                        error_msg = f"delivery error: {exc}"
                if not delivered and status == "success":
                    # The agent succeeded but we couldn't notify — record this as
                    # a delivery failure so users (and the run-history UI) see it.
                    error_msg = "WeCom delivery failed after retries"
                    status = "failed"

        except asyncio.CancelledError:
            # Cancellation is the daemon shutting down — record so the run
            # doesn't sit in 'running' state forever.
            status = "failed"
            error_msg = "cancelled (daemon shutdown)"
            raise
        except Exception as exc:  # noqa: BLE001 — last-ditch safety net
            logger.exception("Unexpected error in scheduled task injection")
            status = "failed"
            error_msg = f"injection error: {exc}"
        finally:
            if runner_holder:
                try:
                    runner_holder[0].finish_run(
                        run_id,
                        task_id,
                        status=status,
                        error=error_msg,
                    )
                except Exception:
                    logger.exception("finish_run failed for run_id=%s", run_id)

    async def _inject_message(task_id: str, run_id: str, prompt: str) -> None:
        task = asyncio.create_task(_run_injected_message(task_id, run_id, prompt))
        injection_tasks[run_id] = task

        def _done(done: asyncio.Task[None]) -> None:
            injection_tasks.pop(run_id, None)
            if done.cancelled():
                return
            try:
                done.result()
            except Exception:
                logger.exception(
                    "Scheduled task injection task failed run_id=%s", run_id
                )

        task.add_done_callback(_done)

    async def _cancel_timed_out_run(run_id: str, task_id: str) -> None:
        task = injection_tasks.get(run_id)
        if task is None or task.done():
            return
        logger.warning(
            "Cancelling scheduled task %r run_id=%s after timeout",
            task_id,
            run_id,
        )
        task.cancel()
        try:
            await daemon_mod._deliver_scheduled_timeout_result(
                store,
                bridge_holder,
                task_id=task_id,
            )
        except Exception:
            logger.warning("Timeout-result delivery failed", exc_info=True)

    runner = SchedulerRunner(
        store,
        inject_message=_inject_message,
        notify=lambda msg: logger.info("Scheduler: %s", msg),
        is_busy=lambda: False,
        on_timeout=_cancel_timed_out_run,
        cwd=str(config.cwd),
        runner_kind="wecom-daemon",
    )
    runner_holder.append(runner)
    logger.info("WeCom daemon scheduler started (cwd=%s)", config.cwd)

    # Wait for the bridge to finish the WeCom subscribe handshake before the first
    # tick.  Without this, scheduled messages queued immediately after startup would
    # be sent before WeCom acknowledges the subscription and could be silently dropped.
    _BRIDGE_READY_TIMEOUT = 120
    try:
        try:
            if bridge_holder:
                try:
                    await asyncio.wait_for(
                        bridge_holder[0].ready.wait(), timeout=_BRIDGE_READY_TIMEOUT
                    )
                    logger.info("Scheduler: WeCom bridge ready, starting first tick")
                except TimeoutError:
                    logger.warning(
                        "Scheduler: WeCom bridge not ready after %ds, proceeding anyway",
                        _BRIDGE_READY_TIMEOUT,
                    )
            else:
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            return

        # Tick loop: initial tick for misfire recovery, then every 60 s.
        while not stop_event.is_set():
            try:
                await runner.tick()
                await asyncio.wait_for(asyncio.shield(stop_event.wait()), timeout=60)
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Scheduler tick error")
    finally:
        for task in list(injection_tasks.values()):
            task.cancel()
        if injection_tasks:
            await asyncio.gather(*injection_tasks.values(), return_exceptions=True)
        injection_tasks.clear()

    logger.info("WeCom daemon scheduler stopped")


async def deliver_scheduled_timeout_result(
    store: Any,
    bridge_holder: list[Any],
    *,
    task_id: str,
) -> bool:
    """Best-effort WeCom timeout notification for a scheduled daemon run."""
    from invincat_cli.wecom import daemon as daemon_mod
    scheduled_task = store.load_task(task_id)
    if scheduled_task is None or not bridge_holder:
        return False

    chatid = daemon_mod._scheduled_task_wecom_chatid(scheduled_task)
    if not chatid:
        return False

    return await daemon_mod._deliver_scheduled_text(
        bridge_holder[0],
        chatid,
        f"⏱️ 定时任务执行超时：{scheduled_task.title}",
        label="timeout-result",
        task_title=scheduled_task.title,
    )


async def deliver_scheduled_text(
    bridge: Any,
    chatid: str,
    content: str,
    *,
    label: str,
    task_title: str,
) -> bool:
    """Deliver a scheduled-task notification via WeCom active push, with retries.

    Uses :py:meth:`WeComBridge.send_request` so the server's response is awaited
    and any non-zero ``errcode`` surfaces as :class:`WeComServerError`.
    Distinguishes:

    - **Transient failures** (offline socket, timeout, generic transport): retry
      with backoff up to ``daemon_mod._DELIVERY_RETRIES`` times, waiting for ``bridge.ready``
      between attempts so a reconnect-in-progress doesn't burn retry budget.
    - **Server rejections** (``errcode != 0``, e.g. invalid chatid / no
      permission / msgtype unsupported): log loudly and bail without retry —
      retrying won't change the outcome.

    Returns True if WeCom acknowledged the message.
    """
    from invincat_cli.wecom import daemon as daemon_mod
    from invincat_cli.wecom.bridge import WeComOfflineError, WeComServerError
    from invincat_cli.wecom.protocol import build_wecom_text_frame

    payload = build_wecom_text_frame(chatid, content)

    for attempt in range(1, daemon_mod._DELIVERY_RETRIES + 1):
        # Wait for the bridge subscribe handshake to complete before sending.
        # During a reconnect this can take several seconds; the per-attempt
        # ready-timeout caps it without consuming the whole retry budget.
        try:
            await asyncio.wait_for(bridge.ready.wait(), timeout=daemon_mod._DELIVERY_READY_TIMEOUT)
        except TimeoutError:
            logger.warning(
                "wecom scheduled delivery (%s) bridge not ready after %ds (attempt %d/%d, task=%r)",
                label,
                daemon_mod._DELIVERY_READY_TIMEOUT,
                attempt,
                daemon_mod._DELIVERY_RETRIES,
                task_title,
            )
            if attempt < daemon_mod._DELIVERY_RETRIES:
                await asyncio.sleep(daemon_mod._DELIVERY_RETRY_DELAY)
            continue

        try:
            await bridge.send_request(payload, timeout=daemon_mod._DELIVERY_REQUEST_TIMEOUT)
            logger.info(
                "wecom scheduled delivery (%s) succeeded chatid=%s task=%r attempt=%d",
                label,
                chatid,
                task_title,
                attempt,
            )
            return True
        except WeComServerError as exc:
            # Server-side rejection: retrying won't help (chatid invalid, bot
            # not authorised for this chat, msgtype unsupported, ...).
            logger.error(
                "wecom scheduled delivery (%s) rejected by server: errcode=%s errmsg=%s "
                "chatid=%s task=%r — not retrying",
                label,
                exc.errcode,
                exc.errmsg,
                chatid,
                task_title,
            )
            return False
        except WeComOfflineError:
            logger.warning(
                "wecom scheduled delivery (%s) offline (attempt %d/%d, chatid=%s task=%r)",
                label,
                attempt,
                daemon_mod._DELIVERY_RETRIES,
                chatid,
                task_title,
            )
        except TimeoutError:
            logger.warning(
                "wecom scheduled delivery (%s) timed out (attempt %d/%d, chatid=%s task=%r)",
                label,
                attempt,
                daemon_mod._DELIVERY_RETRIES,
                chatid,
                task_title,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "wecom scheduled delivery (%s) transient error (attempt %d/%d, chatid=%s task=%r): %s",
                label,
                attempt,
                daemon_mod._DELIVERY_RETRIES,
                chatid,
                task_title,
                exc,
            )
        if attempt < daemon_mod._DELIVERY_RETRIES:
            await asyncio.sleep(daemon_mod._DELIVERY_RETRY_DELAY)

    logger.error(
        "wecom scheduled delivery (%s) permanently failed after %d attempts chatid=%s task=%r",
        label,
        daemon_mod._DELIVERY_RETRIES,
        chatid,
        task_title,
    )
    return False


async def noop_on_content(_content: str) -> None:
    """No-op on_content for scheduled tasks — active push only, no streaming."""
