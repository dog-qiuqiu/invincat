from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

from invincat_cli.server.config import ServerConfig


def import_graph_module(monkeypatch: pytest.MonkeyPatch, config: ServerConfig):
    sys.modules.pop("invincat_cli.server.graph", None)

    import invincat_cli.agent as agent_module
    import invincat_cli.config as config_module
    import invincat_cli.project_utils as project_utils

    model_result = SimpleNamespace(
        model="model-object",
        apply_to_settings=lambda: None,
    )
    created: list[dict[str, object]] = []

    def create_cli_agent(**kwargs: object) -> tuple[str, object]:
        created.append(kwargs)
        return ("agent-graph", object())

    monkeypatch.setattr(ServerConfig, "from_env", classmethod(lambda cls: config))
    monkeypatch.setattr(project_utils, "get_server_project_context", lambda: None)
    monkeypatch.setattr(
        config_module, "create_model", lambda *_args, **_kwargs: model_result
    )
    monkeypatch.setattr(
        config_module,
        "settings",
        SimpleNamespace(
            has_tavily=False,
            reload_from_environment=lambda start_path: None,
        ),
    )
    monkeypatch.setattr(agent_module, "create_cli_agent", create_cli_agent)
    monkeypatch.setattr(agent_module, "load_async_subagents", lambda: [])

    module = importlib.import_module("invincat_cli.server.graph")
    return module, created


def test_module_import_builds_graph_with_stubbed_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ServerConfig(
        model="openai:gpt-test",
        assistant_id="assistant",
        no_mcp=True,
        cwd="/work",
        auto_approve=True,
        interrupt_shell_only=True,
        shell_allow_list=["echo"],
        enable_ask_user=True,
        scheduler_cwd_scope="project",
    )

    graph_module, created = import_graph_module(monkeypatch, config)

    assert graph_module.graph == "agent-graph"
    assert created[0]["assistant_id"] == "assistant"
    assert created[0]["model"] == "model-object"
    assert created[0]["tools"]
    assert created[0]["cwd"] == "/work"
    assert created[0]["auto_approve"] is True
    assert created[0]["interrupt_shell_only"] is True
    assert created[0]["shell_allow_list"] == ["echo"]
    assert created[0]["enable_ask_user"] is True
    assert created[0]["scheduler_cwd_scope"] == "project"


def test_build_tools_includes_fetch_web_and_skips_mcp_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_module, _created = import_graph_module(
        monkeypatch,
        ServerConfig(no_mcp=True),
    )

    import invincat_cli.config as config_module
    import invincat_cli.tools as tools_module

    monkeypatch.setattr(config_module.settings, "has_tavily", True)

    tools, mcp_info = graph_module._build_tools(ServerConfig(no_mcp=True), None)

    assert tools == [tools_module.fetch_url, tools_module.web_search]
    assert mcp_info is None


def test_build_tools_loads_mcp_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    graph_module, _created = import_graph_module(
        monkeypatch,
        ServerConfig(no_mcp=True),
    )

    async def resolve_and_load_mcp_tools(**kwargs: object):
        assert kwargs["explicit_config_path"] == "mcp.json"
        assert kwargs["no_mcp"] is False
        assert kwargs["trust_project_mcp"] is True
        return (["mcp-tool"], object(), ["server-info"])

    monkeypatch.setattr(
        "invincat_cli.mcp.tools.resolve_and_load_mcp_tools",
        resolve_and_load_mcp_tools,
    )
    import invincat_cli.config as config_module

    monkeypatch.setattr(config_module.settings, "has_tavily", False)

    tools, mcp_info = graph_module._build_tools(
        ServerConfig(
            mcp_config_path="mcp.json",
            no_mcp=False,
            trust_project_mcp=True,
        ),
        project_context=object(),
    )

    assert tools[-1] == "mcp-tool"
    assert mcp_info == ["server-info"]


@pytest.mark.parametrize("error", [FileNotFoundError("missing"), RuntimeError("boom")])
def test_build_tools_reraises_mcp_load_failures(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    graph_module, _created = import_graph_module(
        monkeypatch,
        ServerConfig(no_mcp=True),
    )

    async def resolve_and_load_mcp_tools(**_kwargs: object):
        raise error

    monkeypatch.setattr(
        "invincat_cli.mcp.tools.resolve_and_load_mcp_tools",
        resolve_and_load_mcp_tools,
    )

    with pytest.raises(type(error)):
        graph_module._build_tools(ServerConfig(mcp_config_path="bad.json"), None)


def test_make_graph_uses_project_context_and_async_subagents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_module, created = import_graph_module(
        monkeypatch,
        ServerConfig(no_mcp=True),
    )
    created.clear()

    import invincat_cli.agent as agent_module
    import invincat_cli.config as config_module

    context = SimpleNamespace(user_cwd="/project")
    reloads: list[str] = []

    monkeypatch.setattr(
        graph_module.ServerConfig,
        "from_env",
        classmethod(lambda cls: ServerConfig(model="openai:gpt", no_mcp=True)),
    )
    monkeypatch.setattr(graph_module, "get_server_project_context", lambda: context)
    monkeypatch.setattr(
        graph_module, "_build_tools", lambda _config, _ctx: (["tool"], ["mcp"])
    )
    monkeypatch.setattr(
        config_module.settings,
        "reload_from_environment",
        lambda *, start_path: reloads.append(start_path),
    )
    monkeypatch.setattr(agent_module, "load_async_subagents", lambda: ["subagent"])

    result = graph_module.make_graph()

    assert result == "agent-graph"
    assert reloads == ["/project"]
    assert created[0]["cwd"] == "/project"
    assert created[0]["project_context"] is context
    assert created[0]["mcp_server_info"] == ["mcp"]
    assert created[0]["async_subagents"] == ["subagent"]


def test_make_graph_keeps_sandbox_context_until_process_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_module, created = import_graph_module(
        monkeypatch,
        ServerConfig(no_mcp=True),
    )
    created.clear()
    graph_module._sandbox_cm = None
    graph_module._sandbox_backend = None

    entered: list[str] = []
    exited: list[tuple[object, object, object]] = []
    registered: list[object] = []

    class SandboxContext:
        def __enter__(self) -> str:
            entered.append("enter")
            return "sandbox-backend"

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            exited.append((exc_type, exc, tb))

    monkeypatch.setattr(
        graph_module.ServerConfig,
        "from_env",
        classmethod(
            lambda cls: ServerConfig(
                no_mcp=True,
                sandbox_type="local",
                sandbox_id="sid",
                sandbox_setup="setup.sh",
            )
        ),
    )
    monkeypatch.setattr(graph_module, "_build_tools", lambda _config, _ctx: ([], None))
    monkeypatch.setattr(
        "invincat_cli.integrations.sandbox_factory.create_sandbox",
        lambda sandbox_type, sandbox_id, setup_script_path: SandboxContext(),
    )
    monkeypatch.setattr(graph_module.atexit, "register", registered.append)

    result = graph_module.make_graph()

    assert result == "agent-graph"
    assert entered == ["enter"]
    assert graph_module._sandbox_backend == "sandbox-backend"
    assert created[0]["sandbox"] == "sandbox-backend"
    assert created[0]["sandbox_type"] == "local"
    assert registered

    registered[0]()
    assert exited == [(None, None, None)]


def test_make_graph_exits_for_sandbox_creation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_module, _created = import_graph_module(
        monkeypatch,
        ServerConfig(no_mcp=True),
    )

    monkeypatch.setattr(
        graph_module.ServerConfig,
        "from_env",
        classmethod(lambda cls: ServerConfig(no_mcp=True, sandbox_type="missing")),
    )
    monkeypatch.setattr(graph_module, "_build_tools", lambda _config, _ctx: ([], None))

    def fail_sandbox(*_args: object, **_kwargs: object) -> object:
        raise ImportError("missing provider")

    monkeypatch.setattr(
        "invincat_cli.integrations.sandbox_factory.create_sandbox",
        fail_sandbox,
    )

    with pytest.raises(SystemExit) as exc_info:
        graph_module.make_graph()

    assert exc_info.value.code == 1


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (NotImplementedError("unsupported"), "is not supported"),
        (ValueError("bad sandbox"), "Sandbox creation failed"),
    ],
)
def test_make_graph_exits_for_other_sandbox_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    message: str,
) -> None:
    graph_module, _created = import_graph_module(
        monkeypatch,
        ServerConfig(no_mcp=True),
    )

    monkeypatch.setattr(
        graph_module.ServerConfig,
        "from_env",
        classmethod(lambda cls: ServerConfig(no_mcp=True, sandbox_type="bad")),
    )
    monkeypatch.setattr(graph_module, "_build_tools", lambda _config, _ctx: ([], None))

    def fail_sandbox(*_args: object, **_kwargs: object) -> object:
        raise error

    monkeypatch.setattr(
        "invincat_cli.integrations.sandbox_factory.create_sandbox",
        fail_sandbox,
    )

    with pytest.raises(SystemExit) as exc_info:
        graph_module.make_graph()

    assert exc_info.value.code == 1
    assert message in capsys.readouterr().err


def test_module_import_exits_when_graph_initialization_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sys.modules.pop("invincat_cli.server.graph", None)
    monkeypatch.setattr(
        ServerConfig,
        "from_env",
        classmethod(lambda cls: (_ for _ in ()).throw(ValueError("bad env"))),
    )

    with pytest.raises(SystemExit) as exc_info:
        importlib.import_module("invincat_cli.server.graph")

    assert exc_info.value.code == 1
    assert "Failed to initialize server graph: bad env" in capsys.readouterr().err
