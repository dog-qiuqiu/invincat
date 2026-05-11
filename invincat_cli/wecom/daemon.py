"""WeCom background daemon lifecycle management.

Provides start/stop/status for a per-project daemon process that keeps the
WeCom bridge alive independently of the Textual UI.

State file:  {cwd}/.invincat/wecom_daemon.json
Log file:    {cwd}/.invincat/wecom_daemon.log
Lock file:   {cwd}/.invincat/wecom_daemon.lock  (authoritative liveness via flock)
Unix socket: {cwd}/.invincat/wecom_daemon.sock
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import errno
import fcntl
import json
import logging
import os
import resource
import select
import signal
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATE_FILENAME = ".invincat/wecom_daemon.json"
_LOG_FILENAME = ".invincat/wecom_daemon.log"
_SOCKET_FILENAME = ".invincat/wecom_daemon.sock"
_LOCK_FILENAME = ".invincat/wecom_daemon.lock"
_SOCKET_TIMEOUT = 5.0
_STARTUP_TIMEOUT = 75.0
_DELIVERY_RETRIES = 8                 # 1 initial attempt + 7 retries
_DELIVERY_RETRY_DELAY = 15            # seconds between retries
_DELIVERY_READY_TIMEOUT = 30          # per-attempt wait for bridge subscribe ACK
_DELIVERY_REQUEST_TIMEOUT = 30        # per-attempt WeCom request response timeout
_FILE_PERMS = 0o600                   # log / socket / state / lock — owner-only


# ---------------------------------------------------------------------------
# Config / State
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class WeComDaemonConfig:
    bot_id: str
    secret: str
    ws_url: str
    cwd: Path

    @property
    def state_file(self) -> Path:
        return self.cwd / _STATE_FILENAME

    @property
    def log_file(self) -> Path:
        return self.cwd / _LOG_FILENAME

    @property
    def socket_path(self) -> Path:
        return self.cwd / _SOCKET_FILENAME

    @property
    def lock_file(self) -> Path:
        return self.cwd / _LOCK_FILENAME

    @classmethod
    def from_env(cls, cwd: Path) -> "WeComDaemonConfig":
        bot_id = os.getenv("WECOM_BOT_ID", "").strip()
        secret = os.getenv("WECOM_BOT_SECRET", "").strip()
        if not bot_id or not secret:
            raise ValueError(
                "WECOM_BOT_ID and WECOM_BOT_SECRET must be set to start the WeCom daemon."
            )
        ws_url = os.getenv("WECOM_WS_URL", "wss://openws.work.weixin.qq.com").strip()
        return cls(bot_id=bot_id, secret=secret, ws_url=ws_url, cwd=cwd)


def read_daemon_state(cwd: Path) -> dict[str, Any] | None:
    state_file = cwd / _STATE_FILENAME
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_daemon_state(config: WeComDaemonConfig) -> None:
    state = {
        "pid": os.getpid(),
        "socket_path": str(config.socket_path),
        "started_at": datetime.datetime.now().isoformat(),
        "cwd": str(config.cwd),
        "bot_id": config.bot_id,
    }
    config.state_file.parent.mkdir(parents=True, exist_ok=True)
    # Open with explicit owner-only mode so other local users can't read PID/bot_id.
    fd = os.open(
        str(config.state_file),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        _FILE_PERMS,
    )
    try:
        try:
            os.fchmod(fd, _FILE_PERMS)
        except OSError:
            pass
        os.write(fd, json.dumps(state, indent=2, ensure_ascii=False).encode("utf-8"))
    finally:
        os.close(fd)


def _remove_daemon_state(config: WeComDaemonConfig) -> None:
    # Lock file is intentionally NOT removed: keeping the inode stable means a
    # racing peer that opened the same path before we deleted it still sees the
    # same lock state.  The OS releases our flock automatically on process exit.
    for path in (config.state_file, config.socket_path):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Authoritative liveness via fcntl.flock on a per-cwd lockfile.
#
# The PID-based check used previously was unsound: after the daemon died, the
# OS could reuse the recorded PID for an unrelated local process; ``os.kill(p, 0)``
# would then succeed and ``stop_daemon`` would SIGTERM that innocent process.
#
# An exclusive ``flock`` is owned by the running daemon process for its
# lifetime.  The kernel releases the lock automatically when the process exits
# (clean or crash), so probing the lock from another process gives a definitive
# "is anyone alive?" answer with no PID-reuse hazard.
# ---------------------------------------------------------------------------


def _open_lock_fd(cwd: Path) -> int:
    """Open the lockfile, creating the parent dir + file as needed."""
    lock_path = cwd / _LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, _FILE_PERMS)
    try:
        os.fchmod(fd, _FILE_PERMS)
    except OSError:
        pass
    return fd


def acquire_daemon_lock(cwd: Path) -> int:
    """Acquire the exclusive daemon lock.  Returns an fd that must be held open.

    Raises ``BlockingIOError`` if another daemon already holds the lock.
    """
    fd = _open_lock_fd(cwd)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise
    # Record our PID inside the file so external tooling can identify the owner.
    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
    except Exception:
        pass
    return fd


def is_daemon_running(cwd: Path) -> bool:
    """Return True if a daemon process holds the per-cwd lockfile."""
    lock_path = cwd / _LOCK_FILENAME
    if not lock_path.exists():
        return False
    try:
        fd = os.open(str(lock_path), os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True  # someone holds it → alive
        # We acquired the lock → no daemon running.  Release immediately.
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# IPC via Unix socket
# ---------------------------------------------------------------------------


async def _socket_rpc(socket_path: Path, request: dict[str, Any]) -> dict[str, Any]:
    """Send one JSON request to the daemon socket and return the response."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(socket_path)),
            timeout=_SOCKET_TIMEOUT,
        )
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        raise RuntimeError(f"Daemon socket not available: {exc}") from exc
    try:
        writer.write((json.dumps(request, ensure_ascii=False) + "\n").encode())
        await asyncio.wait_for(writer.drain(), timeout=_SOCKET_TIMEOUT)
        line = await asyncio.wait_for(reader.readline(), timeout=_SOCKET_TIMEOUT)
        return json.loads(line.decode())
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def get_daemon_status(cwd: Path) -> dict[str, Any]:
    """Query the running daemon for its status. Falls back to state file."""
    state = read_daemon_state(cwd)
    if state is None:
        return {"running": False}
    pid = state.get("pid")
    alive = is_daemon_running(cwd)
    if not alive:
        return {"running": False}
    socket_path = Path(state.get("socket_path", ""))
    try:
        resp = await _socket_rpc(socket_path, {"cmd": "status"})
        resp["running"] = True
        return resp
    except Exception:
        # Socket not yet up or temporarily unavailable
        return {
            "running": True,
            "pid": pid,
            "started_at": state.get("started_at", ""),
            "connected": None,
            "messages_handled": None,
        }


async def stop_daemon(cwd: Path) -> bool:
    """Send a stop command to the running daemon. Returns True if accepted."""
    state = read_daemon_state(cwd)
    if state is None or not is_daemon_running(cwd):
        return False
    socket_path = Path(state.get("socket_path", ""))
    try:
        resp = await _socket_rpc(socket_path, {"cmd": "stop"})
        return bool(resp.get("ok"))
    except Exception as exc:
        logger.debug("stop_daemon socket rpc failed: %s", exc)
        # Fallback: SIGTERM
        pid = state.get("pid")
        if isinstance(pid, int):
            try:
                os.kill(pid, signal.SIGTERM)
                return True
            except Exception:
                pass
        return False


# ---------------------------------------------------------------------------
# Daemon process launch (double-fork)
# ---------------------------------------------------------------------------


def _write_startup_status(fd: int | None, status: str) -> None:
    if fd is None:
        return
    try:
        os.write(fd, (status.replace("\n", " ") + "\n").encode("utf-8", errors="replace"))
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _read_startup_status(fd: int, *, timeout: float) -> str:
    import time

    deadline = time.monotonic() + timeout
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        readable, _, _ = select.select([fd], [], [], min(remaining, 1.0))
        if not readable:
            continue
        data = os.read(fd, 4096)
        if not data:
            break
        chunks.append(data)
        if b"\n" in data:
            break
    if not chunks:
        return "TIMEOUT"
    return b"".join(chunks).split(b"\n", 1)[0].decode("utf-8", errors="replace")


def _wait_for_startup_result(startup_read_fd: int) -> None:
    try:
        status = _read_startup_status(startup_read_fd, timeout=_STARTUP_TIMEOUT)
    finally:
        try:
            os.close(startup_read_fd)
        except OSError:
            pass
    if status == "READY":
        return
    if status.startswith("ERROR "):
        raise RuntimeError(
            f"WeCom daemon failed to start: {status.removeprefix('ERROR ')}"
        )
    raise RuntimeError(
        f"WeCom daemon failed to start within {_STARTUP_TIMEOUT:.0f}s — check the log file."
    )


def _fork_daemon(config: WeComDaemonConfig) -> int:
    """Fork the daemon to the background and return the startup status read fd.

    Uses the standard Unix double-fork idiom so the daemon is fully detached
    from the controlling terminal and cannot reacquire one.

    Refuses to start if another daemon already holds the per-cwd lockfile.
    """
    if is_daemon_running(config.cwd):
        raise RuntimeError(
            f"WeCom daemon already running for {config.cwd} (lockfile held)."
        )

    startup_read_fd, startup_write_fd = os.pipe()
    pid = os.fork()
    if pid > 0:
        # Parent: reap the first child and wait for the grandchild to report
        # that the server, IPC socket and state file are actually ready.
        os.close(startup_write_fd)
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        return startup_read_fd

    # --- First child ---
    os.close(startup_read_fd)
    os.setsid()  # New session — detach from terminal

    pid2 = os.fork()
    if pid2 > 0:
        # First child exits so the grandchild is adopted by init.
        os._exit(0)

    # --- Grandchild (the actual daemon) ---
    _redirect_stdio(config.log_file, preserve_fds=(startup_write_fd,))
    # Acquire the lock NOW so racing peers immediately see we're alive.
    # If somebody else snuck in between the parent's check and here, exit.
    try:
        lock_fd = acquire_daemon_lock(config.cwd)
    except BlockingIOError:
        logger.error("WeCom daemon: another instance acquired the lock — exiting.")
        _write_startup_status(startup_write_fd, "ERROR another instance acquired the lock")
        os._exit(0)
    try:
        asyncio.run(_daemon_main(config, startup_fd=startup_write_fd))
    except Exception as exc:
        _write_startup_status(startup_write_fd, f"ERROR {type(exc).__name__}: {exc}")
        logger.exception("WeCom daemon crashed")
    finally:
        # Releasing the fd releases the flock; the OS would do this anyway.
        try:
            os.close(lock_fd)
        except OSError:
            pass
        os._exit(0)


def start_daemon(config: WeComDaemonConfig) -> None:
    """Fork the daemon and block until its startup handshake completes."""
    _wait_for_startup_result(_fork_daemon(config))


async def start_daemon_async(config: WeComDaemonConfig) -> None:
    """Fork the daemon, then await its startup handshake without blocking the loop."""
    startup_read_fd = _fork_daemon(config)
    await asyncio.to_thread(_wait_for_startup_result, startup_read_fd)


def run_daemon_foreground(config: WeComDaemonConfig) -> None:
    """Run the daemon in the foreground (for debugging). Blocking."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        lock_fd = acquire_daemon_lock(config.cwd)
    except BlockingIOError as exc:
        raise RuntimeError(
            f"WeCom daemon already running for {config.cwd} (lockfile held)."
        ) from exc
    try:
        asyncio.run(_daemon_main(config))
    finally:
        try:
            os.close(lock_fd)
        except OSError:
            pass


def _redirect_stdio(log_file: Path, *, preserve_fds: tuple[int, ...] = ()) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    # Owner-only perms: the log contains chatids, message bodies and bot_id.
    log_fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, _FILE_PERMS)
    # If the file existed already with looser perms, tighten now.
    try:
        os.fchmod(log_fd, _FILE_PERMS)
    except OSError:
        pass
    os.dup2(log_fd, sys.stdout.fileno())
    os.dup2(log_fd, sys.stderr.fileno())
    os.close(log_fd)
    devnull_fd = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull_fd, sys.stdin.fileno())
    os.close(devnull_fd)

    # Close every other inherited fd so the daemon doesn't keep the parent
    # CLI's terminal / pipes / sockets / langgraph fds alive.  Skipping 0..2
    # which we've just rewired above, plus explicit startup handoff fds.
    try:
        max_fd = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
        if max_fd in (resource.RLIM_INFINITY, -1) or max_fd > 65536:
            max_fd = 65536
    except Exception:
        max_fd = 1024
    preserved = {fd for fd in preserve_fds if fd >= 3}
    start = 3
    for fd in sorted(preserved):
        if start < fd:
            os.closerange(start, fd)
        start = fd + 1
    if start < max_fd:
        os.closerange(start, max_fd)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
        force=True,
    )


# ---------------------------------------------------------------------------
# Daemon main loop
# ---------------------------------------------------------------------------


async def _daemon_main(config: WeComDaemonConfig, *, startup_fd: int | None = None) -> None:
    """Async entry point that runs inside the daemon process."""
    logger.info("WeCom daemon starting cwd=%s bot_id=%s", config.cwd, config.bot_id)

    stop_event = asyncio.Event()
    startup_reported = False

    def _report_startup(status: str) -> None:
        nonlocal startup_fd, startup_reported
        if startup_reported:
            return
        _write_startup_status(startup_fd, status)
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
            logger.info("Daemon shell tool disabled; set a shell allow-list to enable it.")
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
        )
        logger.info("LangGraph agent server ready")

        # --- Headless handler ---
        from invincat_cli.wecom.bridge import WeComBridge
        from invincat_cli.wecom.headless import HeadlessWeComHandler
        from invincat_cli.wecom.session import WeComMessageResponder

        bridge_holder: list[WeComBridge] = []  # populated after bridge is created
        scheduler_runner_holder: list[Any] = []  # populated by _run_scheduler

        async def _fire_task_now(task: Any) -> None:
            if scheduler_runner_holder:
                await scheduler_runner_holder[0].fire_now(task)

        handler = HeadlessWeComHandler(
            agent=agent,
            cwd=config.cwd,
            send_request=lambda payload: _bridge_send_request(bridge_holder, payload),
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
                enqueue=lambda p: bridge_holder[0].enqueue(p) if bridge_holder else None,
                flush=_flush_outbox,
                build_agent_input=_make_build_agent_input(config.cwd),
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

        # --- Unix socket IPC server ---
        # Bind socket FIRST, then write state file.  The previous order let
        # external callers see a "running" state file with a socket that
        # hadn't started listening yet, leading them to fall back to SIGTERM
        # against the half-started daemon.
        config.socket_path.parent.mkdir(parents=True, exist_ok=True)
        # Remove stale socket file from a previous crash before binding.
        config.socket_path.unlink(missing_ok=True)
        socket_server = await asyncio.start_unix_server(
            lambda r, w: _handle_socket_client(r, w, bridge, handler, stop_event),
            path=str(config.socket_path),
        )
        # Tighten perms so other local users can't connect and issue "stop".
        try:
            os.chmod(str(config.socket_path), _FILE_PERMS)
        except OSError as exc:
            logger.warning("Could not chmod daemon socket: %s", exc)
        logger.info("IPC socket listening at %s", config.socket_path)

        _write_daemon_state(config)
        logger.info("Daemon state written to %s", config.state_file)
        _report_startup("READY")

        # --- Scheduler ---
        scheduler_task = asyncio.create_task(
            _run_scheduler(config, handler, bridge_holder, stop_event, scheduler_runner_holder)
        )

        # --- Run bridge ---
        bridge_task = asyncio.create_task(
            bridge.run(bot_id=config.bot_id, secret=config.secret, ws_url=config.ws_url)
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
        _remove_daemon_state(config)
        if not startup_reported:
            _report_startup("ERROR daemon stopped before startup completed")
        logger.info("WeCom daemon stopped")


async def _bridge_send_request(
    bridge_holder: list[Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not bridge_holder:
        raise RuntimeError("WeCom bridge not yet initialised")
    return await bridge_holder[0].send_request(payload)


def _make_build_agent_input(cwd: Path):
    async def _build(frame: dict[str, Any]) -> str:
        from invincat_cli.wecom.media import build_wecom_agent_input_with_media_downloads
        return await build_wecom_agent_input_with_media_downloads(frame, cwd=cwd)
    return _build


async def _run_scheduler(
    config: WeComDaemonConfig,
    handler: Any,
    bridge_holder: list[Any],
    stop_event: asyncio.Event,
    runner_holder: list[Any],
) -> None:
    """Background task: tick the scheduler every 60 s and deliver results via WeCom."""
    from invincat_cli.scheduler.runner import SchedulerRunner
    from invincat_cli.scheduler.store import SchedulerStore

    class _CwdFilteredStore(SchedulerStore):
        """Only surface tasks whose cwd matches this daemon's project directory."""

        def list_tasks(self, *, enabled_only: bool = False):
            return [
                t for t in super().list_tasks(enabled_only=enabled_only)
                if t.cwd == str(config.cwd)
            ]

    store = _CwdFilteredStore()

    # Reconcile orphaned 'running' runs left behind by a previous daemon kill.
    # Without this, scheduled_task_runs accumulates rows that never resolve,
    # and scheduled_tasks.last_status stays stuck at 'running' so the run-now
    # check in SchedulerRunner.tick wouldn't matter — but the UI / history
    # would show ghost activity forever.
    try:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
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
            channels = getattr(task.delivery, "channels", []) or []
            wecom_ch = next(
                (ch for ch in channels if isinstance(ch, dict) and ch.get("type") == "wecom"),
                None,
            )
            chatid = str(wecom_ch.get("chatid") or "").strip() if wecom_ch else ""

            if not chatid:
                if wecom_ch is not None:
                    logger.warning(
                        "Scheduled task %r has an empty WeCom chatid in delivery spec; "
                        "messages will not be delivered to WeCom",
                        task_id,
                    )
                else:
                    logger.warning(
                        "Scheduled task %r has no WeCom delivery channel configured; "
                        "messages will not be delivered to WeCom",
                        task_id,
                    )

            # Send start notification if we have a WeCom target.  Use the same
            # robust-delivery helper as the final result so the start notice can
            # survive a transient reconnect during long agent turns.
            if chatid and bridge_holder:
                try:
                    await _deliver_scheduled_text(
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
                result = await handler.run_turn(prompt, synthetic_frame, _noop_on_content)
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
                        summary = result if len(result) <= 1200 else result[:1200].rstrip() + "\n\n(摘要过长，已截断)"
                        content += f"\n\n{summary}"
                else:
                    content = f"❌ 定时任务执行失败：{task.title}"
                    if error_msg:
                        content += f"\n\n{error_msg}"
                try:
                    delivered = await _deliver_scheduled_text(
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
                        run_id, task_id, status=status, error=error_msg,
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
                logger.exception("Scheduled task injection task failed run_id=%s", run_id)

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

    runner = SchedulerRunner(
        store,
        inject_message=_inject_message,
        notify=lambda msg: logger.info("Scheduler: %s", msg),
        is_busy=lambda: False,
        on_timeout=_cancel_timed_out_run,
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
                    await asyncio.wait_for(bridge_holder[0].ready.wait(), timeout=_BRIDGE_READY_TIMEOUT)
                    logger.info("Scheduler: WeCom bridge ready, starting first tick")
                except asyncio.TimeoutError:
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
            except asyncio.TimeoutError:
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


async def _deliver_scheduled_text(
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
      with backoff up to ``_DELIVERY_RETRIES`` times, waiting for ``bridge.ready``
      between attempts so a reconnect-in-progress doesn't burn retry budget.
    - **Server rejections** (``errcode != 0``, e.g. invalid chatid / no
      permission / msgtype unsupported): log loudly and bail without retry —
      retrying won't change the outcome.

    Returns True if WeCom acknowledged the message.
    """
    from invincat_cli.wecom.bridge import WeComOfflineError, WeComServerError
    from invincat_cli.wecom.protocol import build_wecom_text_frame

    payload = build_wecom_text_frame(chatid, content)

    for attempt in range(1, _DELIVERY_RETRIES + 1):
        # Wait for the bridge subscribe handshake to complete before sending.
        # During a reconnect this can take several seconds; the per-attempt
        # ready-timeout caps it without consuming the whole retry budget.
        try:
            await asyncio.wait_for(bridge.ready.wait(), timeout=_DELIVERY_READY_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(
                "wecom scheduled delivery (%s) bridge not ready after %ds (attempt %d/%d, task=%r)",
                label, _DELIVERY_READY_TIMEOUT, attempt, _DELIVERY_RETRIES, task_title,
            )
            if attempt < _DELIVERY_RETRIES:
                await asyncio.sleep(_DELIVERY_RETRY_DELAY)
            continue

        try:
            await bridge.send_request(payload, timeout=_DELIVERY_REQUEST_TIMEOUT)
            logger.info(
                "wecom scheduled delivery (%s) succeeded chatid=%s task=%r attempt=%d",
                label, chatid, task_title, attempt,
            )
            return True
        except WeComServerError as exc:
            # Server-side rejection: retrying won't help (chatid invalid, bot
            # not authorised for this chat, msgtype unsupported, ...).
            logger.error(
                "wecom scheduled delivery (%s) rejected by server: errcode=%s errmsg=%s "
                "chatid=%s task=%r — not retrying",
                label, exc.errcode, exc.errmsg, chatid, task_title,
            )
            return False
        except WeComOfflineError:
            logger.warning(
                "wecom scheduled delivery (%s) offline (attempt %d/%d, chatid=%s task=%r)",
                label, attempt, _DELIVERY_RETRIES, chatid, task_title,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "wecom scheduled delivery (%s) timed out (attempt %d/%d, chatid=%s task=%r)",
                label, attempt, _DELIVERY_RETRIES, chatid, task_title,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "wecom scheduled delivery (%s) transient error (attempt %d/%d, chatid=%s task=%r): %s",
                label, attempt, _DELIVERY_RETRIES, chatid, task_title, exc,
            )
        if attempt < _DELIVERY_RETRIES:
            await asyncio.sleep(_DELIVERY_RETRY_DELAY)

    logger.error(
        "wecom scheduled delivery (%s) permanently failed after %d attempts chatid=%s task=%r",
        label, _DELIVERY_RETRIES, chatid, task_title,
    )
    return False


async def _noop_on_content(_content: str) -> None:
    """No-op on_content for scheduled tasks — active push only, no streaming."""


async def _handle_socket_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    bridge: Any,
    handler: Any,
    stop_event: asyncio.Event,
) -> None:
    pending_stop = False
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout=_SOCKET_TIMEOUT)
        request = json.loads(raw.decode())
        cmd = request.get("cmd", "")

        if cmd == "status":
            # Reflect the actual transport state (subscribe ACK seen) rather
            # than the bridge's intent flag, which is True from boot to stop.
            response: dict[str, Any] = {
                "ok": True,
                "pid": os.getpid(),
                "connected": bridge.ready.is_set(),
                "messages_handled": handler.messages_handled,
            }
        elif cmd == "stop":
            response = {"ok": True}
            pending_stop = True
        else:
            response = {"ok": False, "error": f"Unknown cmd: {cmd}"}

        writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
        await writer.drain()
    except Exception as exc:
        logger.debug("Socket client error: %s", exc)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        # Signal shutdown only AFTER the response has been fully delivered
        # and the connection closed, so the client always sees {"ok": true}.
        if pending_stop:
            stop_event.set()
