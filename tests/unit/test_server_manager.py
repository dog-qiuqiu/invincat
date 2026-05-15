"""Tests for server lifecycle orchestration helpers."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from invincat_cli.core.env_vars import SERVER_ENV_PREFIX
from invincat_cli.server import manager
from invincat_cli.server.config import ServerConfig


def test_set_or_clear_server_env(monkeypatch) -> None:
    key = f"{SERVER_ENV_PREFIX}MODEL"
    monkeypatch.delenv(key, raising=False)

    manager._set_or_clear_server_env("MODEL", "openai:gpt-test")
    assert os.environ[key] == "openai:gpt-test"

    manager._set_or_clear_server_env("MODEL", None)
    assert key not in os.environ


def test_apply_server_config_sets_and_clears_env(monkeypatch) -> None:
    stale_key = f"{SERVER_ENV_PREFIX}MODEL_PARAMS"
    monkeypatch.setenv(stale_key, "{}")

    manager._apply_server_config(ServerConfig(model="openai:gpt-test"))

    assert os.environ[f"{SERVER_ENV_PREFIX}MODEL"] == "openai:gpt-test"
    assert stale_key not in os.environ


def test_write_checkpointer_generates_runtime_env_lookup(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.db"
    monkeypatch.setattr("invincat_cli.sessions.get_db_path", lambda: db_path)

    manager._write_checkpointer(tmp_path)

    content = (tmp_path / "checkpointer.py").read_text()
    assert f"{SERVER_ENV_PREFIX}DB_PATH" in content
    assert str(db_path) not in content
    assert os.environ[f"{SERVER_ENV_PREFIX}DB_PATH"] == str(db_path)


def test_write_pyproject_points_to_cli_package(tmp_path) -> None:
    manager._write_pyproject(tmp_path)

    content = (tmp_path / "pyproject.toml").read_text()
    assert 'name = "deepagents-server-runtime"' in content
    assert "deepagents-cli @ file://" in content


def test_scaffold_workspace_writes_required_files(monkeypatch, tmp_path) -> None:
    generated: list[tuple[object, str, str]] = []
    monkeypatch.setattr(
        "invincat_cli.sessions.get_db_path", lambda: tmp_path / "db.sqlite"
    )
    monkeypatch.setattr(
        "invincat_cli.server.app_server.generate_langgraph_json",
        lambda work_dir, graph_ref, checkpointer_path: generated.append(
            (work_dir, graph_ref, checkpointer_path)
        ),
    )

    manager._scaffold_workspace(tmp_path)

    assert (tmp_path / "server_graph.py").exists()
    assert (tmp_path / "checkpointer.py").exists()
    assert (tmp_path / "pyproject.toml").exists()
    assert generated == [
        (tmp_path, "./server_graph.py:graph", "./checkpointer.py:create_checkpointer")
    ]


def test_capture_project_context_returns_none_when_cwd_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "invincat_cli.server.manager.Path.cwd",
        lambda: (_ for _ in ()).throw(OSError("cwd failed")),
    )

    assert manager._capture_project_context() is None


def test_server_session_stops_server_and_cleans_mcp(monkeypatch) -> None:
    server = SimpleNamespace(stopped=False)
    mcp = SimpleNamespace(cleaned=False)

    def _stop() -> None:
        server.stopped = True

    async def _cleanup() -> None:
        mcp.cleaned = True

    server.stop = _stop
    mcp.cleanup = _cleanup

    async def _start(**_kwargs):
        return "agent", server, mcp

    monkeypatch.setattr(manager, "start_server_and_get_agent", _start)

    async def _run() -> None:
        async with manager.server_session(assistant_id="agent") as result:
            assert result == ("agent", server)

    asyncio.run(_run())

    assert mcp.cleaned is True
    assert server.stopped is True


def test_server_session_stops_server_when_body_fails(monkeypatch) -> None:
    server = SimpleNamespace(stopped=False, stop=lambda: None)

    def _stop() -> None:
        server.stopped = True

    server.stop = _stop

    async def _start(**_kwargs):
        return "agent", server, None

    monkeypatch.setattr(manager, "start_server_and_get_agent", _start)

    async def _run() -> None:
        async with manager.server_session(assistant_id="agent"):
            raise RuntimeError("body failed")

    with pytest.raises(RuntimeError, match="body failed"):
        asyncio.run(_run())

    assert server.stopped is True


def test_start_server_and_get_agent_builds_config_and_remote_agent(
    monkeypatch,
    tmp_path,
) -> None:
    created_servers: list[object] = []
    created_agents: list[tuple[str, str]] = []
    scaffolded: list[object] = []

    class FakeServerProcess:
        def __init__(
            self,
            *,
            host: str,
            port: int,
            config_dir: object,
            owns_config_dir: bool,
        ) -> None:
            self.host = host
            self.port = port
            self.config_dir = config_dir
            self.owns_config_dir = owns_config_dir
            self.url = f"http://{host}:{port}"
            self.started = False
            self.stopped = False
            created_servers.append(self)

        async def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

    class FakeRemoteAgent:
        def __init__(self, *, url: str, graph_name: str) -> None:
            self.url = url
            self.graph_name = graph_name
            created_agents.append((url, graph_name))

    monkeypatch.setattr(manager, "_capture_project_context", lambda: None)
    monkeypatch.setattr(manager.tempfile, "mkdtemp", lambda prefix: str(tmp_path))
    monkeypatch.setattr(manager, "_scaffold_workspace", scaffolded.append)
    monkeypatch.setattr(
        "invincat_cli.server.app_server.ServerProcess", FakeServerProcess
    )
    monkeypatch.setattr("invincat_cli.remote.client.RemoteAgent", FakeRemoteAgent)

    async def run_and_assert() -> None:
        agent, server, mcp = await manager.start_server_and_get_agent(
            assistant_id="agent-1",
            model_name="openai:gpt-test",
            host="0.0.0.0",
            port=3030,
            no_mcp=True,
            scheduler_cwd_scope="/tmp/project",
        )

        assert isinstance(agent, FakeRemoteAgent)
        assert isinstance(server, FakeServerProcess)
        assert mcp is None
        assert server.started is True
        assert server.stopped is False

    asyncio.run(run_and_assert())

    assert scaffolded == [tmp_path]
    assert created_agents == [("http://0.0.0.0:3030", "agent")]
    assert os.environ[f"{SERVER_ENV_PREFIX}MODEL"] == "openai:gpt-test"
    assert os.environ[f"{SERVER_ENV_PREFIX}NO_MCP"] == "true"
    assert os.environ[f"{SERVER_ENV_PREFIX}SCHEDULER_CWD_SCOPE"] == str(
        Path("/tmp/project").resolve()
    )


def test_start_server_and_get_agent_stops_server_when_start_fails(
    monkeypatch,
    tmp_path,
) -> None:
    created_servers: list[object] = []

    class FailingServerProcess:
        def __init__(self, **_kwargs: object) -> None:
            self.stopped = False
            created_servers.append(self)

        async def start(self) -> None:
            raise RuntimeError("start failed")

        def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr(manager, "_capture_project_context", lambda: None)
    monkeypatch.setattr(manager.tempfile, "mkdtemp", lambda prefix: str(tmp_path))
    monkeypatch.setattr(manager, "_scaffold_workspace", lambda _work_dir: None)
    monkeypatch.setattr(
        "invincat_cli.server.app_server.ServerProcess",
        FailingServerProcess,
    )

    async def run() -> None:
        with pytest.raises(RuntimeError, match="start failed"):
            await manager.start_server_and_get_agent(assistant_id="agent")

    asyncio.run(run())

    assert created_servers[0].stopped is True


def test_server_session_logs_mcp_cleanup_failures(monkeypatch) -> None:
    server = SimpleNamespace(stopped=False)
    mcp = SimpleNamespace()

    def _stop() -> None:
        server.stopped = True

    async def _cleanup() -> None:
        raise RuntimeError("cleanup failed")

    server.stop = _stop
    mcp.cleanup = _cleanup

    async def _start(**_kwargs):
        return "agent", server, mcp

    monkeypatch.setattr(manager, "start_server_and_get_agent", _start)

    async def _run() -> None:
        async with manager.server_session(assistant_id="agent") as result:
            assert result == ("agent", server)

    asyncio.run(_run())

    assert server.stopped is True
