"""Unit tests for MCP configuration trust handling."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from invincat_cli.mcp import tools as mcp_tools
from invincat_cli.mcp.tools import (
    MCPSessionManager,
    _check_remote_server,
    _check_stdio_server,
    _load_tools_from_config,
    classify_discovered_configs,
    discover_mcp_configs,
    extract_server_summaries,
    extract_stdio_server_commands,
    get_mcp_tools,
    load_mcp_config,
    load_mcp_config_lenient,
    merge_mcp_configs,
    resolve_and_load_mcp_tools,
)
from invincat_cli.project_utils import ProjectContext


def _install_fake_mcp_adapter_modules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    load_raises: bool = False,
    client_raises: bool = False,
) -> dict:
    captured = {"connections": None, "load_calls": []}

    package = ModuleType("langchain_mcp_adapters")
    client_module = ModuleType("langchain_mcp_adapters.client")
    sessions_module = ModuleType("langchain_mcp_adapters.sessions")
    tools_module = ModuleType("langchain_mcp_adapters.tools")

    class FakeClient:
        def __init__(self, *, connections):
            if client_raises:
                raise ValueError("client failed")
            captured["connections"] = connections

        def session(self, server_name: str):
            @asynccontextmanager
            async def _session():
                yield {"server": server_name}

            return _session()

    def fake_connection(**kwargs):
        return dict(kwargs)

    async def fake_load_mcp_tools(session, *, server_name, tool_name_prefix):
        captured["load_calls"].append((session, server_name, tool_name_prefix))
        if load_raises:
            raise ValueError("load failed")
        return [
            SimpleNamespace(
                name=f"{server_name}_tool",
                description=None if server_name == "stdio" else "Remote tool",
            )
        ]

    client_module.MultiServerMCPClient = FakeClient
    sessions_module.SSEConnection = fake_connection
    sessions_module.StdioConnection = fake_connection
    sessions_module.StreamableHttpConnection = fake_connection
    tools_module.load_mcp_tools = fake_load_mcp_tools

    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters", package)
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", client_module)
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.sessions", sessions_module)
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.tools", tools_module)
    return captured


def test_extract_server_summaries_includes_remote_and_stdio() -> None:
    config = {
        "mcpServers": {
            "local": {"command": "node", "args": ["server.js"]},
            "remote": {"type": "http", "url": "https://mcp.example.com"},
        }
    }

    assert extract_server_summaries(config) == [
        ("local", "stdio", "node server.js"),
        ("remote", "http", "https://mcp.example.com"),
    ]


def test_load_mcp_config_validates_required_shapes(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(FileNotFoundError):
        load_mcp_config(str(missing))

    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{bad", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_mcp_config(str(invalid_json))

    no_servers = tmp_path / "no_servers.json"
    no_servers.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="mcpServers"):
        load_mcp_config(str(no_servers))

    wrong_type = tmp_path / "wrong_type.json"
    wrong_type.write_text(json.dumps({"mcpServers": []}), encoding="utf-8")
    with pytest.raises(TypeError, match="dictionary"):
        load_mcp_config(str(wrong_type))

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_mcp_config(str(empty))


@pytest.mark.parametrize(
    ("server", "error_type", "message"),
    [
        ([], TypeError, "dictionary"),
        ({"type": "http"}, ValueError, "url"),
        ({"type": "sse", "url": "https://x", "headers": []}, TypeError, "headers"),
        ({}, ValueError, "command"),
        ({"command": "node", "args": "bad"}, TypeError, "args"),
        ({"command": "node", "env": []}, TypeError, "env"),
        ({"type": "websocket", "url": "wss://x"}, ValueError, "unsupported"),
    ],
)
def test_load_mcp_config_validates_server_entries(
    tmp_path: Path,
    server: dict[str, object],
    error_type: type[Exception],
    message: str,
) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"bad": server}}), encoding="utf-8")

    with pytest.raises(error_type, match=message):
        load_mcp_config(str(path))


def test_load_mcp_config_accepts_stdio_and_remote_entries(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    config = {
        "mcpServers": {
            "local": {"command": "node", "args": ["server.js"], "env": {"A": "B"}},
            "remote": {
                "transport": "sse",
                "url": "https://mcp.example.com",
                "headers": {"Authorization": "Bearer x"},
            },
        }
    }
    path.write_text(json.dumps(config), encoding="utf-8")

    assert load_mcp_config(str(path)) == config


def test_discover_and_classify_mcp_configs(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".invincat").mkdir(parents=True)
    (project / ".invincat").mkdir(parents=True)
    user_cfg = home / ".invincat" / ".mcp.json"
    project_sub_cfg = project / ".invincat" / ".mcp.json"
    project_root_cfg = project / ".mcp.json"
    for path in (user_cfg, project_sub_cfg, project_root_cfg):
        path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    context = ProjectContext(user_cwd=project, project_root=project)

    discovered = discover_mcp_configs(project_context=context)
    user, project_configs = classify_discovered_configs(discovered)

    assert discovered == [user_cfg, project_sub_cfg, project_root_cfg]
    assert user == [user_cfg]
    assert project_configs == [project_sub_cfg, project_root_cfg]


def test_discover_and_classify_treat_unreadable_paths_as_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    context = ProjectContext(user_cwd=project, project_root=project)

    def raise_os_error(_path: Path) -> bool:
        raise OSError("stat failed")

    monkeypatch.setattr(Path, "is_file", raise_os_error)

    assert discover_mcp_configs(project_context=context) == []

    class UnreadablePath:
        def resolve(self) -> Path:
            raise OSError("resolve failed")

    unreadable = UnreadablePath()
    user, project_configs = classify_discovered_configs([unreadable])

    assert user == []
    assert project_configs == [unreadable]


def test_extract_stdio_commands_and_merge_configs() -> None:
    config = {
        "mcpServers": {
            "local": {"command": "node", "args": ["server.js"]},
            "remote": {"type": "http", "url": "https://mcp.example.com"},
            "bad": "not-a-dict",
        }
    }

    assert extract_stdio_server_commands(config) == [("local", "node", ["server.js"])]
    assert extract_server_summaries(config) == [
        ("local", "stdio", "node server.js"),
        ("remote", "http", "https://mcp.example.com"),
    ]
    assert extract_stdio_server_commands({"mcpServers": []}) == []
    assert extract_server_summaries({"mcpServers": []}) == []
    assert merge_mcp_configs(
        [
            {"mcpServers": {"a": {"command": "one"}}},
            {"mcpServers": {"a": {"command": "two"}, "b": {"command": "three"}}},
            {"ignored": {}},
        ]
    ) == {"mcpServers": {"a": {"command": "two"}, "b": {"command": "three"}}}


def test_load_mcp_config_lenient_skips_invalid_files(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{bad", encoding="utf-8")
    valid = tmp_path / "valid.json"
    valid.write_text(
        json.dumps({"mcpServers": {"local": {"command": "node"}}}),
        encoding="utf-8",
    )

    assert load_mcp_config_lenient(missing) is None
    assert load_mcp_config_lenient(invalid) is None
    assert load_mcp_config_lenient(valid) == {
        "mcpServers": {"local": {"command": "node"}}
    }


def test_load_mcp_config_lenient_skips_unreadable_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unreadable = tmp_path / "unreadable.json"

    def raise_os_error(_config_path: str) -> dict:
        raise OSError("permission denied")

    monkeypatch.setattr(mcp_tools, "load_mcp_config", raise_os_error)

    assert load_mcp_config_lenient(unreadable) is None


def test_session_manager_cleanup_closes_exit_stack() -> None:
    manager = MCPSessionManager()
    closed: list[bool] = []

    class FakeExitStack:
        async def aclose(self) -> None:
            closed.append(True)

    manager.exit_stack = FakeExitStack()

    asyncio.run(manager.cleanup())

    assert manager.client is None
    assert closed == [True]


def test_stdio_server_check_reports_missing_command(monkeypatch) -> None:
    with pytest.raises(RuntimeError, match="missing 'command'"):
        _check_stdio_server("local", {})

    monkeypatch.setattr(mcp_tools.shutil, "which", lambda _command: None)
    with pytest.raises(RuntimeError, match="not found"):
        _check_stdio_server("local", {"command": "missing"})

    monkeypatch.setattr(mcp_tools.shutil, "which", lambda _command: "/bin/node")
    _check_stdio_server("local", {"command": "node"})


def test_remote_server_check_reports_missing_or_unreachable_url(monkeypatch) -> None:
    async def run() -> None:
        with pytest.raises(RuntimeError, match="missing 'url'"):
            await _check_remote_server("remote", {})

        class FakeClient:
            async def __aenter__(self) -> FakeClient:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def head(self, _url: str, *, timeout: int) -> None:
                assert timeout == 2
                raise OSError("network down")

        monkeypatch.setattr("httpx.AsyncClient", lambda: FakeClient())
        with pytest.raises(RuntimeError, match="unreachable"):
            await _check_remote_server("remote", {"url": "https://mcp.example.com"})

    asyncio.run(run())


def test_remote_server_check_allows_successful_head(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    async def run() -> None:
        class FakeClient:
            async def __aenter__(self) -> FakeClient:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def head(self, url: str, *, timeout: int) -> None:
                calls.append((url, timeout))

        monkeypatch.setattr("httpx.AsyncClient", lambda: FakeClient())

        await _check_remote_server("remote", {"url": "https://mcp.example.com"})

    asyncio.run(run())

    assert calls == [("https://mcp.example.com", 2)]


def test_load_tools_from_config_builds_connections_and_server_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_mcp_adapter_modules(monkeypatch)
    remote_checks: list[tuple[str, str]] = []

    monkeypatch.setattr(mcp_tools, "_check_stdio_server", lambda *_args: None)

    async def fake_remote_check(server_name: str, server_config: dict) -> None:
        remote_checks.append((server_name, server_config["url"]))

    monkeypatch.setattr(mcp_tools, "_check_remote_server", fake_remote_check)

    config = {
        "mcpServers": {
            "stdio": {"command": "node", "args": ["server.js"], "env": {"A": "B"}},
            "http": {
                "type": "http",
                "url": "https://mcp.example.com/http",
                "headers": {"Authorization": "Bearer token"},
            },
            "sse": {"type": "sse", "url": "https://mcp.example.com/sse"},
        }
    }

    tools, manager, server_infos = asyncio.run(_load_tools_from_config(config))
    asyncio.run(manager.cleanup())

    assert [tool.name for tool in tools] == ["stdio_tool", "http_tool", "sse_tool"]
    assert remote_checks == [
        ("http", "https://mcp.example.com/http"),
        ("sse", "https://mcp.example.com/sse"),
    ]
    assert captured["connections"] == {
        "stdio": {
            "command": "node",
            "args": ["server.js"],
            "env": {"A": "B"},
            "transport": "stdio",
        },
        "http": {
            "transport": "streamable_http",
            "url": "https://mcp.example.com/http",
            "headers": {"Authorization": "Bearer token"},
        },
        "sse": {"transport": "sse", "url": "https://mcp.example.com/sse"},
    }
    assert [(info.name, info.transport) for info in server_infos] == [
        ("stdio", "stdio"),
        ("http", "http"),
        ("sse", "sse"),
    ]
    assert [(tool.name, tool.description) for tool in server_infos[0].tools] == [
        ("stdio_tool", "")
    ]


def test_load_tools_from_config_reports_preflight_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_mcp_adapter_modules(monkeypatch)

    def fail_stdio(_server_name: str, _server_config: dict) -> None:
        raise RuntimeError("stdio failed")

    async def fail_remote(_server_name: str, _server_config: dict) -> None:
        raise RuntimeError("remote failed")

    monkeypatch.setattr(mcp_tools, "_check_stdio_server", fail_stdio)
    monkeypatch.setattr(mcp_tools, "_check_remote_server", fail_remote)

    config = {
        "mcpServers": {
            "stdio": {"command": "node"},
            "remote": {"type": "http", "url": "https://mcp.example.com"},
        }
    }

    with pytest.raises(
        RuntimeError,
        match=r"(?s)Pre-flight.*stdio failed.*remote failed",
    ):
        asyncio.run(_load_tools_from_config(config))


def test_load_tools_from_config_wraps_client_and_tool_load_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {"mcpServers": {"stdio": {"command": "node"}}}
    monkeypatch.setattr(mcp_tools, "_check_stdio_server", lambda *_args: None)

    _install_fake_mcp_adapter_modules(monkeypatch, client_raises=True)
    with pytest.raises(RuntimeError, match="Failed to initialize MCP client"):
        asyncio.run(_load_tools_from_config(config))

    _install_fake_mcp_adapter_modules(monkeypatch, load_raises=True)
    with pytest.raises(RuntimeError, match="Failed to load tools from MCP server"):
        asyncio.run(_load_tools_from_config(config))


def test_get_mcp_tools_loads_config_and_delegates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps({"mcpServers": {"stdio": {"command": "node"}}}),
        encoding="utf-8",
    )
    captured: list[dict] = []

    async def fake_load_tools(config: dict):
        captured.append(config)
        return ["tool"], "manager", ["server"]

    monkeypatch.setattr(mcp_tools, "_load_tools_from_config", fake_load_tools)

    assert asyncio.run(get_mcp_tools(str(path))) == (["tool"], "manager", ["server"])
    assert captured == [{"mcpServers": {"stdio": {"command": "node"}}}]


def test_untrusted_project_remote_mcp_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote": {
                        "type": "http",
                        "url": "https://mcp.example.com",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    project_context = ProjectContext(user_cwd=tmp_path, project_root=tmp_path)

    tools, manager, server_info = asyncio.run(
        resolve_and_load_mcp_tools(project_context=project_context)
    )

    assert tools == []
    assert manager is None
    assert server_info == []


def test_resolve_and_load_mcp_tools_handles_flags_and_explicit_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit.json"
    explicit.write_text(
        json.dumps({"mcpServers": {"explicit": {"command": "node"}}}),
        encoding="utf-8",
    )
    captured: list[dict] = []

    async def fake_load(config: dict):
        captured.append(config)
        return ["tool"], "manager", ["server"]

    monkeypatch.setattr(mcp_tools, "discover_mcp_configs", lambda **_: [])
    monkeypatch.setattr(mcp_tools, "_load_tools_from_config", fake_load)

    assert asyncio.run(resolve_and_load_mcp_tools(no_mcp=True)) == ([], None, [])
    assert asyncio.run(resolve_and_load_mcp_tools()) == ([], None, [])
    assert asyncio.run(
        resolve_and_load_mcp_tools(explicit_config_path=str(explicit))
    ) == (["tool"], "manager", ["server"])
    assert captured == [{"mcpServers": {"explicit": {"command": "node"}}}]


def test_resolve_and_load_mcp_tools_handles_discovery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_discovery(**_kwargs):
        raise OSError("discovery failed")

    monkeypatch.setattr(mcp_tools, "discover_mcp_configs", fail_discovery)

    assert asyncio.run(resolve_and_load_mcp_tools()) == ([], None, [])


def test_resolve_and_load_mcp_tools_skips_invalid_and_empty_project_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = Path("/tmp/invalid-project-mcp.json")
    empty = Path("/tmp/empty-project-mcp.json")
    monkeypatch.setattr(mcp_tools, "discover_mcp_configs", lambda **_: [invalid, empty])
    monkeypatch.setattr(
        mcp_tools,
        "classify_discovered_configs",
        lambda _paths: ([], [invalid, empty]),
    )
    monkeypatch.setattr(
        mcp_tools,
        "load_mcp_config_lenient",
        lambda path: (
            None if path == invalid else {"mcpServers": {"ignored": "not-a-dict"}}
        ),
    )

    assert asyncio.run(resolve_and_load_mcp_tools()) == ([], None, [])


def test_resolve_and_load_mcp_tools_logs_explicit_project_deny(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_config = Path("/tmp/project-mcp.json")
    monkeypatch.setattr(mcp_tools, "discover_mcp_configs", lambda **_: [project_config])
    monkeypatch.setattr(
        mcp_tools,
        "classify_discovered_configs",
        lambda _paths: ([], [project_config]),
    )
    monkeypatch.setattr(
        mcp_tools,
        "load_mcp_config_lenient",
        lambda _path: {"mcpServers": {"remote": {"type": "http", "url": "https://x"}}},
    )

    assert asyncio.run(resolve_and_load_mcp_tools(trust_project_mcp=False)) == (
        [],
        None,
        [],
    )


def test_resolve_and_load_mcp_tools_keeps_non_empty_filtered_project_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_config = Path("/tmp/project-mcp.json")
    cfg = {"mcpServers": {"remote": {"type": "http", "url": "https://x"}}}
    filtered = {"mcpServers": {"safe": {"command": "node"}}}
    captured: list[dict] = []

    async def fake_load(config: dict):
        captured.append(config)
        return [], None, []

    monkeypatch.setattr(mcp_tools, "discover_mcp_configs", lambda **_: [project_config])
    monkeypatch.setattr(
        mcp_tools,
        "classify_discovered_configs",
        lambda _paths: ([], [project_config]),
    )
    monkeypatch.setattr(mcp_tools, "load_mcp_config_lenient", lambda _path: cfg)
    monkeypatch.setattr(mcp_tools, "_empty_project_config", lambda _cfg: filtered)
    monkeypatch.setattr(mcp_tools, "_load_tools_from_config", fake_load)

    assert asyncio.run(resolve_and_load_mcp_tools(trust_project_mcp=False)) == (
        [],
        None,
        [],
    )
    assert captured == [filtered]


def test_resolve_and_load_mcp_tools_keeps_non_empty_untrusted_project_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.mcp import trust as mcp_trust

    project_config = Path("/tmp/project-mcp.json")
    cfg = {"mcpServers": {"remote": {"type": "http", "url": "https://x"}}}
    filtered = {"mcpServers": {"safe": {"command": "node"}}}
    captured: list[dict] = []

    async def fake_load(config: dict):
        captured.append(config)
        return [], None, []

    monkeypatch.setattr(mcp_tools, "discover_mcp_configs", lambda **_: [project_config])
    monkeypatch.setattr(
        mcp_tools,
        "classify_discovered_configs",
        lambda _paths: ([], [project_config]),
    )
    monkeypatch.setattr(mcp_tools, "load_mcp_config_lenient", lambda _path: cfg)
    monkeypatch.setattr(mcp_tools, "_empty_project_config", lambda _cfg: filtered)
    monkeypatch.setattr(mcp_tools, "_load_tools_from_config", fake_load)
    monkeypatch.setattr(mcp_trust, "compute_config_fingerprint", lambda _paths: "fp")
    monkeypatch.setattr(mcp_trust, "is_project_mcp_trusted", lambda *_args: False)

    assert asyncio.run(resolve_and_load_mcp_tools()) == ([], None, [])
    assert captured == [filtered]


def test_resolve_and_load_mcp_tools_uses_persisted_project_trust(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from invincat_cli.mcp import trust as mcp_trust

    project_config = tmp_path / ".mcp.json"
    project_config.write_text(
        json.dumps({"mcpServers": {"trusted": {"command": "node"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_trust, "compute_config_fingerprint", lambda _paths: "fp")
    monkeypatch.setattr(
        mcp_trust,
        "is_project_mcp_trusted",
        lambda project_root, fingerprint: (
            project_root == str(tmp_path) and fingerprint == "fp"
        ),
    )
    captured: list[dict] = []

    async def fake_load(config: dict):
        captured.append(config)
        return [], None, []

    monkeypatch.setattr(mcp_tools, "_load_tools_from_config", fake_load)

    assert asyncio.run(
        resolve_and_load_mcp_tools(
            project_context=ProjectContext(
                user_cwd=tmp_path,
                project_root=tmp_path,
            )
        )
    ) == ([], None, [])
    assert captured == [{"mcpServers": {"trusted": {"command": "node"}}}]


def test_resolve_and_load_mcp_tools_trusts_project_configs_when_allowed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    user_cfg = home / ".invincat" / ".mcp.json"
    user_cfg.parent.mkdir()
    user_cfg.write_text(
        json.dumps({"mcpServers": {"shared": {"command": "user"}}}),
        encoding="utf-8",
    )
    project_cfg = project / ".mcp.json"
    project_cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared": {"command": "project"},
                    "project-only": {"type": "http", "url": "https://mcp.example.com"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    captured: list[dict] = []

    async def fake_load(config: dict):
        captured.append(config)
        return [], None, []

    monkeypatch.setattr(mcp_tools, "_load_tools_from_config", fake_load)
    context = ProjectContext(user_cwd=project, project_root=project)

    asyncio.run(
        resolve_and_load_mcp_tools(
            trust_project_mcp=True,
            project_context=context,
        )
    )

    assert captured == [
        {
            "mcpServers": {
                "shared": {"command": "project"},
                "project-only": {"type": "http", "url": "https://mcp.example.com"},
            }
        }
    ]


def test_resolve_and_load_mcp_tools_wraps_invalid_merged_config(monkeypatch) -> None:
    config_path = Path("/tmp/user-mcp.json")
    monkeypatch.setattr(mcp_tools, "discover_mcp_configs", lambda **_: [config_path])
    monkeypatch.setattr(
        mcp_tools,
        "classify_discovered_configs",
        lambda _paths: ([config_path], []),
    )
    monkeypatch.setattr(
        mcp_tools,
        "load_mcp_config_lenient",
        lambda _path: {"mcpServers": {"ok": {"command": "node"}}},
    )
    monkeypatch.setattr(
        mcp_tools,
        "merge_mcp_configs",
        lambda _configs: {"mcpServers": {"bad": {"type": "websocket"}}},
    )

    with pytest.raises(RuntimeError, match="Invalid MCP server configuration"):
        asyncio.run(resolve_and_load_mcp_tools(explicit_config_path=None, no_mcp=False))


def test_resolve_and_load_mcp_tools_returns_empty_when_merge_has_no_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = Path("/tmp/user-mcp.json")
    monkeypatch.setattr(mcp_tools, "discover_mcp_configs", lambda **_: [config_path])
    monkeypatch.setattr(
        mcp_tools,
        "classify_discovered_configs",
        lambda _paths: ([config_path], []),
    )
    monkeypatch.setattr(
        mcp_tools,
        "load_mcp_config_lenient",
        lambda _path: {"mcpServers": {"ok": {"command": "node"}}},
    )
    monkeypatch.setattr(mcp_tools, "merge_mcp_configs", lambda _configs: {})

    assert asyncio.run(resolve_and_load_mcp_tools()) == ([], None, [])
