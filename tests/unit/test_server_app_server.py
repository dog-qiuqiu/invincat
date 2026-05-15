from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from invincat_cli.server import app_server


def test_port_in_use_reports_bind_success_and_failure(monkeypatch) -> None:
    class FakeSocket:
        def __init__(self, *, fail_bind: bool) -> None:
            self.fail_bind = fail_bind

        def __enter__(self) -> FakeSocket:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def bind(self, address: tuple[str, int]) -> None:
            assert address == ("127.0.0.1", 2024)
            if self.fail_bind:
                raise OSError("busy")

    socket_state = {"fail_bind": True}

    def socket_factory(_family: int, _type: int) -> FakeSocket:
        return FakeSocket(fail_bind=socket_state["fail_bind"])

    monkeypatch.setitem(
        sys.modules,
        "socket",
        SimpleNamespace(AF_INET=1, SOCK_STREAM=2, socket=socket_factory),
    )

    assert app_server._port_in_use("127.0.0.1", 2024) is True

    socket_state["fail_bind"] = False
    assert app_server._port_in_use("127.0.0.1", 2024) is False


def test_find_free_port_binds_ephemeral_port(monkeypatch) -> None:
    class FakeSocket:
        def __enter__(self) -> FakeSocket:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def bind(self, address: tuple[str, int]) -> None:
            assert address == ("127.0.0.1", 0)

        def getsockname(self) -> tuple[str, int]:
            return ("127.0.0.1", 3031)

    def socket_factory(_family: int, _type: int) -> FakeSocket:
        return FakeSocket()

    monkeypatch.setitem(
        sys.modules,
        "socket",
        SimpleNamespace(AF_INET=1, SOCK_STREAM=2, socket=socket_factory),
    )

    assert app_server._find_free_port("127.0.0.1") == 3031


def test_generate_langgraph_json_includes_optional_env_and_checkpointer(
    tmp_path,
) -> None:
    path = app_server.generate_langgraph_json(
        tmp_path,
        graph_ref="./graph.py:agent",
        env_file=".env",
        checkpointer_path="./checkpointer.py:create",
    )

    assert path == tmp_path / "langgraph.json"
    assert json.loads(path.read_text()) == {
        "dependencies": ["."],
        "graphs": {"agent": "./graph.py:agent"},
        "env": ".env",
        "checkpointer": {"path": "./checkpointer.py:create"},
    }


def test_scoped_env_overrides_keeps_success_and_rolls_back_failure(monkeypatch) -> None:
    monkeypatch.setenv("INV_TEST_KEEP", "old")
    monkeypatch.delenv("INV_TEST_NEW", raising=False)

    with app_server._scoped_env_overrides(
        {"INV_TEST_KEEP": "new", "INV_TEST_NEW": "value"}
    ):
        assert app_server.os.environ["INV_TEST_KEEP"] == "new"
        assert app_server.os.environ["INV_TEST_NEW"] == "value"

    assert app_server.os.environ["INV_TEST_KEEP"] == "new"
    assert app_server.os.environ["INV_TEST_NEW"] == "value"

    with pytest.raises(RuntimeError, match="boom"):
        with app_server._scoped_env_overrides(
            {"INV_TEST_KEEP": "failed", "INV_TEST_MISSING": "value"}
        ):
            raise RuntimeError("boom")

    assert app_server.os.environ["INV_TEST_KEEP"] == "new"
    assert "INV_TEST_MISSING" not in app_server.os.environ


def test_wait_for_server_healthy_succeeds_with_status_200(monkeypatch) -> None:
    requests: list[tuple[str, int]] = []

    class AsyncClient:
        async def __aenter__(self) -> AsyncClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str, *, timeout: int) -> object:
            requests.append((url, timeout))
            return SimpleNamespace(status_code=200)

    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(AsyncClient=AsyncClient, TransportError=OSError),
    )

    asyncio.run(app_server.wait_for_server_healthy("http://server", timeout=1))

    assert requests == [("http://server/ok", 2)]


def test_wait_for_server_healthy_fails_fast_when_process_exits(
    monkeypatch,
) -> None:
    class AsyncClient:
        async def __aenter__(self) -> AsyncClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    process = SimpleNamespace(returncode=9, poll=lambda: 9)
    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(AsyncClient=AsyncClient, TransportError=OSError),
    )

    with pytest.raises(RuntimeError, match="Server process exited with code 9"):
        asyncio.run(
            app_server.wait_for_server_healthy(
                "http://server",
                timeout=1,
                process=process,
                read_log=lambda: "server log",
            )
        )


def test_wait_for_server_healthy_reports_last_status_and_transport_error(
    monkeypatch,
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    class StatusClient:
        async def __aenter__(self) -> StatusClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str, *, timeout: int) -> object:
            assert timeout == 2
            return SimpleNamespace(status_code=503)

    times = iter([0.0, 0.1, 2.0])
    monkeypatch.setattr(
        app_server, "time", SimpleNamespace(monotonic=lambda: next(times))
    )
    monkeypatch.setattr(app_server.asyncio, "sleep", no_sleep)
    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(AsyncClient=StatusClient, TransportError=OSError),
    )

    with pytest.raises(RuntimeError, match="last status: 503"):
        asyncio.run(app_server.wait_for_server_healthy("http://server", timeout=1))

    class ErrorClient:
        async def __aenter__(self) -> ErrorClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str, *, timeout: int) -> object:
            raise OSError("network down")

    times = iter([0.0, 0.1, 2.0])
    monkeypatch.setattr(
        app_server, "time", SimpleNamespace(monotonic=lambda: next(times))
    )
    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(AsyncClient=ErrorClient, TransportError=OSError),
    )

    with pytest.raises(RuntimeError, match="last error: network down"):
        asyncio.run(app_server.wait_for_server_healthy("http://server", timeout=1))


def test_build_server_cmd_and_env(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "langgraph.json"
    config_path.write_text(json.dumps({"checkpointer": {"path": "check.py:create"}}))
    monkeypatch.setenv("LANGGRAPH_AUTH", "token")
    monkeypatch.setenv("LANGGRAPH_CLOUD_LICENSE_KEY", "license")

    cmd = app_server._build_server_cmd(config_path, host="0.0.0.0", port=3030)
    env = app_server._build_server_env(config_path)

    assert cmd[:3] == [sys.executable, "-m", "langgraph_cli"]
    assert cmd[-3:] == ["--no-reload", "--config", str(config_path)]
    assert "--host" in cmd
    assert "0.0.0.0" in cmd
    assert "3030" in cmd
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["LANGGRAPH_AUTH_TYPE"] == "noop"
    assert env["LANGGRAPH_CHECKPOINTER"] == json.dumps({"path": "check.py:create"})
    assert "LANGGRAPH_AUTH" not in env
    assert "LANGGRAPH_CLOUD_LICENSE_KEY" not in env


def test_build_server_env_ignores_unreadable_or_invalid_config(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    invalid = tmp_path / "langgraph.json"
    invalid.write_text("{bad")

    assert "LANGGRAPH_CHECKPOINTER" not in app_server._build_server_env(missing)
    assert "LANGGRAPH_CHECKPOINTER" not in app_server._build_server_env(invalid)


def test_server_process_reads_log_tail(tmp_path) -> None:
    server = app_server.ServerProcess(config_dir=tmp_path)
    log_path = tmp_path / "server.log"
    log_path.write_text("0123456789")
    server._log_file = log_path.open("r+")

    try:
        assert server.read_log_tail(max_chars=4) == "6789"
        assert server.read_log_tail(max_chars=0) == ""
    finally:
        server._log_file.close()


def test_server_process_log_read_handles_missing_log_file(tmp_path) -> None:
    server = app_server.ServerProcess(config_dir=tmp_path)
    assert server._read_log_file() == ""

    missing_log = tmp_path / "missing.log"
    server._log_file = SimpleNamespace(
        name=str(missing_log),
        flush=lambda: None,
    )

    assert server._read_log_file() == ""
    assert server.read_log_tail(max_chars=10) == ""


def test_server_process_start_reassigns_busy_port_and_waits_for_health(
    monkeypatch,
    tmp_path,
) -> None:
    (tmp_path / "langgraph.json").write_text("{}")
    popen_calls: list[dict[str, object]] = []
    health_calls: list[dict[str, object]] = []

    class FakePopen:
        pid = 123
        returncode = None

        def __init__(self, cmd, **kwargs):  # noqa: ANN001
            self.cmd = cmd
            self.kwargs = kwargs
            self.signals: list[int] = []
            self.killed = False
            popen_calls.append({"cmd": cmd, **kwargs})

        def poll(self) -> None:
            return None

        def send_signal(self, sig: int) -> None:
            self.signals.append(sig)

        def wait(self, *, timeout: float) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            self.killed = True

    async def wait_healthy(url: str, **kwargs: object) -> None:
        health_calls.append({"url": url, **kwargs})

    monkeypatch.setattr(app_server, "_port_in_use", lambda _host, _port: True)
    monkeypatch.setattr(app_server, "_find_free_port", lambda _host: 3031)
    monkeypatch.setattr(app_server.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(app_server, "wait_for_server_healthy", wait_healthy)

    server = app_server.ServerProcess(config_dir=tmp_path, port=2024)
    asyncio.run(server.start(timeout=2))

    assert server.port == 3031
    assert server.running is True
    assert popen_calls[0]["cwd"] == str(tmp_path)
    assert "--port" in popen_calls[0]["cmd"]
    assert "3031" in popen_calls[0]["cmd"]
    assert health_calls[0]["url"] == "http://127.0.0.1:3031"
    assert health_calls[0]["timeout"] == 2
    assert health_calls[0]["local"] is True

    server.stop()
    assert server.running is False


def test_server_process_start_is_noop_when_already_running(tmp_path) -> None:
    server = app_server.ServerProcess(config_dir=tmp_path)
    server._process = SimpleNamespace(poll=lambda: None)

    asyncio.run(server.start())


def test_server_process_start_with_temp_dir_requires_generated_config(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeTempDir:
        def __init__(self, *, prefix: str) -> None:
            assert prefix == "deepagents_server_"
            self.name = str(tmp_path)

        def cleanup(self) -> None:
            return None

    monkeypatch.setattr(app_server.tempfile, "TemporaryDirectory", FakeTempDir)
    server = app_server.ServerProcess()

    with pytest.raises(RuntimeError, match="langgraph.json not found"):
        asyncio.run(server.start())

    assert server._temp_dir is not None


def test_server_process_start_stops_on_health_failure(monkeypatch, tmp_path) -> None:
    (tmp_path / "langgraph.json").write_text("{}")
    stopped: list[bool] = []

    class FakePopen:
        pid = 123
        returncode = None

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        def poll(self) -> None:
            return None

        def send_signal(self, _sig: int) -> None:
            stopped.append(True)

        def wait(self, *, timeout: float) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            return None

    async def fail_health(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("not healthy")

    monkeypatch.setattr(app_server, "_port_in_use", lambda _host, _port: False)
    monkeypatch.setattr(app_server.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(app_server, "wait_for_server_healthy", fail_health)

    server = app_server.ServerProcess(config_dir=tmp_path)

    with pytest.raises(RuntimeError, match="not healthy"):
        asyncio.run(server.start(timeout=1))

    assert stopped == [True]
    assert server.running is False


def test_server_process_stop_handles_timeout_kill_and_cleanup_errors(
    monkeypatch,
    tmp_path,
) -> None:
    class SlowProcess:
        pid = 456

        def __init__(self) -> None:
            self.killed = False

        def poll(self) -> None:
            return None

        def send_signal(self, _sig: int) -> None:
            return None

        def wait(self, *, timeout: float) -> int:
            raise app_server.subprocess.TimeoutExpired("cmd", timeout)

        def kill(self) -> None:
            self.killed = True

    class BadLog:
        name = str(tmp_path / "log.txt")

        def close(self) -> None:
            raise OSError("close failed")

    process = SlowProcess()
    server = app_server.ServerProcess(config_dir=tmp_path)
    server._process = process
    server._log_file = BadLog()

    server._stop_process()

    assert process.killed is True
    assert server._process is None
    assert server._log_file is None


def test_server_process_stop_handles_signal_os_error() -> None:
    class BadProcess:
        pid = 789

        def poll(self) -> None:
            return None

        def send_signal(self, _sig: int) -> None:
            raise OSError("signal failed")

    server = app_server.ServerProcess()
    server._process = BadProcess()

    server._stop_process()

    assert server._process is None


def test_server_process_stop_cleans_temp_and_owned_config_dirs(
    monkeypatch,
    tmp_path,
) -> None:
    cleaned: list[str] = []
    removed: list[object] = []

    class BadTempDir:
        def cleanup(self) -> None:
            cleaned.append("temp")
            raise OSError("cleanup failed")

    monkeypatch.setattr(
        "shutil.rmtree",
        lambda path: removed.append(path) or (_ for _ in ()).throw(OSError("rm")),
    )

    server = app_server.ServerProcess(config_dir=tmp_path, owns_config_dir=True)
    server._temp_dir = BadTempDir()

    server.stop()

    assert cleaned == ["temp"]
    assert removed == [tmp_path]
    assert server._temp_dir is None
    assert server._owns_config_dir is False


def test_server_process_start_requires_langgraph_json(tmp_path) -> None:
    server = app_server.ServerProcess(config_dir=tmp_path)

    with pytest.raises(RuntimeError, match="langgraph.json not found"):
        asyncio.run(server.start())


def test_server_process_restart_applies_and_clears_env_overrides(
    monkeypatch, tmp_path
) -> None:
    (tmp_path / "langgraph.json").write_text("{}")
    server = app_server.ServerProcess(config_dir=tmp_path)
    server.update_env(INV_RESTART_MODEL="new")
    starts: list[str] = []

    async def start(*, timeout: float) -> None:
        starts.append(app_server.os.environ["INV_RESTART_MODEL"])

    monkeypatch.setattr(server, "start", start)
    monkeypatch.setattr(server, "_stop_process", lambda: None)

    asyncio.run(server.restart(timeout=3))

    assert starts == ["new"]
    assert server._env_overrides == {}
    assert app_server.os.environ["INV_RESTART_MODEL"] == "new"


def test_server_process_restart_rolls_back_env_on_start_failure(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("INV_RESTART_MODEL", "old")
    server = app_server.ServerProcess(config_dir=tmp_path)
    server.update_env(INV_RESTART_MODEL="new", INV_RESTART_EXTRA="value")

    async def fail_start(*, timeout: float) -> None:
        assert app_server.os.environ["INV_RESTART_MODEL"] == "new"
        assert app_server.os.environ["INV_RESTART_EXTRA"] == "value"
        raise RuntimeError("restart failed")

    monkeypatch.setattr(server, "start", fail_start)
    monkeypatch.setattr(server, "_stop_process", lambda: None)

    with pytest.raises(RuntimeError, match="restart failed"):
        asyncio.run(server.restart(timeout=3))

    assert app_server.os.environ["INV_RESTART_MODEL"] == "old"
    assert "INV_RESTART_EXTRA" not in app_server.os.environ
    assert server._env_overrides == {
        "INV_RESTART_MODEL": "new",
        "INV_RESTART_EXTRA": "value",
    }


def test_server_process_async_context_manager_starts_and_stops(monkeypatch) -> None:
    server = app_server.ServerProcess()
    calls: list[str] = []

    async def start() -> None:
        calls.append("start")

    def stop() -> None:
        calls.append("stop")

    monkeypatch.setattr(server, "start", start)
    monkeypatch.setattr(server, "stop", stop)

    async def run() -> None:
        async with server as active:
            assert active is server
            calls.append("body")

    asyncio.run(run())

    assert calls == ["start", "body", "stop"]
