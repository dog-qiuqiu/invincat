"""Tests for the WeCom daemon's fcntl-based liveness check.

The daemon previously inferred liveness from a recorded PID; this was
unsound under PID reuse and could lead ``stop_daemon`` to SIGTERM an
unrelated local process.  These tests verify that ``is_daemon_running``
now relies on an exclusive ``flock`` and that holding the lock is the
sole signal that another daemon is alive.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

import invincat_cli.wecom.daemon as daemon_module
from invincat_cli.wecom.daemon import (
    WeComDaemonConfig,
    _wait_for_bridge_startup,
    _write_daemon_state,
    acquire_daemon_lock,
    get_daemon_status,
    is_daemon_running,
    read_daemon_state,
    stop_daemon,
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


def test_stop_daemon_does_not_sigterm_stale_state_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Socket-less stop must not trust a stale state-file PID."""
    state_dir = tmp_path / ".invincat"
    state_dir.mkdir()
    (state_dir / "wecom_daemon.json").write_text(
        json.dumps(
            {
                "pid": 123456,
                "socket_path": str(state_dir / "missing.sock"),
                "started_at": "2026-01-01T00:00:00",
                "cwd": str(tmp_path),
                "bot_id": "bot",
            }
        ),
        encoding="utf-8",
    )
    killed: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        killed.append((pid, sig))

    monkeypatch.setattr(daemon_module.os, "kill", fake_kill)

    lock_fd = acquire_daemon_lock(tmp_path)
    try:
        assert asyncio.run(stop_daemon(tmp_path)) is False
    finally:
        os.close(lock_fd)

    assert killed == []


def test_stop_daemon_falls_back_to_verified_lock_owner_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Socket-less stop may signal only when state pid and lock owner agree."""
    state_dir = tmp_path / ".invincat"
    state_dir.mkdir()
    (state_dir / "wecom_daemon.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "socket_path": str(state_dir / "missing.sock"),
                "started_at": "2026-01-01T00:00:00",
                "cwd": str(tmp_path),
                "bot_id": "bot",
            }
        ),
        encoding="utf-8",
    )
    killed: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        killed.append((pid, sig))

    monkeypatch.setattr(daemon_module.os, "kill", fake_kill)

    lock_fd = acquire_daemon_lock(tmp_path)
    try:
        assert asyncio.run(stop_daemon(tmp_path)) is True
    finally:
        os.close(lock_fd)

    assert killed == [(os.getpid(), daemon_module.signal.SIGTERM)]


def test_wait_for_bridge_startup_requires_ready_ack() -> None:
    class FakeBridge:
        def __init__(self) -> None:
            self.ready = asyncio.Event()

    async def _run() -> None:
        bridge = FakeBridge()
        bridge.ready.set()
        bridge_task = asyncio.create_task(asyncio.sleep(60))
        try:
            await _wait_for_bridge_startup(bridge, bridge_task)
        finally:
            bridge_task.cancel()

    asyncio.run(_run())


def test_wait_for_bridge_startup_fails_when_bridge_exits_before_ready() -> None:
    class FakeBridge:
        def __init__(self) -> None:
            self.ready = asyncio.Event()

    async def _run() -> None:
        async def _done() -> None:
            return None

        bridge_task = asyncio.create_task(_done())
        await bridge_task
        with pytest.raises(RuntimeError, match="stopped before subscription"):
            await _wait_for_bridge_startup(FakeBridge(), bridge_task)

    asyncio.run(_run())


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


def test_acquire_lock_ignores_chmod_and_pid_write_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = daemon_module.os.write

    def fake_fchmod(_fd: int, _mode: int) -> None:
        raise OSError("chmod failed")

    def fake_write(fd: int, data: bytes) -> int:
        if data == f"{os.getpid()}\n".encode("ascii"):
            raise OSError("write failed")
        return real_write(fd, data)

    monkeypatch.setattr(daemon_module.os, "fchmod", fake_fchmod)
    monkeypatch.setattr(daemon_module.os, "write", fake_write)

    fd = acquire_daemon_lock(tmp_path)
    try:
        assert fd >= 0
    finally:
        os.close(fd)


def test_read_lockfile_pid_handles_missing_invalid_and_non_positive(
    tmp_path: Path,
) -> None:
    assert daemon_module._read_lockfile_pid(tmp_path) is None

    lock_path = tmp_path / ".invincat" / "wecom_daemon.lock"
    lock_path.parent.mkdir()
    lock_path.write_text("abc\n", encoding="ascii")
    assert daemon_module._read_lockfile_pid(tmp_path) is None

    lock_path.write_text("0\n", encoding="ascii")
    assert daemon_module._read_lockfile_pid(tmp_path) is None


def test_is_daemon_running_handles_open_and_cleanup_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / ".invincat" / "wecom_daemon.lock"
    lock_path.parent.mkdir()
    lock_path.write_text("", encoding="ascii")

    def fake_open(*_args: Any, **_kwargs: Any) -> int:
        raise OSError("open failed")

    monkeypatch.setattr(daemon_module.os, "open", fake_open)
    assert is_daemon_running(tmp_path) is False

    flock_calls: list[int] = []

    def fake_flock(_fd: int, flags: int) -> None:
        flock_calls.append(flags)
        if flags == daemon_module.fcntl.LOCK_UN:
            raise OSError("unlock failed")

    def fake_close(_fd: int) -> None:
        raise OSError("close failed")

    monkeypatch.setattr(daemon_module.os, "open", lambda *_args, **_kwargs: 123)
    monkeypatch.setattr(daemon_module.os, "close", fake_close)
    monkeypatch.setattr(daemon_module.fcntl, "flock", fake_flock)

    assert is_daemon_running(tmp_path) is False
    assert flock_calls == [
        daemon_module.fcntl.LOCK_EX | daemon_module.fcntl.LOCK_NB,
        daemon_module.fcntl.LOCK_UN,
    ]


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


def test_write_daemon_state_ignores_fchmod_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = WeComDaemonConfig(
        bot_id="bot",
        secret="secret",
        ws_url="wss://example.test",
        cwd=tmp_path,
    )

    def fake_fchmod(_fd: int, _mode: int) -> None:
        raise OSError("chmod denied")

    monkeypatch.setattr(daemon_module.os, "fchmod", fake_fchmod)

    _write_daemon_state(config)

    state = read_daemon_state(tmp_path)
    assert state is not None
    assert state["bot_id"] == "bot"


def test_config_from_env_requires_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WECOM_BOT_ID", raising=False)
    monkeypatch.delenv("WECOM_BOT_SECRET", raising=False)

    with pytest.raises(ValueError, match="WECOM_BOT_ID"):
        WeComDaemonConfig.from_env(tmp_path)


def test_config_from_env_reads_paths_and_custom_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WECOM_BOT_ID", " bot ")
    monkeypatch.setenv("WECOM_BOT_SECRET", " secret ")
    monkeypatch.setenv("WECOM_WS_URL", " wss://example.test/ws ")

    config = WeComDaemonConfig.from_env(tmp_path)

    assert config.bot_id == "bot"
    assert config.secret == "secret"
    assert config.ws_url == "wss://example.test/ws"
    assert config.state_file == tmp_path / ".invincat" / "wecom_daemon.json"
    assert config.log_file == tmp_path / ".invincat" / "wecom_daemon.log"
    assert config.socket_path == tmp_path / ".invincat" / "wecom_daemon.sock"
    assert config.lock_file == tmp_path / ".invincat" / "wecom_daemon.lock"


def test_read_daemon_state_returns_none_for_missing_or_invalid_json(
    tmp_path: Path,
) -> None:
    assert read_daemon_state(tmp_path) is None

    state_path = tmp_path / ".invincat" / "wecom_daemon.json"
    state_path.parent.mkdir()
    state_path.write_text("{bad json", encoding="utf-8")

    assert read_daemon_state(tmp_path) is None


def test_remove_daemon_state_swallows_unlink_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = WeComDaemonConfig(
        bot_id="bot",
        secret="secret",
        ws_url="wss://example.test",
        cwd=tmp_path,
    )
    seen: list[Path] = []

    def fake_unlink(self: Path, missing_ok: bool = False) -> None:
        seen.append(self)
        raise OSError("cannot unlink")

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    daemon_module._remove_daemon_state(config)

    assert seen == [config.state_file, config.socket_path]


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({}, None),
        ({"pid": None}, None),
        ({"pid": "not-int"}, None),
        ({"pid": 0}, None),
        ({"pid": -1}, None),
        ({"pid": "42"}, 42),
    ],
)
def test_state_pid_validates_positive_integers(
    state: dict[str, Any], expected: int | None
) -> None:
    assert daemon_module._state_pid(state) == expected


def test_verified_lock_owner_requires_matching_state_lock_and_live_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {"cwd": str(tmp_path), "pid": 42}
    monkeypatch.setattr(daemon_module, "_read_lockfile_pid", lambda cwd: 42)
    monkeypatch.setattr(daemon_module, "is_daemon_running", lambda cwd: True)

    assert daemon_module._verified_lock_owner_pid(tmp_path, state) == 42

    assert daemon_module._verified_lock_owner_pid(tmp_path / "other", state) is None

    monkeypatch.setattr(daemon_module, "_read_lockfile_pid", lambda cwd: 99)
    assert daemon_module._verified_lock_owner_pid(tmp_path, state) is None

    monkeypatch.setattr(daemon_module, "_read_lockfile_pid", lambda cwd: 42)
    monkeypatch.setattr(daemon_module, "is_daemon_running", lambda cwd: False)
    assert daemon_module._verified_lock_owner_pid(tmp_path, state) is None


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (None, True),
        (ProcessLookupError(), False),
        (PermissionError(), False),
        (OSError("boom"), False),
    ],
)
def test_signal_verified_daemon_owner_handles_os_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raised: BaseException | None,
    expected: bool,
) -> None:
    monkeypatch.setattr(
        daemon_module, "_verified_lock_owner_pid", lambda cwd, state: 42
    )
    signals: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        signals.append((pid, sig))
        if raised is not None:
            raise raised

    monkeypatch.setattr(daemon_module.os, "kill", fake_kill)

    assert daemon_module._signal_verified_daemon_owner(tmp_path, {}) is expected
    assert signals == [(42, daemon_module.signal.SIGTERM)]


def test_socket_rpc_round_trips_json_and_closes_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeReader:
        async def readline(self) -> bytes:
            return b'{"ok": true, "value": 7}\n'

    class FakeWriter:
        def __init__(self) -> None:
            self.writes: list[bytes] = []
            self.closed = False
            self.waited = False

        def write(self, data: bytes) -> None:
            self.writes.append(data)

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            self.waited = True
            raise RuntimeError("close ignored")

    writer = FakeWriter()

    async def fake_open_unix_connection(_path: str) -> tuple[FakeReader, FakeWriter]:
        return FakeReader(), writer

    monkeypatch.setattr(
        daemon_module.asyncio, "open_unix_connection", fake_open_unix_connection
    )

    response = asyncio.run(
        daemon_module._socket_rpc(tmp_path / "daemon.sock", {"cmd": "status"})
    )

    assert response == {"ok": True, "value": 7}
    assert writer.writes == [b'{"cmd": "status"}\n']
    assert writer.closed is True
    assert writer.waited is True


def test_socket_rpc_reports_missing_socket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_open_unix_connection(_path: str) -> tuple[None, None]:
        raise FileNotFoundError("missing")

    monkeypatch.setattr(
        daemon_module.asyncio, "open_unix_connection", fake_open_unix_connection
    )

    with pytest.raises(RuntimeError, match="Daemon socket not available"):
        asyncio.run(daemon_module._socket_rpc(tmp_path / "missing.sock", {}))


def test_get_daemon_status_uses_socket_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        daemon_module,
        "read_daemon_state",
        lambda cwd: {"pid": 42, "socket_path": str(tmp_path / "daemon.sock")},
    )
    monkeypatch.setattr(daemon_module, "is_daemon_running", lambda cwd: True)

    async def fake_socket_rpc(_socket_path: Path, _request: dict[str, Any]) -> dict:
        return {"ok": True, "connected": True, "messages_handled": 3}

    monkeypatch.setattr(daemon_module, "_socket_rpc", fake_socket_rpc)

    assert asyncio.run(get_daemon_status(tmp_path)) == {
        "ok": True,
        "connected": True,
        "messages_handled": 3,
        "running": True,
    }


def test_get_daemon_status_falls_back_when_socket_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {
        "pid": 42,
        "socket_path": str(tmp_path / "daemon.sock"),
        "started_at": "2026-01-01T00:00:00",
    }
    monkeypatch.setattr(daemon_module, "read_daemon_state", lambda cwd: state)
    monkeypatch.setattr(daemon_module, "is_daemon_running", lambda cwd: True)
    monkeypatch.setattr(
        daemon_module, "_verified_lock_owner_pid", lambda cwd, state: 42
    )

    async def fake_socket_rpc(_socket_path: Path, _request: dict[str, Any]) -> dict:
        raise RuntimeError("down")

    monkeypatch.setattr(daemon_module, "_socket_rpc", fake_socket_rpc)

    assert asyncio.run(get_daemon_status(tmp_path)) == {
        "running": True,
        "pid": 42,
        "started_at": "2026-01-01T00:00:00",
        "connected": None,
        "messages_handled": None,
        "control_socket": "unavailable",
        "verified_stop_fallback": True,
    }


def test_get_daemon_status_reports_not_running_without_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(daemon_module, "read_daemon_state", lambda cwd: None)

    assert asyncio.run(get_daemon_status(tmp_path)) == {"running": False}


def test_get_daemon_status_reports_not_running_for_stale_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        daemon_module,
        "read_daemon_state",
        lambda cwd: {"pid": 42, "socket_path": str(tmp_path / "daemon.sock")},
    )
    monkeypatch.setattr(daemon_module, "is_daemon_running", lambda cwd: False)

    assert asyncio.run(get_daemon_status(tmp_path)) == {"running": False}


def test_stop_daemon_returns_false_for_missing_or_stale_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(daemon_module, "read_daemon_state", lambda cwd: None)
    assert asyncio.run(stop_daemon(tmp_path)) is False

    monkeypatch.setattr(daemon_module, "read_daemon_state", lambda cwd: {"pid": 42})
    monkeypatch.setattr(daemon_module, "is_daemon_running", lambda cwd: False)
    assert asyncio.run(stop_daemon(tmp_path)) is False


def test_stop_daemon_returns_socket_ok_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        daemon_module,
        "read_daemon_state",
        lambda cwd: {"socket_path": str(tmp_path / "daemon.sock")},
    )
    monkeypatch.setattr(daemon_module, "is_daemon_running", lambda cwd: True)

    async def fake_socket_rpc(_socket_path: Path, _request: dict[str, Any]) -> dict:
        return {"ok": True}

    monkeypatch.setattr(daemon_module, "_socket_rpc", fake_socket_rpc)

    assert asyncio.run(stop_daemon(tmp_path)) is True


def test_stop_daemon_socket_failure_uses_signal_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        daemon_module,
        "read_daemon_state",
        lambda cwd: {"socket_path": str(tmp_path / "daemon.sock")},
    )
    monkeypatch.setattr(daemon_module, "is_daemon_running", lambda cwd: True)
    monkeypatch.setattr(
        daemon_module, "_signal_verified_daemon_owner", lambda cwd, state: True
    )

    async def fake_socket_rpc(_socket_path: Path, _request: dict[str, Any]) -> dict:
        raise RuntimeError("down")

    monkeypatch.setattr(daemon_module, "_socket_rpc", fake_socket_rpc)

    assert asyncio.run(stop_daemon(tmp_path)) is True


def test_write_startup_status_sanitizes_newlines_and_closes_fd() -> None:
    read_fd, write_fd = os.pipe()
    try:
        daemon_module._write_startup_status(write_fd, "ERROR first\nsecond")
        assert os.read(read_fd, 1024) == b"ERROR first second\n"
    finally:
        os.close(read_fd)


def test_write_startup_status_ignores_missing_fd_and_os_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon_module._write_startup_status(None, "READY")
    closed: list[int] = []

    def fake_write(_fd: int, _data: bytes) -> int:
        raise OSError("write failed")

    def fake_close(fd: int) -> None:
        closed.append(fd)
        raise OSError("close failed")

    monkeypatch.setattr(daemon_module.os, "write", fake_write)
    monkeypatch.setattr(daemon_module.os, "close", fake_close)

    daemon_module._write_startup_status(123, "READY")

    assert closed == [123]


def test_read_startup_status_returns_timeout_for_empty_pipe() -> None:
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    try:
        assert daemon_module._read_startup_status(read_fd, timeout=0.01) == "TIMEOUT"
    finally:
        os.close(read_fd)


def test_read_startup_status_waits_until_pipe_is_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"READY\n")
    os.close(write_fd)
    calls = 0

    def fake_select(
        rlist: list[int], _wlist: list[int], _xlist: list[int], _timeout: float
    ) -> tuple[list[int], list[int], list[int]]:
        nonlocal calls
        calls += 1
        return ([], [], []) if calls == 1 else (rlist, [], [])

    monkeypatch.setattr(daemon_module.select, "select", fake_select)
    try:
        assert daemon_module._read_startup_status(read_fd, timeout=1.0) == "READY"
    finally:
        os.close(read_fd)


def test_wait_for_startup_result_accepts_ready() -> None:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"READY\n")
    os.close(write_fd)

    daemon_module._wait_for_startup_result(read_fd)


def test_wait_for_startup_result_raises_reported_error() -> None:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"ERROR bad credentials\n")
    os.close(write_fd)

    with pytest.raises(RuntimeError, match="bad credentials"):
        daemon_module._wait_for_startup_result(read_fd)


def test_wait_for_startup_result_raises_timeout_for_empty_status() -> None:
    read_fd, write_fd = os.pipe()
    os.close(write_fd)

    with pytest.raises(RuntimeError, match="failed to start within"):
        daemon_module._wait_for_startup_result(read_fd)


def test_wait_for_startup_result_ignores_close_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"READY\n")
    os.close(write_fd)
    real_close = daemon_module.os.close

    def fake_close(fd: int) -> None:
        if fd == read_fd:
            raise OSError("close failed")
        real_close(fd)

    monkeypatch.setattr(daemon_module.os, "close", fake_close)

    daemon_module._wait_for_startup_result(read_fd)


def test_start_daemon_waits_on_forked_startup_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"READY\n")
    os.close(write_fd)
    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)
    monkeypatch.setattr(daemon_module, "_fork_daemon", lambda _config: read_fd)

    daemon_module.start_daemon(config)


def test_fork_daemon_rejects_existing_running_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)
    monkeypatch.setattr(daemon_module, "is_daemon_running", lambda _cwd: True)

    with pytest.raises(RuntimeError, match="already running"):
        daemon_module._fork_daemon(config)


def test_fork_daemon_parent_returns_startup_read_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)
    closed: list[int] = []
    waited: list[tuple[int, int]] = []

    monkeypatch.setattr(daemon_module, "is_daemon_running", lambda _cwd: False)
    monkeypatch.setattr(daemon_module.os, "pipe", lambda: (10, 11))
    monkeypatch.setattr(daemon_module.os, "fork", lambda: 1234)
    monkeypatch.setattr(daemon_module.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(
        daemon_module.os,
        "waitpid",
        lambda pid, options: waited.append((pid, options)) or (pid, 0),
    )

    assert daemon_module._fork_daemon(config) == 10
    assert closed == [11]
    assert waited == [(1234, 0)]


def test_start_daemon_async_waits_in_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)
    seen: list[int] = []
    monkeypatch.setattr(daemon_module, "_fork_daemon", lambda _config: 123)
    monkeypatch.setattr(
        daemon_module, "_wait_for_startup_result", lambda fd: seen.append(fd)
    )

    asyncio.run(daemon_module.start_daemon_async(config))

    assert seen == [123]


def test_run_daemon_foreground_rejects_existing_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)

    def fake_acquire(_cwd: Path) -> int:
        raise BlockingIOError

    monkeypatch.setattr(daemon_module, "acquire_daemon_lock", fake_acquire)

    with pytest.raises(RuntimeError, match="already running"):
        daemon_module.run_daemon_foreground(config)


def test_run_daemon_foreground_runs_main_and_closes_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)
    closed: list[int] = []

    async def fake_daemon_main(_config: WeComDaemonConfig) -> None:
        return None

    monkeypatch.setattr(daemon_module, "acquire_daemon_lock", lambda _cwd: 123)
    monkeypatch.setattr(daemon_module, "_daemon_main", fake_daemon_main)
    monkeypatch.setattr(daemon_module.os, "close", lambda fd: closed.append(fd))

    daemon_module.run_daemon_foreground(config)

    assert closed == [123]


def test_run_daemon_foreground_ignores_lock_close_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)

    async def fake_daemon_main(_config: WeComDaemonConfig) -> None:
        return None

    def fake_close(_fd: int) -> None:
        raise OSError("already closed")

    monkeypatch.setattr(daemon_module, "acquire_daemon_lock", lambda _cwd: 123)
    monkeypatch.setattr(daemon_module, "_daemon_main", fake_daemon_main)
    monkeypatch.setattr(daemon_module.os, "close", fake_close)

    daemon_module.run_daemon_foreground(config)


def test_daemon_main_runs_ready_lifecycle_with_fake_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)
    events: list[tuple[str, Any]] = []

    config_module = ModuleType("invincat_cli.config")
    setattr(config_module, "SHELL_ALLOW_ALL", object())
    setattr(config_module, "settings", SimpleNamespace(shell_allow_list=[]))
    monkeypatch.setitem(sys.modules, "invincat_cli.config", config_module)

    manager_module = ModuleType("invincat_cli.server.manager")

    class FakeServerProcess:
        def stop(self) -> None:
            events.append(("server_stop", None))

    async def fake_start_server_and_get_agent(
        **kwargs: Any,
    ) -> tuple[object, Any, None]:
        events.append(("start_server", kwargs))
        return object(), FakeServerProcess(), None

    setattr(
        manager_module, "start_server_and_get_agent", fake_start_server_and_get_agent
    )
    monkeypatch.setitem(sys.modules, "invincat_cli.server.manager", manager_module)

    bridge_module = ModuleType("invincat_cli.wecom.bridge")

    class FakeBridge:
        def __init__(self, **_kwargs: Any) -> None:
            self.ready = asyncio.Event()
            self.stopped = False

        async def run(self, **kwargs: Any) -> None:
            events.append(("bridge_run", kwargs))
            self.ready.set()
            await asyncio.Event().wait()

        def stop(self) -> None:
            self.stopped = True
            events.append(("bridge_stop", None))

    setattr(bridge_module, "WeComBridge", FakeBridge)
    monkeypatch.setitem(sys.modules, "invincat_cli.wecom.bridge", bridge_module)

    headless_module = ModuleType("invincat_cli.wecom.headless")

    class FakeHeadlessWeComHandler:
        def __init__(self, **kwargs: Any) -> None:
            events.append(("handler", sorted(kwargs)))

        async def run_turn(self, *_args: Any, **_kwargs: Any) -> str:
            return "done"

    setattr(headless_module, "HeadlessWeComHandler", FakeHeadlessWeComHandler)
    monkeypatch.setitem(sys.modules, "invincat_cli.wecom.headless", headless_module)

    session_module = ModuleType("invincat_cli.wecom.session")

    class FakeResponder:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def handle(self, _frame: dict[str, Any]) -> None:
            return None

    setattr(session_module, "WeComMessageResponder", FakeResponder)
    monkeypatch.setitem(sys.modules, "invincat_cli.wecom.session", session_module)

    async def fake_run_scheduler(
        _config: WeComDaemonConfig,
        _handler: object,
        _bridge_holder: list[Any],
        stop_event: asyncio.Event,
        runner_holder: list[Any],
    ) -> None:
        events.append(("scheduler", None))
        runner_holder.append("runner")
        stop_event.set()

    class FakeSocketServer:
        def close(self) -> None:
            events.append(("socket_close", None))

        async def wait_closed(self) -> None:
            events.append(("socket_wait_closed", None))

    async def fake_start_unix_server(*_args: Any, **kwargs: Any) -> FakeSocketServer:
        events.append(("socket_start", kwargs["path"]))
        return FakeSocketServer()

    statuses: list[tuple[int | None, str]] = []
    monkeypatch.setattr(daemon_module, "_run_scheduler", fake_run_scheduler)
    monkeypatch.setattr(
        daemon_module.asyncio, "start_unix_server", fake_start_unix_server
    )
    monkeypatch.setattr(
        daemon_module,
        "_write_startup_status",
        lambda fd, status: statuses.append((fd, status)),
    )
    monkeypatch.setattr(
        daemon_module.os, "chdir", lambda cwd: events.append(("chdir", cwd))
    )

    asyncio.run(daemon_module._daemon_main(config, startup_fd=77))

    assert statuses == [(77, "READY")]
    assert ("chdir", tmp_path) in events
    assert ("scheduler", None) in events
    assert ("server_stop", None) in events
    assert ("bridge_stop", None) in events
    assert not config.state_file.exists()


def test_redirect_stdio_rewires_standard_streams_and_preserves_fds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[tuple[str, int, int | None]] = []
    fchmods: list[tuple[int, int]] = []
    duped: list[tuple[int, int]] = []
    closed: list[int] = []
    close_ranges: list[tuple[int, int]] = []
    basic_configs: list[dict[str, Any]] = []
    next_fd = iter([10, 11])

    def fake_open(path: str, flags: int, mode: int | None = None) -> int:
        opened.append((path, flags, mode))
        return next(next_fd)

    monkeypatch.setattr(daemon_module.os, "open", fake_open)
    monkeypatch.setattr(
        daemon_module.os,
        "fchmod",
        lambda fd, mode: fchmods.append((fd, mode)),
    )
    monkeypatch.setattr(
        daemon_module.os, "dup2", lambda src, dst: duped.append((src, dst))
    )
    monkeypatch.setattr(daemon_module.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(
        daemon_module.os,
        "closerange",
        lambda start, end: close_ranges.append((start, end)),
    )
    monkeypatch.setattr(daemon_module.resource, "getrlimit", lambda _limit: (0, 20))
    monkeypatch.setattr(
        daemon_module.logging,
        "basicConfig",
        lambda **kwargs: basic_configs.append(kwargs),
    )
    monkeypatch.setattr(daemon_module.sys, "stdout", SimpleNamespace(fileno=lambda: 1))
    monkeypatch.setattr(daemon_module.sys, "stderr", SimpleNamespace(fileno=lambda: 2))
    monkeypatch.setattr(daemon_module.sys, "stdin", SimpleNamespace(fileno=lambda: 0))

    daemon_module._redirect_stdio(
        tmp_path / ".invincat" / "daemon.log",
        preserve_fds=(4, 7),
    )

    assert opened == [
        (
            str(tmp_path / ".invincat" / "daemon.log"),
            daemon_module.os.O_WRONLY
            | daemon_module.os.O_CREAT
            | daemon_module.os.O_APPEND,
            daemon_module._FILE_PERMS,
        ),
        (daemon_module.os.devnull, daemon_module.os.O_RDONLY, None),
    ]
    assert fchmods == [(10, daemon_module._FILE_PERMS)]
    assert duped == [
        (10, daemon_module.sys.stdout.fileno()),
        (10, daemon_module.sys.stderr.fileno()),
        (11, daemon_module.sys.stdin.fileno()),
    ]
    assert closed == [10, 11]
    assert close_ranges == [(3, 4), (5, 7), (8, 20)]
    assert basic_configs[-1]["force"] is True


def test_redirect_stdio_handles_perm_and_rlimit_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    close_ranges: list[tuple[int, int]] = []
    next_fd = iter([10, 11])

    def fake_fchmod(_fd: int, _mode: int) -> None:
        raise OSError("chmod denied")

    def fake_getrlimit(_limit: int) -> tuple[int, int]:
        raise RuntimeError("rlimit unavailable")

    monkeypatch.setattr(
        daemon_module.os,
        "open",
        lambda *_args, **_kwargs: next(next_fd),
    )
    monkeypatch.setattr(daemon_module.os, "fchmod", fake_fchmod)
    monkeypatch.setattr(daemon_module.os, "dup2", lambda *_args: None)
    monkeypatch.setattr(daemon_module.os, "close", lambda _fd: None)
    monkeypatch.setattr(
        daemon_module.os,
        "closerange",
        lambda start, end: close_ranges.append((start, end)),
    )
    monkeypatch.setattr(daemon_module.resource, "getrlimit", fake_getrlimit)
    monkeypatch.setattr(daemon_module.logging, "basicConfig", lambda **_kwargs: None)
    monkeypatch.setattr(daemon_module.sys, "stdout", SimpleNamespace(fileno=lambda: 1))
    monkeypatch.setattr(daemon_module.sys, "stderr", SimpleNamespace(fileno=lambda: 2))
    monkeypatch.setattr(daemon_module.sys, "stdin", SimpleNamespace(fileno=lambda: 0))

    daemon_module._redirect_stdio(tmp_path / ".invincat" / "daemon.log")

    assert close_ranges == [(3, 1024)]


def test_redirect_stdio_clamps_unbounded_fd_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    close_ranges: list[tuple[int, int]] = []
    next_fd = iter([10, 11])

    monkeypatch.setattr(
        daemon_module.os,
        "open",
        lambda *_args, **_kwargs: next(next_fd),
    )
    monkeypatch.setattr(daemon_module.os, "fchmod", lambda *_args: None)
    monkeypatch.setattr(daemon_module.os, "dup2", lambda *_args: None)
    monkeypatch.setattr(daemon_module.os, "close", lambda _fd: None)
    monkeypatch.setattr(
        daemon_module.os,
        "closerange",
        lambda start, end: close_ranges.append((start, end)),
    )
    monkeypatch.setattr(
        daemon_module.resource,
        "getrlimit",
        lambda _limit: (0, daemon_module.resource.RLIM_INFINITY),
    )
    monkeypatch.setattr(daemon_module.logging, "basicConfig", lambda **_kwargs: None)
    monkeypatch.setattr(daemon_module.sys, "stdout", SimpleNamespace(fileno=lambda: 1))
    monkeypatch.setattr(daemon_module.sys, "stderr", SimpleNamespace(fileno=lambda: 2))
    monkeypatch.setattr(daemon_module.sys, "stdin", SimpleNamespace(fileno=lambda: 0))

    daemon_module._redirect_stdio(tmp_path / ".invincat" / "daemon.log")

    assert close_ranges == [(3, 65536)]


def test_bridge_send_request_requires_initialised_bridge() -> None:
    with pytest.raises(RuntimeError, match="not yet initialised"):
        asyncio.run(daemon_module._bridge_send_request([], {"cmd": "send"}))


def test_bridge_send_request_delegates_to_bridge() -> None:
    class FakeBridge:
        async def send_request(self, payload: dict[str, Any]) -> dict[str, Any]:
            return {"echo": payload}

    assert asyncio.run(
        daemon_module._bridge_send_request([FakeBridge()], {"cmd": "send"})
    ) == {"echo": {"cmd": "send"}}


def test_make_build_agent_input_delegates_to_media_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import invincat_cli.wecom.media as media_module

    async def fake_build(frame: dict[str, Any], *, cwd: Path) -> str:
        return f"{cwd.name}:{frame['body']}"

    monkeypatch.setattr(
        media_module, "build_wecom_agent_input_with_media_downloads", fake_build
    )

    build = daemon_module._make_build_agent_input(tmp_path)

    assert asyncio.run(build({"body": "hello"})) == f"{tmp_path.name}:hello"


def test_scheduled_task_visibility_uses_delivery_helper(tmp_path: Path) -> None:
    visible = SimpleNamespace(
        cwd=str(tmp_path),
        delivery=SimpleNamespace(channels=[{"type": "wecom", "chatid": " chat-1 "}]),
    )
    hidden_cwd = SimpleNamespace(
        cwd=str(tmp_path / "other"),
        delivery=SimpleNamespace(channels=[{"type": "wecom", "chatid": "chat-1"}]),
    )
    hidden_delivery = SimpleNamespace(
        cwd=str(tmp_path),
        delivery=SimpleNamespace(channels=[{"type": "webhook", "chatid": "chat-1"}]),
    )

    assert daemon_module._scheduled_task_wecom_chatid(visible) == "chat-1"
    assert daemon_module._task_visible_to_wecom_daemon(visible, tmp_path) is True
    assert daemon_module._task_visible_to_wecom_daemon(hidden_cwd, tmp_path) is False
    assert (
        daemon_module._task_visible_to_wecom_daemon(hidden_delivery, tmp_path) is False
    )


def test_wait_for_bridge_startup_fails_when_bridge_is_cancelled() -> None:
    class FakeBridge:
        def __init__(self) -> None:
            self.ready = asyncio.Event()

    async def _run() -> None:
        bridge_task = asyncio.create_task(asyncio.sleep(60))
        bridge_task.cancel()
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="cancelled before subscription"):
            await _wait_for_bridge_startup(FakeBridge(), bridge_task)

    asyncio.run(_run())


def test_wait_for_bridge_startup_fails_when_bridge_raises() -> None:
    class FakeBridge:
        def __init__(self) -> None:
            self.ready = asyncio.Event()

    async def _run() -> None:
        async def _boom() -> None:
            raise RuntimeError("boom")

        bridge_task = asyncio.create_task(_boom())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="bridge failed"):
            await _wait_for_bridge_startup(FakeBridge(), bridge_task)

    asyncio.run(_run())


def test_wait_for_bridge_startup_fails_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeBridge:
        def __init__(self) -> None:
            self.ready = asyncio.Event()

    async def _run() -> None:
        monkeypatch.setattr(daemon_module, "_BRIDGE_STARTUP_READY_TIMEOUT", 0.01)
        bridge_task = asyncio.create_task(asyncio.sleep(60))
        try:
            with pytest.raises(RuntimeError, match="did not receive subscription"):
                await _wait_for_bridge_startup(FakeBridge(), bridge_task)
        finally:
            bridge_task.cancel()

    asyncio.run(_run())


def test_run_scheduler_injects_wecom_task_and_finishes_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import invincat_cli.scheduler.runner as runner_module
    import invincat_cli.scheduler.store as store_module
    from invincat_cli.scheduler.tool import SCHEDULE_CONTEXT_FLAG

    task = SimpleNamespace(
        id="task-1",
        title="nightly",
        cwd=str(tmp_path),
        delivery=SimpleNamespace(channels=[]),
    )
    hidden_task = SimpleNamespace(
        id="hidden",
        title="hidden",
        cwd=str(tmp_path / "other"),
        delivery=SimpleNamespace(channels=[]),
    )
    delivered: list[tuple[str, str, str]] = []
    finished: list[tuple[str, str, str, str | None]] = []
    run_frames: list[dict[str, Any]] = []
    visible_tasks: list[Any] = []
    try_start_results: list[bool] = []

    class FakeStore:
        def reconcile_orphan_runs(self, *args: Any, **kwargs: Any) -> int:
            return 1

        def list_tasks(
            self, *, enabled_only: bool = False, cwd: str | None = None
        ) -> list[Any]:
            assert enabled_only is True
            assert cwd == str(tmp_path)
            return [task, hidden_task]

        def load_task(self, task_id: str) -> Any:
            if task_id == "task-1":
                return task
            if task_id == "hidden":
                return hidden_task
            return None

        def try_start_run(self, task_id: str, run: Any, **kwargs: Any) -> bool:
            assert task_id == "task-1"
            assert run == "run"
            assert kwargs == {"expected_next_run_at": "expected"}
            return True

    class FakeRunner:
        def __init__(
            self,
            store: Any,
            *,
            inject_message: Any,
            notify: Any,
            is_busy: Any,
            on_timeout: Any = None,
            cwd: str | None = None,
            runner_kind: str = "tui",
        ) -> None:
            self.store = store
            self.inject_message = inject_message
            self.notify = notify
            self.is_busy = is_busy
            self.on_timeout = on_timeout
            self.cwd = cwd
            self.runner_kind = runner_kind
            visible_tasks.extend(store.list_tasks(enabled_only=True, cwd=cwd))
            try_start_results.extend(
                [
                    store.try_start_run(
                        "task-1",
                        "run",
                        expected_next_run_at="expected",
                    ),
                    store.try_start_run("hidden", "run"),
                    store.try_start_run("missing", "run"),
                ]
            )

        async def tick(self) -> None:
            await self.inject_message("task-1", "run-1", "prompt")
            for _ in range(20):
                if finished:
                    break
                await asyncio.sleep(0)
            stop_event.set()

        def finish_run(
            self,
            run_id: str,
            task_id: str,
            *,
            status: str,
            error: str | None = None,
        ) -> None:
            finished.append((run_id, task_id, status, error))

    class FakeHandler:
        async def run_turn(
            self,
            prompt: str,
            frame: dict[str, Any],
            on_content: Any,
            *,
            runtime_context: dict[str, Any],
        ) -> str:
            assert prompt == "prompt"
            assert runtime_context == {SCHEDULE_CONTEXT_FLAG: True}
            await on_content("ignored")
            run_frames.append(frame)
            return "done"

    async def fake_deliver(
        _bridge: Any,
        chatid: str,
        content: str,
        *,
        label: str,
        task_title: str,
    ) -> bool:
        delivered.append((chatid, label, content))
        assert task_title == "nightly"
        return True

    monkeypatch.setattr(store_module, "SchedulerStore", FakeStore)
    monkeypatch.setattr(runner_module, "SchedulerRunner", FakeRunner)
    monkeypatch.setattr(
        daemon_module, "_scheduled_task_wecom_chatid", lambda _task: "chat-1"
    )
    monkeypatch.setattr(daemon_module, "_deliver_scheduled_text", fake_deliver)

    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)
    stop_event = asyncio.Event()
    runner_holder: list[Any] = []
    bridge = SimpleNamespace(ready=asyncio.Event())
    bridge.ready.set()

    asyncio.run(
        daemon_module._run_scheduler(
            config,
            FakeHandler(),
            [bridge],
            stop_event,
            runner_holder,
        )
    )

    assert len(runner_holder) == 1
    assert visible_tasks == [task]
    assert try_start_results == [True, False, False]
    assert run_frames == [
        {"body": {"chatid": "__scheduled_task-1", "_wecom_target_chatid": "chat-1"}}
    ]
    assert delivered[0] == ("chat-1", "start-notice", "⏳ 定时任务开始执行：nightly")
    assert delivered[1] == (
        "chat-1",
        "final-result",
        "✅ 定时任务已完成：nightly\n\ndone",
    )
    assert finished == [("run-1", "task-1", "success", None)]


def test_run_scheduler_timeout_cancels_injected_task_and_notifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import invincat_cli.scheduler.runner as runner_module
    import invincat_cli.scheduler.store as store_module

    task = SimpleNamespace(
        id="task-1",
        title="nightly",
        cwd=str(tmp_path),
        delivery=SimpleNamespace(channels=[]),
    )
    delivered: list[tuple[str, str, str]] = []
    finished: list[tuple[str, str, str, str | None]] = []

    class FakeStore:
        def reconcile_orphan_runs(self, *args: Any, **kwargs: Any) -> int:
            return 0

        def load_task(self, task_id: str) -> Any:
            return task if task_id == "task-1" else None

    class FakeRunner:
        def __init__(
            self,
            store: Any,
            *,
            inject_message: Any,
            notify: Any,
            is_busy: Any,
            on_timeout: Any = None,
            cwd: str | None = None,
            runner_kind: str = "tui",
        ) -> None:
            self.store = store
            self.inject_message = inject_message
            self.on_timeout = on_timeout

        async def tick(self) -> None:
            await self.inject_message("task-1", "run-1", "prompt")
            await asyncio.sleep(0)
            await self.on_timeout("run-1", "task-1")
            for _ in range(20):
                if finished:
                    break
                await asyncio.sleep(0)
            stop_event.set()

        def finish_run(
            self,
            run_id: str,
            task_id: str,
            *,
            status: str,
            error: str | None = None,
        ) -> None:
            finished.append((run_id, task_id, status, error))

    class HangingHandler:
        async def run_turn(
            self,
            prompt: str,
            frame: dict[str, Any],
            on_content: Any,
            *,
            runtime_context: dict[str, Any],
        ) -> str:
            await asyncio.Future()
            raise AssertionError("unreachable")

    async def fake_deliver(
        _bridge: Any,
        chatid: str,
        content: str,
        *,
        label: str,
        task_title: str,
    ) -> bool:
        delivered.append((chatid, label, content))
        assert task_title == "nightly"
        return True

    monkeypatch.setattr(store_module, "SchedulerStore", FakeStore)
    monkeypatch.setattr(runner_module, "SchedulerRunner", FakeRunner)
    monkeypatch.setattr(
        daemon_module, "_scheduled_task_wecom_chatid", lambda _task: "chat-1"
    )
    monkeypatch.setattr(daemon_module, "_deliver_scheduled_text", fake_deliver)

    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)
    stop_event = asyncio.Event()
    runner_holder: list[Any] = []
    bridge = SimpleNamespace(ready=asyncio.Event())
    bridge.ready.set()

    asyncio.run(
        daemon_module._run_scheduler(
            config,
            HangingHandler(),
            [bridge],
            stop_event,
            runner_holder,
        )
    )

    assert delivered == [
        ("chat-1", "start-notice", "⏳ 定时任务开始执行：nightly"),
        ("chat-1", "timeout-result", "⏱️ 定时任务执行超时：nightly"),
    ]
    assert finished == [("run-1", "task-1", "failed", "cancelled (daemon shutdown)")]


def test_run_scheduler_records_missing_task_and_no_chatid_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import invincat_cli.scheduler.runner as runner_module
    import invincat_cli.scheduler.store as store_module

    task = SimpleNamespace(
        id="task-1",
        title="nightly",
        cwd=str(tmp_path),
        delivery=SimpleNamespace(channels=[]),
    )
    finished: list[tuple[str, str, str, str | None]] = []
    frames: list[dict[str, Any]] = []

    class FakeStore:
        def reconcile_orphan_runs(self, *args: Any, **kwargs: Any) -> int:
            return 0

        def load_task(self, task_id: str) -> Any:
            if task_id == "task-1":
                return task
            return None

    class FakeRunner:
        def __init__(
            self,
            store: Any,
            *,
            inject_message: Any,
            notify: Any,
            is_busy: Any,
            on_timeout: Any = None,
            cwd: str | None = None,
            runner_kind: str = "tui",
        ) -> None:
            self.inject_message = inject_message

        async def tick(self) -> None:
            await self.inject_message("missing", "run-missing", "prompt")
            await self.inject_message("task-1", "run-1", "prompt")
            for _ in range(20):
                if len(finished) == 2:
                    break
                await asyncio.sleep(0)
            stop_event.set()

        def finish_run(
            self,
            run_id: str,
            task_id: str,
            *,
            status: str,
            error: str | None = None,
        ) -> None:
            finished.append((run_id, task_id, status, error))

    class FakeHandler:
        async def run_turn(
            self,
            prompt: str,
            frame: dict[str, Any],
            on_content: Any,
            *,
            runtime_context: dict[str, Any],
        ) -> str:
            frames.append(frame)
            raise RuntimeError("agent failed")

    monkeypatch.setattr(store_module, "SchedulerStore", FakeStore)
    monkeypatch.setattr(runner_module, "SchedulerRunner", FakeRunner)
    monkeypatch.setattr(daemon_module, "_scheduled_task_wecom_chatid", lambda _task: "")

    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)
    stop_event = asyncio.Event()
    bridge = SimpleNamespace(ready=asyncio.Event())
    bridge.ready.set()

    asyncio.run(
        daemon_module._run_scheduler(
            config,
            FakeHandler(),
            [bridge],
            stop_event,
            [],
        )
    )

    assert frames == [{"body": {"chatid": "__scheduled_task-1"}}]
    assert ("run-missing", "missing", "failed", "task not found") in finished
    assert ("run-1", "task-1", "failed", "agent failed") in finished


def test_run_scheduler_marks_success_as_failed_when_final_delivery_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import invincat_cli.scheduler.runner as runner_module
    import invincat_cli.scheduler.store as store_module

    task = SimpleNamespace(
        id="task-1",
        title="nightly",
        cwd=str(tmp_path),
        delivery=SimpleNamespace(channels=[]),
    )
    finished: list[tuple[str, str, str, str | None]] = []
    labels: list[str] = []

    class FakeStore:
        def reconcile_orphan_runs(self, *args: Any, **kwargs: Any) -> int:
            return 0

        def load_task(self, task_id: str) -> Any:
            return task

    class FakeRunner:
        def __init__(
            self,
            store: Any,
            *,
            inject_message: Any,
            notify: Any,
            is_busy: Any,
            on_timeout: Any = None,
            cwd: str | None = None,
            runner_kind: str = "tui",
        ) -> None:
            self.inject_message = inject_message

        async def tick(self) -> None:
            await self.inject_message("task-1", "run-1", "prompt")
            for _ in range(20):
                if finished:
                    break
                await asyncio.sleep(0)
            stop_event.set()

        def finish_run(
            self,
            run_id: str,
            task_id: str,
            *,
            status: str,
            error: str | None = None,
        ) -> None:
            finished.append((run_id, task_id, status, error))

    class FakeHandler:
        async def run_turn(
            self,
            prompt: str,
            frame: dict[str, Any],
            on_content: Any,
            *,
            runtime_context: dict[str, Any],
        ) -> str:
            return "done"

    async def fake_deliver(
        _bridge: Any,
        _chatid: str,
        _content: str,
        *,
        label: str,
        task_title: str,
    ) -> bool:
        labels.append(label)
        if label == "final-result":
            raise RuntimeError("delivery exploded")
        return True

    monkeypatch.setattr(store_module, "SchedulerStore", FakeStore)
    monkeypatch.setattr(runner_module, "SchedulerRunner", FakeRunner)
    monkeypatch.setattr(
        daemon_module, "_scheduled_task_wecom_chatid", lambda _task: "chat-1"
    )
    monkeypatch.setattr(daemon_module, "_deliver_scheduled_text", fake_deliver)

    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)
    stop_event = asyncio.Event()
    bridge = SimpleNamespace(ready=asyncio.Event())
    bridge.ready.set()

    asyncio.run(
        daemon_module._run_scheduler(
            config,
            FakeHandler(),
            [bridge],
            stop_event,
            [],
        )
    )

    assert labels == ["start-notice", "final-result"]
    assert finished == [
        ("run-1", "task-1", "failed", "delivery error: delivery exploded")
    ]


def test_run_scheduler_timeout_noops_without_injection_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import invincat_cli.scheduler.runner as runner_module
    import invincat_cli.scheduler.store as store_module

    timeout_deliveries = 0

    class FakeStore:
        def reconcile_orphan_runs(self, *args: Any, **kwargs: Any) -> int:
            return 0

    class FakeRunner:
        def __init__(
            self,
            store: Any,
            *,
            inject_message: Any,
            notify: Any,
            is_busy: Any,
            on_timeout: Any = None,
            cwd: str | None = None,
            runner_kind: str = "tui",
        ) -> None:
            self.on_timeout = on_timeout

        async def tick(self) -> None:
            await self.on_timeout("missing-run", "task-1")
            stop_event.set()

    async def fake_timeout_delivery(*args: Any, **kwargs: Any) -> bool:
        nonlocal timeout_deliveries
        timeout_deliveries += 1
        return True

    monkeypatch.setattr(store_module, "SchedulerStore", FakeStore)
    monkeypatch.setattr(runner_module, "SchedulerRunner", FakeRunner)
    monkeypatch.setattr(
        daemon_module, "_deliver_scheduled_timeout_result", fake_timeout_delivery
    )

    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)
    stop_event = asyncio.Event()
    bridge = SimpleNamespace(ready=asyncio.Event())
    bridge.ready.set()

    asyncio.run(
        daemon_module._run_scheduler(
            config,
            object(),
            [bridge],
            stop_event,
            [],
        )
    )

    assert timeout_deliveries == 0


def test_run_scheduler_recovers_from_reconcile_delivery_and_finish_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import invincat_cli.scheduler.runner as runner_module
    import invincat_cli.scheduler.store as store_module

    task = SimpleNamespace(
        id="task-1",
        title="nightly",
        cwd=str(tmp_path),
        delivery=SimpleNamespace(channels=[]),
    )
    labels: list[str] = []
    finish_calls: list[tuple[str, str, str, str | None]] = []

    class FakeStore:
        def reconcile_orphan_runs(self, *args: Any, **kwargs: Any) -> int:
            raise RuntimeError("reconcile failed")

        def load_task(self, task_id: str) -> Any:
            return task

    class FakeRunner:
        def __init__(
            self,
            store: Any,
            *,
            inject_message: Any,
            notify: Any,
            is_busy: Any,
            on_timeout: Any = None,
            cwd: str | None = None,
            runner_kind: str = "tui",
        ) -> None:
            self.inject_message = inject_message

        async def tick(self) -> None:
            await self.inject_message("task-1", "run-1", "prompt")
            for _ in range(20):
                await asyncio.sleep(0)
            stop_event.set()

        def finish_run(
            self,
            run_id: str,
            task_id: str,
            *,
            status: str,
            error: str | None = None,
        ) -> None:
            finish_calls.append((run_id, task_id, status, error))
            raise RuntimeError("finish failed")

    class FakeHandler:
        async def run_turn(
            self,
            prompt: str,
            frame: dict[str, Any],
            on_content: Any,
            *,
            runtime_context: dict[str, Any],
        ) -> str:
            return "done"

    async def fake_deliver(
        _bridge: Any,
        _chatid: str,
        _content: str,
        *,
        label: str,
        task_title: str,
    ) -> bool:
        labels.append(label)
        if label == "start-notice":
            raise RuntimeError("start failed")
        return False

    monkeypatch.setattr(store_module, "SchedulerStore", FakeStore)
    monkeypatch.setattr(runner_module, "SchedulerRunner", FakeRunner)
    monkeypatch.setattr(
        daemon_module, "_scheduled_task_wecom_chatid", lambda _task: "chat-1"
    )
    monkeypatch.setattr(daemon_module, "_deliver_scheduled_text", fake_deliver)

    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)
    stop_event = asyncio.Event()
    bridge = SimpleNamespace(ready=asyncio.Event())
    bridge.ready.set()

    asyncio.run(
        daemon_module._run_scheduler(
            config,
            FakeHandler(),
            [bridge],
            stop_event,
            [],
        )
    )

    assert labels == ["start-notice", "final-result"]
    assert finish_calls == [
        ("run-1", "task-1", "failed", "WeCom delivery failed after retries")
    ]


def test_run_scheduler_records_unexpected_injection_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import invincat_cli.scheduler.runner as runner_module
    import invincat_cli.scheduler.store as store_module

    finished: list[tuple[str, str, str, str | None]] = []

    class FakeStore:
        def reconcile_orphan_runs(self, *args: Any, **kwargs: Any) -> int:
            return 0

        def load_task(self, task_id: str) -> Any:
            raise RuntimeError("db down")

    class FakeRunner:
        def __init__(
            self,
            store: Any,
            *,
            inject_message: Any,
            notify: Any,
            is_busy: Any,
            on_timeout: Any = None,
            cwd: str | None = None,
            runner_kind: str = "tui",
        ) -> None:
            self.inject_message = inject_message

        async def tick(self) -> None:
            await self.inject_message("task-1", "run-1", "prompt")
            for _ in range(20):
                if finished:
                    break
                await asyncio.sleep(0)
            stop_event.set()

        def finish_run(
            self,
            run_id: str,
            task_id: str,
            *,
            status: str,
            error: str | None = None,
        ) -> None:
            finished.append((run_id, task_id, status, error))

    monkeypatch.setattr(store_module, "SchedulerStore", FakeStore)
    monkeypatch.setattr(runner_module, "SchedulerRunner", FakeRunner)

    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)
    stop_event = asyncio.Event()
    bridge = SimpleNamespace(ready=asyncio.Event())
    bridge.ready.set()

    asyncio.run(
        daemon_module._run_scheduler(
            config,
            object(),
            [bridge],
            stop_event,
            [],
        )
    )

    assert finished == [("run-1", "task-1", "failed", "injection error: db down")]


def test_run_scheduler_timeout_delivery_failure_is_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import invincat_cli.scheduler.runner as runner_module
    import invincat_cli.scheduler.store as store_module

    task = SimpleNamespace(
        id="task-1",
        title="nightly",
        cwd=str(tmp_path),
        delivery=SimpleNamespace(channels=[]),
    )
    finished: list[tuple[str, str, str, str | None]] = []
    timeout_calls = 0

    class FakeStore:
        def reconcile_orphan_runs(self, *args: Any, **kwargs: Any) -> int:
            return 0

        def load_task(self, task_id: str) -> Any:
            return task

    class FakeRunner:
        def __init__(
            self,
            store: Any,
            *,
            inject_message: Any,
            notify: Any,
            is_busy: Any,
            on_timeout: Any = None,
            cwd: str | None = None,
            runner_kind: str = "tui",
        ) -> None:
            self.inject_message = inject_message
            self.on_timeout = on_timeout

        async def tick(self) -> None:
            await self.inject_message("task-1", "run-1", "prompt")
            await asyncio.sleep(0)
            await self.on_timeout("run-1", "task-1")
            for _ in range(20):
                if finished:
                    break
                await asyncio.sleep(0)
            stop_event.set()

        def finish_run(
            self,
            run_id: str,
            task_id: str,
            *,
            status: str,
            error: str | None = None,
        ) -> None:
            finished.append((run_id, task_id, status, error))

    class HangingHandler:
        async def run_turn(
            self,
            prompt: str,
            frame: dict[str, Any],
            on_content: Any,
            *,
            runtime_context: dict[str, Any],
        ) -> str:
            await asyncio.Future()
            raise AssertionError("unreachable")

    async def fake_timeout_delivery(*args: Any, **kwargs: Any) -> bool:
        nonlocal timeout_calls
        timeout_calls += 1
        raise RuntimeError("timeout notice failed")

    monkeypatch.setattr(store_module, "SchedulerStore", FakeStore)
    monkeypatch.setattr(runner_module, "SchedulerRunner", FakeRunner)
    monkeypatch.setattr(
        daemon_module, "_scheduled_task_wecom_chatid", lambda _task: "chat-1"
    )
    monkeypatch.setattr(
        daemon_module, "_deliver_scheduled_timeout_result", fake_timeout_delivery
    )
    monkeypatch.setattr(
        daemon_module,
        "_deliver_scheduled_text",
        lambda *args, **kwargs: asyncio.sleep(0, result=True),
    )

    config = WeComDaemonConfig("bot", "secret", "wss://example.test", tmp_path)
    stop_event = asyncio.Event()
    bridge = SimpleNamespace(ready=asyncio.Event())
    bridge.ready.set()

    asyncio.run(
        daemon_module._run_scheduler(
            config,
            HangingHandler(),
            [bridge],
            stop_event,
            [],
        )
    )

    assert timeout_calls == 1
    assert finished == [("run-1", "task-1", "failed", "cancelled (daemon shutdown)")]


def test_deliver_scheduled_timeout_result_returns_false_without_target() -> None:
    store = SimpleNamespace(load_task=lambda task_id: None)

    assert (
        asyncio.run(
            daemon_module._deliver_scheduled_timeout_result(
                store,
                [object()],
                task_id="missing",
            )
        )
        is False
    )


def test_deliver_scheduled_timeout_result_returns_false_without_chatid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = SimpleNamespace(title="nightly", delivery=SimpleNamespace(channels=[]))
    store = SimpleNamespace(load_task=lambda task_id: task)
    monkeypatch.setattr(daemon_module, "_scheduled_task_wecom_chatid", lambda task: "")

    assert (
        asyncio.run(
            daemon_module._deliver_scheduled_timeout_result(
                store,
                [object()],
                task_id="task-1",
            )
        )
        is False
    )


def test_deliver_scheduled_timeout_result_sends_timeout_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = SimpleNamespace(title="nightly", delivery=SimpleNamespace(channels=[]))
    store = SimpleNamespace(load_task=lambda task_id: task)
    sent: list[tuple[Any, str, str, str, str]] = []
    monkeypatch.setattr(
        daemon_module, "_scheduled_task_wecom_chatid", lambda _task: "chat-1"
    )

    async def fake_deliver(
        bridge: Any,
        chatid: str,
        content: str,
        *,
        label: str,
        task_title: str,
    ) -> bool:
        sent.append((bridge, chatid, content, label, task_title))
        return True

    monkeypatch.setattr(daemon_module, "_deliver_scheduled_text", fake_deliver)
    bridge = object()

    assert (
        asyncio.run(
            daemon_module._deliver_scheduled_timeout_result(
                store,
                [bridge],
                task_id="task-1",
            )
        )
        is True
    )
    assert sent == [
        (bridge, "chat-1", "⏱️ 定时任务执行超时：nightly", "timeout-result", "nightly")
    ]


def test_deliver_scheduled_text_succeeds_after_transient_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.wecom.bridge import WeComOfflineError

    class FakeBridge:
        def __init__(self) -> None:
            self.ready = asyncio.Event()
            self.ready.set()
            self.calls = 0

        async def send_request(
            self, payload: dict[str, Any], *, timeout: float
        ) -> None:
            assert payload["body"]["chatid"] == "chat-1"
            assert timeout == daemon_module._DELIVERY_REQUEST_TIMEOUT
            self.calls += 1
            if self.calls == 1:
                raise WeComOfflineError("offline")

    bridge = FakeBridge()
    monkeypatch.setattr(daemon_module, "_DELIVERY_RETRIES", 2)
    monkeypatch.setattr(daemon_module, "_DELIVERY_RETRY_DELAY", 0)

    assert (
        asyncio.run(
            daemon_module._deliver_scheduled_text(
                bridge,
                "chat-1",
                "done",
                label="final-result",
                task_title="nightly",
            )
        )
        is True
    )
    assert bridge.calls == 2


def test_deliver_scheduled_text_does_not_retry_server_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.wecom.bridge import WeComServerError

    class FakeBridge:
        def __init__(self) -> None:
            self.ready = asyncio.Event()
            self.ready.set()
            self.calls = 0

        async def send_request(
            self, payload: dict[str, Any], *, timeout: float
        ) -> None:
            self.calls += 1
            raise WeComServerError(400, "bad chatid")

    bridge = FakeBridge()
    monkeypatch.setattr(daemon_module, "_DELIVERY_RETRIES", 3)
    monkeypatch.setattr(daemon_module, "_DELIVERY_RETRY_DELAY", 0)

    assert (
        asyncio.run(
            daemon_module._deliver_scheduled_text(
                bridge,
                "chat-1",
                "done",
                label="final-result",
                task_title="nightly",
            )
        )
        is False
    )
    assert bridge.calls == 1


def test_deliver_scheduled_text_returns_false_when_bridge_never_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeBridge:
        def __init__(self) -> None:
            self.ready = asyncio.Event()
            self.calls = 0

        async def send_request(
            self, payload: dict[str, Any], *, timeout: float
        ) -> None:
            self.calls += 1

    bridge = FakeBridge()
    monkeypatch.setattr(daemon_module, "_DELIVERY_RETRIES", 1)
    monkeypatch.setattr(daemon_module, "_DELIVERY_READY_TIMEOUT", 0)

    assert (
        asyncio.run(
            daemon_module._deliver_scheduled_text(
                bridge,
                "chat-1",
                "done",
                label="final-result",
                task_title="nightly",
            )
        )
        is False
    )
    assert bridge.calls == 0


def test_deliver_scheduled_text_retries_ready_wait_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeBridge:
        def __init__(self) -> None:
            self.ready = asyncio.Event()

        async def send_request(
            self, payload: dict[str, Any], *, timeout: float
        ) -> None:
            raise AssertionError("not ready")

    bridge = FakeBridge()
    monkeypatch.setattr(daemon_module, "_DELIVERY_RETRIES", 2)
    monkeypatch.setattr(daemon_module, "_DELIVERY_READY_TIMEOUT", 0)
    monkeypatch.setattr(daemon_module, "_DELIVERY_RETRY_DELAY", 0)

    assert (
        asyncio.run(
            daemon_module._deliver_scheduled_text(
                bridge,
                "chat-1",
                "done",
                label="final-result",
                task_title="nightly",
            )
        )
        is False
    )


@pytest.mark.parametrize("raised", [TimeoutError(), RuntimeError("boom")])
def test_deliver_scheduled_text_retries_transient_request_errors(
    monkeypatch: pytest.MonkeyPatch, raised: BaseException
) -> None:
    class FakeBridge:
        def __init__(self) -> None:
            self.ready = asyncio.Event()
            self.ready.set()
            self.calls = 0

        async def send_request(
            self, payload: dict[str, Any], *, timeout: float
        ) -> None:
            self.calls += 1
            raise raised

    bridge = FakeBridge()
    monkeypatch.setattr(daemon_module, "_DELIVERY_RETRIES", 1)

    assert (
        asyncio.run(
            daemon_module._deliver_scheduled_text(
                bridge,
                "chat-1",
                "done",
                label="final-result",
                task_title="nightly",
            )
        )
        is False
    )
    assert bridge.calls == 1


def test_deliver_scheduled_text_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeBridge:
        def __init__(self) -> None:
            self.ready = asyncio.Event()
            self.ready.set()

        async def send_request(
            self, payload: dict[str, Any], *, timeout: float
        ) -> None:
            raise asyncio.CancelledError

    monkeypatch.setattr(daemon_module, "_DELIVERY_RETRIES", 1)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            daemon_module._deliver_scheduled_text(
                FakeBridge(),
                "chat-1",
                "done",
                label="final-result",
                task_title="nightly",
            )
        )


def test_handle_socket_client_status_response() -> None:
    class FakeWriter:
        def __init__(self) -> None:
            self.payload = b""
            self.closed = False

        def write(self, data: bytes) -> None:
            self.payload += data

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            return None

    async def _run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"cmd": "status"}\n')
        reader.feed_eof()
        writer = FakeWriter()
        ready = SimpleNamespace(is_set=lambda: True)
        bridge = SimpleNamespace(ready=ready)
        handler = SimpleNamespace(messages_handled=5)
        stop_event = asyncio.Event()

        await daemon_module._handle_socket_client(
            reader, writer, bridge, handler, stop_event
        )

        response = json.loads(writer.payload.decode())
        assert response["ok"] is True
        assert response["connected"] is True
        assert response["messages_handled"] == 5
        assert writer.closed is True
        assert stop_event.is_set() is False

    asyncio.run(_run())


def test_handle_socket_client_ignores_bad_request_and_closes_writer() -> None:
    class FakeWriter:
        def __init__(self) -> None:
            self.closed = False
            self.waited = False

        def write(self, data: bytes) -> None:
            raise AssertionError("bad request should not write")

        async def drain(self) -> None:
            raise AssertionError("bad request should not drain")

        def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            self.waited = True

    async def _run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b"{not-json}\n")
        reader.feed_eof()
        writer = FakeWriter()
        stop_event = asyncio.Event()

        await daemon_module._handle_socket_client(
            reader,
            writer,
            SimpleNamespace(ready=SimpleNamespace(is_set=lambda: False)),
            SimpleNamespace(messages_handled=0),
            stop_event,
        )

        assert writer.closed is True
        assert writer.waited is True
        assert stop_event.is_set() is False

    asyncio.run(_run())


def test_handle_socket_client_stop_sets_event_after_response() -> None:
    class FakeWriter:
        def __init__(self) -> None:
            self.payload = b""

        def write(self, data: bytes) -> None:
            self.payload += data

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    async def _run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"cmd": "stop"}\n')
        reader.feed_eof()
        writer = FakeWriter()
        stop_event = asyncio.Event()

        await daemon_module._handle_socket_client(
            reader,
            writer,
            SimpleNamespace(ready=SimpleNamespace(is_set=lambda: False)),
            SimpleNamespace(messages_handled=0),
            stop_event,
        )

        assert json.loads(writer.payload.decode()) == {"ok": True}
        assert stop_event.is_set() is True

    asyncio.run(_run())


def test_handle_socket_client_unknown_command() -> None:
    class FakeWriter:
        def __init__(self) -> None:
            self.payload = b""

        def write(self, data: bytes) -> None:
            self.payload += data

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            raise RuntimeError("ignored")

    async def _run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"cmd": "bogus"}\n')
        reader.feed_eof()
        writer = FakeWriter()
        stop_event = asyncio.Event()

        await daemon_module._handle_socket_client(
            reader,
            writer,
            SimpleNamespace(ready=SimpleNamespace(is_set=lambda: False)),
            SimpleNamespace(messages_handled=0),
            stop_event,
        )

        response = json.loads(writer.payload.decode())
        assert response == {"ok": False, "error": "Unknown cmd: bogus"}
        assert stop_event.is_set() is False

    asyncio.run(_run())
