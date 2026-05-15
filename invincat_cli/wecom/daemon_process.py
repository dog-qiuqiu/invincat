"""Process startup and stdio handling for the WeCom daemon."""

from __future__ import annotations

import asyncio
import logging
import os
import resource
import select
import sys
from pathlib import Path

from invincat_cli.wecom.daemon_config import WeComDaemonConfig

logger = logging.getLogger(__name__)

def _write_startup_status(fd: int | None, status: str) -> None:
    if fd is None:
        return
    try:
        os.write(
            fd, (status.replace("\n", " ") + "\n").encode("utf-8", errors="replace")
        )
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
    from invincat_cli.wecom import daemon as daemon_mod

    try:
        status = _read_startup_status(startup_read_fd, timeout=daemon_mod._STARTUP_TIMEOUT)
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
        f"WeCom daemon failed to start within {daemon_mod._STARTUP_TIMEOUT:.0f}s — check the log file."
    )

def _fork_daemon(config: WeComDaemonConfig) -> int:
    from invincat_cli.wecom import daemon as daemon_mod

    """Fork the daemon to the background and return the startup status read fd.

    Uses the standard Unix double-fork idiom so the daemon is fully detached
    from the controlling terminal and cannot reacquire one.

    Refuses to start if another daemon already holds the per-cwd lockfile.
    """
    if daemon_mod.is_daemon_running(config.cwd):
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
    daemon_mod._redirect_stdio(config.log_file, preserve_fds=(startup_write_fd,))
    # Acquire the lock NOW so racing peers immediately see we're alive.
    # If somebody else snuck in between the parent's check and here, exit.
    try:
        lock_fd = daemon_mod.acquire_daemon_lock(config.cwd)
    except BlockingIOError:
        logger.error("WeCom daemon: another instance acquired the lock — exiting.")
        daemon_mod._write_startup_status(
            startup_write_fd, "ERROR another instance acquired the lock"
        )
        os._exit(0)
    try:
        asyncio.run(daemon_mod._daemon_main(config, startup_fd=startup_write_fd))
    except Exception as exc:
        daemon_mod._write_startup_status(startup_write_fd, f"ERROR {type(exc).__name__}: {exc}")
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
    from invincat_cli.wecom import daemon as daemon_mod

    daemon_mod._wait_for_startup_result(daemon_mod._fork_daemon(config))

async def start_daemon_async(config: WeComDaemonConfig) -> None:
    """Fork the daemon, then await its startup handshake without blocking the loop."""
    from invincat_cli.wecom import daemon as daemon_mod

    startup_read_fd = daemon_mod._fork_daemon(config)
    await asyncio.to_thread(daemon_mod._wait_for_startup_result, startup_read_fd)

def run_daemon_foreground(config: WeComDaemonConfig) -> None:
    from invincat_cli.wecom import daemon as daemon_mod

    """Run the daemon in the foreground (for debugging). Blocking."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    try:
        lock_fd = daemon_mod.acquire_daemon_lock(config.cwd)
    except BlockingIOError as exc:
        raise RuntimeError(
            f"WeCom daemon already running for {config.cwd} (lockfile held)."
        ) from exc
    try:
        asyncio.run(daemon_mod._daemon_main(config))
    finally:
        try:
            os.close(lock_fd)
        except OSError:
            pass

def _redirect_stdio(log_file: Path, *, preserve_fds: tuple[int, ...] = ()) -> None:
    from invincat_cli.wecom import daemon as daemon_mod

    log_file.parent.mkdir(parents=True, exist_ok=True)
    # Owner-only perms: the log contains chatids, message bodies and bot_id.
    log_fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, daemon_mod._FILE_PERMS)
    # If the file existed already with looser perms, tighten now.
    try:
        os.fchmod(log_fd, daemon_mod._FILE_PERMS)
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
