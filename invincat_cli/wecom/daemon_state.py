"""State files and lockfile liveness for the WeCom daemon."""

from __future__ import annotations

import datetime
import fcntl
import json
import logging
import os
import signal
from pathlib import Path
from typing import Any

from invincat_cli.wecom.daemon_config import WeComDaemonConfig
from invincat_cli.wecom.daemon_constants import (
    _FILE_PERMS,
    _LOCK_FILENAME,
    _STATE_FILENAME,
)

logger = logging.getLogger(__name__)

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

def _read_lockfile_pid(cwd: Path) -> int | None:
    """Return the PID recorded by the daemon while holding the lockfile."""
    lock_path = cwd / _LOCK_FILENAME
    try:
        raw = lock_path.read_text(encoding="ascii").strip().splitlines()[0]
    except Exception:
        return None
    try:
        pid = int(raw)
    except ValueError:
        return None
    return pid if pid > 0 else None

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

def _state_pid(state: dict[str, Any]) -> int | None:
    try:
        pid = int(state.get("pid"))
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None

def _verified_lock_owner_pid(cwd: Path, state: dict[str, Any]) -> int | None:
    from invincat_cli.wecom import daemon as daemon_mod

    """Return a PID only when state and lockfile agree on the live owner.

    This keeps the socket-less stop path conservative: we only signal the
    process that both wrote the state file and recorded itself in the locked
    lockfile.  Stale state alone is never trusted.
    """
    if state.get("cwd") != str(cwd):
        return None
    state_pid = daemon_mod._state_pid(state)
    lock_pid = daemon_mod._read_lockfile_pid(cwd)
    if state_pid is None or lock_pid is None or state_pid != lock_pid:
        return None
    if not daemon_mod.is_daemon_running(cwd):
        return None
    return state_pid

def _signal_verified_daemon_owner(cwd: Path, state: dict[str, Any]) -> bool:
    from invincat_cli.wecom import daemon as daemon_mod

    pid = daemon_mod._verified_lock_owner_pid(cwd, state)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    except PermissionError:
        logger.warning("No permission to signal WeCom daemon pid=%s", pid)
        return False
    except OSError as exc:
        logger.warning("Failed to signal WeCom daemon pid=%s: %s", pid, exc)
        return False
    logger.info("Sent SIGTERM to verified WeCom daemon pid=%s", pid)
    return True


# ---------------------------------------------------------------------------
# IPC via Unix socket
# ---------------------------------------------------------------------------
