"""Main async runtime for the WeCom daemon."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Any

from invincat_cli.wecom.daemon_config import WeComDaemonConfig
from invincat_cli.wecom.daemon_constants import _FILE_PERMS

logger = logging.getLogger(__name__)

async def _daemon_main(
    config: WeComDaemonConfig, *, startup_fd: int | None = None
) -> None:
    from invincat_cli.wecom import daemon as daemon_mod

    """Async entry point that runs inside the daemon process."""
    logger.info("WeCom daemon starting cwd=%s bot_id=%s", config.cwd, config.bot_id)

    stop_event = asyncio.Event()
    startup_reported = False

    def _report_startup(status: str) -> None:
        nonlocal startup_fd, startup_reported
        if startup_reported:
            return
        daemon_mod._write_startup_status(startup_fd, status)
        startup_fd = None
        startup_reported = True

    def _handle_signal(*_: object) -> None:
        logger.info("WeCom daemon received stop signal")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    server_proc = None
    bridge_task: asyncio.Task[None] | None = None
    socket_server: asyncio.Server | None = None
    scheduler_task: asyncio.Task[None] | None = None

    try:
        # --- Start LangGraph server ---
        logger.info("Starting LangGraph agent server...")
        from invincat_cli.config import SHELL_ALLOW_ALL, settings
        from invincat_cli.server.manager import start_server_and_get_agent

        os.chdir(config.cwd)
        shell_allow_list = settings.shell_allow_list
        shell_enabled = bool(shell_allow_list)
        shell_is_unrestricted = shell_enabled and isinstance(
            shell_allow_list, type(SHELL_ALLOW_ALL)
        )
        restrictive_shell_allow_list = (
            list(shell_allow_list)
            if shell_enabled and not shell_is_unrestricted
            else None
        )
        if not shell_enabled:
            logger.info(
                "Daemon shell tool disabled; set a shell allow-list to enable it."
            )
        elif restrictive_shell_allow_list is not None:
            logger.info("Daemon shell tool enabled with restrictive allow-list.")
        else:
            logger.warning("Daemon shell tool enabled with unrestricted allow-list.")
        agent, server_proc, _ = await start_server_and_get_agent(
            assistant_id="agent",
            auto_approve=not bool(restrictive_shell_allow_list),
            interrupt_shell_only=bool(restrictive_shell_allow_list),
            shell_allow_list=restrictive_shell_allow_list,
            enable_shell=shell_enabled,
            enable_ask_user=False,
            interactive=False,
            scheduler_cwd_scope=str(config.cwd),
        )
        logger.info("LangGraph agent server ready")

        # --- Headless handler ---
        from invincat_cli.wecom.bridge import WeComBridge
        from invincat_cli.wecom.headless import HeadlessWeComHandler
        from invincat_cli.wecom.session import WeComMessageResponder

        bridge_holder: list[WeComBridge] = []  # populated after bridge is created
        scheduler_runner_holder: list[Any] = []  # populated by _run_scheduler

        async def _fire_task_now(task: Any) -> None:
            if not daemon_mod._task_visible_to_wecom_daemon(task, config.cwd):
                logger.warning(
                    "Ignoring WeCom run-now request for non-WeCom-deliverable task %r",
                    getattr(task, "id", None),
                )
                return
            if scheduler_runner_holder:
                await scheduler_runner_holder[0].fire_now(task)

        handler = HeadlessWeComHandler(
            agent=agent,
            cwd=config.cwd,
            send_request=lambda payload: daemon_mod._bridge_send_request(bridge_holder, payload),
            on_schedule_run_now=_fire_task_now,
        )

        # --- WeCom bridge ---
        async def _on_status(msg: str) -> None:
            logger.info("WeCom bridge: %s", msg)

        async def _on_error(msg: str) -> None:
            logger.error("WeCom bridge error: %s", msg)

        async def _flush_outbox() -> bool:
            if not bridge_holder:
                return False
            return await bridge_holder[0].flush_outbox()

        async def _report_error(msg: str) -> None:
            logger.error("WeCom turn error: %s", msg)

        async def _on_message(frame: dict[str, Any]) -> None:
            responder = WeComMessageResponder(
                enqueue=lambda p: (
                    bridge_holder[0].enqueue(p) if bridge_holder else None
                ),
                flush=_flush_outbox,
                build_agent_input=daemon_mod._make_build_agent_input(config.cwd),
                run_turn=handler.run_turn,
                report_error=_report_error,
            )
            await responder.handle(frame)

        bridge = WeComBridge(
            on_status=_on_status,
            on_error=_on_error,
            on_message=_on_message,
            should_exit=stop_event.is_set,
        )
        bridge_holder.append(bridge)

        # Start the WeCom bridge before reporting startup success.  A daemon
        # that has a socket and state file but never subscribed is not useful.
        bridge_task = asyncio.create_task(
            bridge.run(bot_id=config.bot_id, secret=config.secret, ws_url=config.ws_url)
        )

        # --- Unix socket IPC server ---
        # Bind socket FIRST, then write state file.  The previous order let
        # external callers see a "running" state file with a socket that
        # hadn't started listening yet, so stop/status calls could not reach
        # the half-started daemon.
        config.socket_path.parent.mkdir(parents=True, exist_ok=True)
        # Remove stale socket file from a previous crash before binding.
        config.socket_path.unlink(missing_ok=True)
        socket_server = await asyncio.start_unix_server(
            lambda r, w: daemon_mod._handle_socket_client(r, w, bridge, handler, stop_event),
            path=str(config.socket_path),
        )
        # Tighten perms so other local users can't connect and issue "stop".
        try:
            os.chmod(str(config.socket_path), _FILE_PERMS)
        except OSError as exc:
            logger.warning("Could not chmod daemon socket: %s", exc)
        logger.info("IPC socket listening at %s", config.socket_path)

        daemon_mod._write_daemon_state(config)
        logger.info("Daemon state written to %s", config.state_file)

        await daemon_mod._wait_for_bridge_startup(bridge, bridge_task)
        _report_startup("READY")

        # --- Scheduler ---
        scheduler_task = asyncio.create_task(
            daemon_mod._run_scheduler(
                config, handler, bridge_holder, stop_event, scheduler_runner_holder
            )
        )

        logger.info("WeCom daemon ready")

        # Wait until stop_event is set (by signal or socket stop command)
        await stop_event.wait()
        logger.info("WeCom daemon stopping...")

    except Exception as exc:
        _report_startup(f"ERROR {type(exc).__name__}: {exc}")
        logger.exception("WeCom daemon fatal error")
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except (asyncio.CancelledError, Exception):
                pass
        if bridge_task is not None:
            if bridge_holder:
                bridge_holder[0].stop()
            bridge_task.cancel()
            try:
                await bridge_task
            except (asyncio.CancelledError, Exception):
                pass
        if socket_server is not None:
            socket_server.close()
            await socket_server.wait_closed()
        if server_proc is not None:
            server_proc.stop()
        daemon_mod._remove_daemon_state(config)
        if not startup_reported:
            _report_startup("ERROR daemon stopped before startup completed")
        logger.info("WeCom daemon stopped")

async def _wait_for_bridge_startup(
    bridge: Any, bridge_task: asyncio.Task[None]
) -> None:
    from invincat_cli.wecom import daemon as daemon_mod

    """Wait until the WeCom subscribe ACK is observed, or fail startup."""
    ready_wait = asyncio.create_task(bridge.ready.wait())
    try:
        done, pending = await asyncio.wait(
            {ready_wait, bridge_task},
            timeout=daemon_mod._BRIDGE_STARTUP_READY_TIMEOUT,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if ready_wait in done and ready_wait.result():
            return
        if bridge_task in done:
            if bridge_task.cancelled():
                raise RuntimeError(
                    "WeCom bridge was cancelled before subscription acknowledgement"
                )
            exc = bridge_task.exception()
            if exc is not None:
                raise RuntimeError(
                    f"WeCom bridge failed before subscription acknowledgement: {exc}"
                ) from exc
            raise RuntimeError(
                "WeCom bridge stopped before subscription acknowledgement; "
                "check WECOM_BOT_ID / WECOM_BOT_SECRET / WECOM_WS_URL and the daemon log."
            )
        for task in pending:
            if task is not bridge_task:
                task.cancel()
        raise RuntimeError(
            "WeCom bridge did not receive subscription acknowledgement within "
            f"{daemon_mod._BRIDGE_STARTUP_READY_TIMEOUT:.0f}s; check the daemon log."
        )
    finally:
        if not ready_wait.done():
            ready_wait.cancel()
            try:
                await ready_wait
            except asyncio.CancelledError:
                pass

async def _bridge_send_request(
    bridge_holder: list[Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not bridge_holder:
        raise RuntimeError("WeCom bridge not yet initialised")
    return await bridge_holder[0].send_request(payload)

def _make_build_agent_input(cwd: Path):
    async def _build(frame: dict[str, Any]) -> str:
        from invincat_cli.wecom.media import (
            build_wecom_agent_input_with_media_downloads,
        )

        return await build_wecom_agent_input_with_media_downloads(frame, cwd=cwd)

    return _build
