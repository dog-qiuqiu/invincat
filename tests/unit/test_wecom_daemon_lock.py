"""Tests for the WeCom daemon's fcntl-based liveness check.

The daemon previously inferred liveness from a recorded PID; this was
unsound under PID reuse and could lead ``stop_daemon`` to SIGTERM an
unrelated local process.  These tests verify that ``is_daemon_running``
now relies on an exclusive ``flock`` and that holding the lock is the
sole signal that another daemon is alive.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from invincat_cli.wecom.daemon import (
    WeComDaemonConfig,
    _write_daemon_state,
    acquire_daemon_lock,
    is_daemon_running,
)


def test_no_lock_means_not_running(tmp_path: Path) -> None:
    """A clean directory with no lockfile is reported as not running."""
    assert is_daemon_running(tmp_path) is False


def test_unheld_lockfile_means_not_running(tmp_path: Path) -> None:
    """A leftover lockfile from a dead daemon is correctly classified as dead."""
    # Touch the lockfile but don't hold it.  This simulates a previous
    # daemon that exited (the OS released its flock on exit).
    lock_dir = tmp_path / ".invincat"
    lock_dir.mkdir()
    (lock_dir / "wecom_daemon.lock").write_bytes(b"99999\n")
    assert is_daemon_running(tmp_path) is False


def test_held_lock_reports_running(tmp_path: Path) -> None:
    """Holding the exclusive flock makes the daemon look alive to peers."""
    lock_fd = acquire_daemon_lock(tmp_path)
    try:
        assert is_daemon_running(tmp_path) is True
    finally:
        os.close(lock_fd)
    # Once we drop the fd, the OS releases the flock and peers see "not running".
    assert is_daemon_running(tmp_path) is False


def test_acquire_lock_twice_blocks(tmp_path: Path) -> None:
    """Second acquire raises BlockingIOError — the double-start guard."""
    first = acquire_daemon_lock(tmp_path)
    try:
        with pytest.raises(BlockingIOError):
            acquire_daemon_lock(tmp_path)
    finally:
        os.close(first)


def test_lock_per_cwd(tmp_path: Path) -> None:
    """Two different project directories can each have their own daemon."""
    cwd_a = tmp_path / "a"
    cwd_b = tmp_path / "b"
    cwd_a.mkdir()
    cwd_b.mkdir()

    fd_a = acquire_daemon_lock(cwd_a)
    try:
        # Different cwd → independent lockfile → second acquire succeeds.
        fd_b = acquire_daemon_lock(cwd_b)
        try:
            assert is_daemon_running(cwd_a) is True
            assert is_daemon_running(cwd_b) is True
        finally:
            os.close(fd_b)
    finally:
        os.close(fd_a)


def test_lockfile_owner_only_perms(tmp_path: Path) -> None:
    """Lockfile is created with 0600 — local users can't tamper with it."""
    fd = acquire_daemon_lock(tmp_path)
    try:
        lock_path = tmp_path / ".invincat" / "wecom_daemon.lock"
        mode = os.stat(lock_path).st_mode & 0o777
        # Some umasks may further restrict; we only require <= 0600 here.
        assert mode & 0o077 == 0, f"lockfile mode {oct(mode)} leaks to group/other"
    finally:
        os.close(fd)


def test_lockfile_existing_mode_is_tightened(tmp_path: Path) -> None:
    lock_path = tmp_path / ".invincat" / "wecom_daemon.lock"
    lock_path.parent.mkdir()
    lock_path.write_text("", encoding="utf-8")
    os.chmod(lock_path, 0o666)

    fd = acquire_daemon_lock(tmp_path)
    try:
        mode = os.stat(lock_path).st_mode & 0o777
        assert mode & 0o077 == 0, f"lockfile mode {oct(mode)} leaks to group/other"
    finally:
        os.close(fd)


def test_state_file_existing_mode_is_tightened(tmp_path: Path) -> None:
    state_path = tmp_path / ".invincat" / "wecom_daemon.json"
    state_path.parent.mkdir()
    state_path.write_text("{}", encoding="utf-8")
    os.chmod(state_path, 0o666)

    config = WeComDaemonConfig(
        bot_id="bot",
        secret="secret",
        ws_url="wss://example.test",
        cwd=tmp_path,
    )
    _write_daemon_state(config)

    mode = os.stat(state_path).st_mode & 0o777
    assert mode & 0o077 == 0, f"state file mode {oct(mode)} leaks to group/other"
