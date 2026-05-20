from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from invincat_cli import agent as agent_mod
from invincat_cli.agent import (
    MemoryFileGuardMiddleware,
    ShellAllowListMiddleware,
    _add_interrupt_on,
    _format_copy_file_description,
    _format_delete_file_description,
    _format_edit_file_description,
    _format_execute_description,
    _format_fetch_url_description,
    _format_mkdir_description,
    _format_move_file_description,
    _format_task_description,
    _format_web_search_description,
    _format_write_file_description,
    _path_targets_memory_file,
    build_model_identity_section,
    load_async_subagents,
)
from invincat_cli.mcp.tools import MCPServerInfo, MCPToolInfo


def _request(
    name: str, args: dict[str, object], *, call_id: str = "call-1"
) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": name, "args": args, "id": call_id})


def _tool_call(name: str, args: dict[str, object]) -> dict[str, object]:
    return {"name": name, "args": args, "id": "call-1"}


def test_shell_allow_list_middleware_rejects_and_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "invincat_cli.config.is_shell_command_allowed",
        lambda command, allow_list, cwd=None: command.startswith("ls"),
    )
    middleware = ShellAllowListMiddleware(["ls"], cwd="/tmp")

    rejected = middleware.wrap_tool_call(
        _request("shell", {"command": "rm -rf /"}),
        lambda _request: "handled",
    )
    assert rejected.status == "error"
    assert rejected.tool_call_id == "call-1"
    assert "Allowed commands: ls" in rejected.content

    assert (
        middleware.wrap_tool_call(
            _request("shell", {"command": "ls -la"}),
            lambda _request: "handled",
        )
        == "handled"
    )
    assert (
        middleware.wrap_tool_call(
            _request("read_file", {"path": "x"}),
            lambda _request: "handled",
        )
        == "handled"
    )

    async def handler(_request: object) -> str:
        return "async handled"

    assert (
        asyncio.run(
            middleware.awrap_tool_call(_request("shell", {"command": "ls"}), handler)
        )
        == "async handled"
    )

    async_rejected = asyncio.run(
        middleware.awrap_tool_call(
            _request("shell", {"command": "cat secret"}), handler
        )
    )
    assert async_rejected.status == "error"


def test_shell_allow_list_rejects_invalid_configuration() -> None:
    from invincat_cli.config import SHELL_ALLOW_ALL

    with pytest.raises(ValueError, match="must not be empty"):
        ShellAllowListMiddleware([])

    with pytest.raises(TypeError, match="SHELL_ALLOW_ALL"):
        ShellAllowListMiddleware(SHELL_ALLOW_ALL)  # type: ignore[arg-type]


def test_memory_file_guard_blocks_file_and_shell_access() -> None:
    guard = MemoryFileGuardMiddleware()
    assert _path_targets_memory_file("/tmp/memory_user.json")
    assert not _path_targets_memory_file("/tmp/notes.json")

    for name, args in [
        ("read_file", {"path": "/tmp/memory_project.json"}),
        ("write_file", {"file_path": "memory_user.json"}),
        ("edit_file", {"path": "./nested/memory_project.json"}),
        ("shell", {"command": "cat memory_user.json"}),
    ]:
        result = guard.wrap_tool_call(_request(name, args), lambda _request: "handled")
        assert result.status == "error"
        assert "Access denied" in result.content

    assert (
        guard.wrap_tool_call(
            _request("read_file", {"path": "README.md"}),
            lambda _request: "handled",
        )
        == "handled"
    )

    async def handler(_request: object) -> str:
        return "async handled"

    assert (
        asyncio.run(
            guard.awrap_tool_call(_request("write_file", {"path": "ok.txt"}), handler)
        )
        == "async handled"
    )

    async_rejected = asyncio.run(
        guard.awrap_tool_call(
            _request("write_file", {"path": "memory_user.json"}), handler
        )
    )
    assert async_rejected.status == "error"


def test_load_async_subagents_validates_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_mod.Path, "home", lambda: tmp_path)
    assert load_async_subagents() == []

    missing = tmp_path / "missing.toml"
    assert load_async_subagents(missing) == []

    no_section = tmp_path / "no-section.toml"
    no_section.write_text("name = 'agent'\n", encoding="utf-8")
    assert load_async_subagents(no_section) == []

    non_table = tmp_path / "non-table.toml"
    non_table.write_text(
        """
[async_subagents]
notatable = "value"
""",
        encoding="utf-8",
    )
    assert load_async_subagents(non_table) == []

    config = tmp_path / "config.toml"
    config.write_text(
        """
[async_subagents.researcher]
description = "Research"
graph_id = "graph"
url = "https://example.com"
headers = { Authorization = "Bearer token" }

[async_subagents.invalid]
description = "Missing graph"

[async_subagents.notatable]
value = "still a table but missing fields"
""",
        encoding="utf-8",
    )
    assert load_async_subagents(config) == [
        {
            "name": "researcher",
            "description": "Research",
            "graph_id": "graph",
            "url": "https://example.com",
            "headers": {"Authorization": "Bearer token"},
        }
    ]

    bad = tmp_path / "bad.toml"
    bad.write_text("[async_subagents", encoding="utf-8")
    printed: list[str] = []
    monkeypatch.setattr(
        agent_mod.console,
        "print",
        lambda *args, **_kwargs: printed.append(" ".join(map(str, args))),
    )
    assert load_async_subagents(bad) == []
    assert any("Could not read async subagents" in line for line in printed)


def test_model_identity_section_formats_context_and_modalities() -> None:
    assert build_model_identity_section(None) == ""
    section = build_model_identity_section(
        "gpt-test",
        provider="openai",
        context_limit=128000,
        unsupported_modalities=frozenset({"audio", "video", "image"}),
    )

    assert "model `gpt-test` (provider: openai)" in section
    assert "128,000 tokens" in section
    assert "Audio, image, and video input may not be available" in section

    two_items = build_model_identity_section(
        "gpt-test",
        unsupported_modalities=frozenset({"audio", "video"}),
    )
    assert "Audio and video input may not be available" in two_items


def test_tool_description_formatters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    existing = tmp_path / "exists.txt"
    existing.write_text("old", encoding="utf-8")
    monkeypatch.setattr(
        agent_mod,
        "get_glyphs",
        lambda: SimpleNamespace(warning="!", box_horizontal="-"),
    )
    monkeypatch.setattr(
        agent_mod,
        "get_server_project_context",
        lambda: SimpleNamespace(user_cwd=Path("/repo")),
    )

    assert (
        _format_write_file_description(
            _tool_call("write_file", {"file_path": str(existing)}), {}, None
        )
        == "Action: Overwrite file"
    )
    assert (
        _format_write_file_description(
            _tool_call("write_file", {"file_path": str(tmp_path / "new.txt")}), {}, None
        )
        == "Action: Create file"
    )
    assert (
        _format_edit_file_description(
            _tool_call("edit_file", {"replace_all": True}), {}, None
        )
        == "Action: Replace text (all occurrences)"
    )
    assert (
        _format_edit_file_description(_tool_call("edit_file", {}), {}, None)
        == "Action: Replace text (single occurrence)"
    )
    assert "Query: python" in _format_web_search_description(
        _tool_call("web_search", {"query": "python", "max_results": 2}),
        {},
        None,
    )
    fetch_description = _format_fetch_url_description(
        _tool_call("fetch_url", {"url": "https://раypal.com", "timeout": 7}),
        {},
        None,
    )
    assert "URL: https://раypal.com" in fetch_description
    assert "Timeout: 7s" in fetch_description
    assert "URL warning" in fetch_description
    punycode_description = _format_fetch_url_description(
        _tool_call("fetch_url", {"url": "https://xn--80ak6aa92e.com"}),
        {},
        None,
    )
    assert "Decoded domain:" in punycode_description

    long_description = "x" * 520
    task_description = _format_task_description(
        _tool_call(
            "task", {"description": long_description, "subagent_type": "worker"}
        ),
        {},
        None,
    )
    assert "Subagent Type: worker" in task_description
    assert "x" * 500 in task_description
    assert task_description.endswith("...")

    execute_description = _format_execute_description(
        _tool_call("execute", {"command": "echo safe\u202ehidden"}),
        {},
        None,
    )
    assert "Execute Command: echo safehidden" in execute_description
    assert "Working Directory: /repo" in execute_description
    assert "Hidden Unicode detected" in execute_description

    long_execute_description = _format_execute_description(
        _tool_call("execute", {"command": "echo " + ("x" * 240) + "\u202ehidden"}),
        {},
        None,
    )
    assert "Raw: echo " in long_execute_description
    assert long_execute_description.endswith("...")


def test_add_interrupt_on_includes_mcp_and_compact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = MCPServerInfo(
        name="server",
        transport="stdio",
        tools=[MCPToolInfo(name="mcp_tool", description="desc")],
    )

    interrupt_map = _add_interrupt_on([server])

    for name in [
        "execute",
        "write_file",
        "edit_file",
        "mkdir",
        "move_file",
        "copy_file",
        "delete_file",
        "web_search",
        "fetch_url",
        "task",
        "launch_async_subagent",
        "mcp_tool",
        "compact_conversation",
    ]:
        assert interrupt_map[name]["allowed_decisions"] == ["approve", "reject"]

    monkeypatch.setattr(agent_mod, "REQUIRE_COMPACT_TOOL_APPROVAL", False)
    assert "compact_conversation" not in _add_interrupt_on()


def test_file_management_approval_descriptions() -> None:
    assert "Create directory" in _format_mkdir_description(
        _tool_call("mkdir", {"path": "docs/archive"}),
        {},
        None,
    )
    assert "Source: a.md" in _format_move_file_description(
        _tool_call(
            "move_file",
            {"source": "a.md", "destination": "docs/a.md", "overwrite": True},
        ),
        {},
        None,
    )
    assert "Overwrite: True" in _format_copy_file_description(
        _tool_call(
            "copy_file",
            {"source": "a.md", "destination": "docs/a.md", "overwrite": True},
        ),
        {},
        None,
    )
    assert "Move file to project trash" in _format_delete_file_description(
        _tool_call("delete_file", {"path": "old.md"}),
        {},
        None,
    )


class _FakeConsole:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, *args: Any, **_kwargs: Any) -> None:
        self.messages.append(" ".join(map(str, args)))


def test_list_agents_outputs_empty_json_and_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import invincat_cli.io.output as output_module

    writes: list[tuple[str, Any]] = []
    fake_console = _FakeConsole()
    fake_settings = SimpleNamespace(user_deepagents_dir=tmp_path / "agents")
    monkeypatch.setattr(agent_mod, "settings", fake_settings)
    monkeypatch.setattr(agent_mod, "console", fake_console)
    monkeypatch.setattr(
        output_module, "write_json", lambda name, data: writes.append((name, data))
    )

    agent_mod.list_agents(output_format="json")
    agent_mod.list_agents(output_format="text")

    assert writes == [("list", [])]
    assert any("No agents found" in message for message in fake_console.messages)


def test_list_agents_outputs_agent_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import invincat_cli.io.output as output_module

    agents_dir = tmp_path / "agents"
    default_agent = agents_dir / "agent"
    other_agent = agents_dir / "coder"
    (default_agent / "skills").mkdir(parents=True)
    (default_agent / "memory_user.json").write_text("{}", encoding="utf-8")
    other_agent.mkdir()
    (agents_dir / "README.txt").write_text("ignore", encoding="utf-8")
    writes: list[tuple[str, Any]] = []
    fake_console = _FakeConsole()
    monkeypatch.setattr(
        agent_mod, "settings", SimpleNamespace(user_deepagents_dir=agents_dir)
    )
    monkeypatch.setattr(agent_mod, "console", fake_console)
    monkeypatch.setattr(
        output_module, "write_json", lambda name, data: writes.append((name, data))
    )
    monkeypatch.setattr(agent_mod, "get_glyphs", lambda: SimpleNamespace(bullet="-"))

    agent_mod.list_agents(output_format="json")
    agent_mod.list_agents(output_format="text")

    assert writes[0][0] == "list"
    assert writes[0][1][0]["name"] == "agent"
    assert writes[0][1][0]["has_memory"] is True
    assert writes[0][1][0]["has_skills"] is True
    assert writes[0][1][0]["is_default"] is True
    assert any("coder" in message for message in fake_console.messages)


def test_get_system_prompt_modes_sandbox_and_cwd_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_settings = SimpleNamespace(
        model_name="gpt-test",
        model_provider="openai",
        model_context_limit=128000,
        model_unsupported_modalities=frozenset({"audio"}),
    )
    monkeypatch.setattr(agent_mod, "settings", fake_settings)
    monkeypatch.setattr(agent_mod, "get_default_working_dir", lambda _provider: "/work")

    sandbox_prompt = agent_mod.get_system_prompt(
        "agent",
        sandbox_type="modal",
        interactive=False,
    )
    assert "remote Linux sandbox" in sandbox_prompt
    assert "Do NOT ask clarifying questions" in sandbox_prompt
    assert "/work" in sandbox_prompt
    assert "Audio input may not be available" in sandbox_prompt

    explicit_cwd_prompt = agent_mod.get_system_prompt(
        "agent",
        interactive=True,
        cwd="/explicit/workdir",
    )
    assert "/explicit/workdir" in explicit_cwd_prompt

    monkeypatch.setattr(
        agent_mod.Path,
        "cwd",
        lambda: (_ for _ in ()).throw(OSError("cwd missing")),
    )
    local_prompt = agent_mod.get_system_prompt("agent", interactive=True)
    assert "interactive CLI" in local_prompt
    assert "Path Handling" in local_prompt


def test_get_system_prompt_logs_unreplaced_placeholders(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_settings = SimpleNamespace(
        model_name=None,
        model_provider=None,
        model_context_limit=None,
        model_unsupported_modalities=frozenset(),
    )
    template = (
        "{mode_description}\n{interactive_preamble}\n{ambiguity_guidance}\n"
        "{model_identity_section}\n{current_time_section}\n"
        "{working_dir_section}\n{skills_path}\n{unknown_placeholder}"
    )
    monkeypatch.setattr(agent_mod, "settings", fake_settings)
    monkeypatch.setattr(agent_mod.Path, "read_text", lambda _self: template)
    monkeypatch.setattr(agent_mod, "get_default_working_dir", lambda _provider: "/work")

    prompt = agent_mod.get_system_prompt("agent", sandbox_type="modal")

    assert "{unknown_placeholder}" in prompt
    assert "unreplaced placeholders" in caplog.text


def _install_create_cli_agent_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    base: Path | None = None,
) -> dict[str, Any]:
    events: dict[str, Any] = {"created_agents": []}

    class FakeSettings:
        user_langchain_project = "user-project"
        shell_allow_list = ["ls"]

        def __init__(self) -> None:
            self.base = base or Path("/tmp/invincat-agent-test")

        def ensure_agent_dir(self, assistant_id: str) -> Path:
            events.setdefault("ensure_agent", []).append(assistant_id)
            return self.base / assistant_id

        def ensure_user_skills_dir(self, assistant_id: str) -> Path:
            return self.base / assistant_id / "skills"

        def ensure_user_agent_skills_dir(self) -> Path:
            return self.base / ".agents" / "skills"

        def get_project_skills_dir(self) -> Path:
            return self.base / "project" / ".invincat" / "skills"

        def get_project_agent_skills_dir(self) -> Path:
            return self.base / "project" / ".agents" / "skills"

        def get_built_in_skills_dir(self) -> Path:
            return self.base / "built_in_skills"

        def get_user_claude_skills_dir(self) -> Path:
            return self.base / ".claude" / "skills"

        def get_project_claude_skills_dir(self) -> Path:
            return self.base / "project" / ".claude" / "skills"

        def get_agent_dir(self, assistant_id: str) -> Path:
            return self.base / assistant_id

    class FakeFilesystemBackend:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class FakeLocalShellBackend:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class FakeCompositeBackend:
        def __init__(self, *, default: Any, routes: dict[str, Any]) -> None:
            self.default = default
            self.routes = routes

    class FakeGraph:
        def __init__(self, kwargs: dict[str, Any]) -> None:
            self.kwargs = kwargs

        def with_config(self, config: Any) -> FakeGraph:
            self.config = config
            return self

    def fake_create_deep_agent(**kwargs: Any) -> FakeGraph:
        events["created_agents"].append(kwargs)
        return FakeGraph(kwargs)

    def marker(name: str):
        class Marker:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.name = name
                self.args = args
                self.kwargs = kwargs

        return Marker

    monkeypatch.setattr(agent_mod, "settings", FakeSettings())
    monkeypatch.setattr(agent_mod, "FilesystemBackend", FakeFilesystemBackend)
    monkeypatch.setattr(agent_mod, "LocalShellBackend", FakeLocalShellBackend)
    monkeypatch.setattr(agent_mod, "CompositeBackend", FakeCompositeBackend)
    monkeypatch.setattr(agent_mod, "_ExecutableBackend", FakeLocalShellBackend)
    monkeypatch.setattr(
        agent_mod, "_AsyncExecutableBackend", type("AsyncBackend", (), {})
    )
    monkeypatch.setattr(agent_mod, "LocalContextMiddleware", marker("local-context"))
    monkeypatch.setattr(agent_mod, "SkillsMiddleware", marker("skills"))
    monkeypatch.setattr(agent_mod, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(agent_mod.tempfile, "mkdtemp", lambda prefix: f"/tmp/{prefix}x")
    monkeypatch.setattr(agent_mod, "get_system_prompt", lambda **_kwargs: "prompt")

    module_fakes = {
        "invincat_cli.middleware.token_state": {"TokenStateMiddleware": marker("token")},
        "invincat_cli.middleware.micro_compact": {"MicroCompactMiddleware": marker("micro")},
        "invincat_cli.middleware.ask_user": {"AskUserMiddleware": marker("ask-user")},
        "invincat_cli.middleware.approve_plan": {"ApprovePlanMiddleware": marker("approve-plan")},
        "invincat_cli.wecom.file": {"WeComFileMiddleware": marker("wecom-file")},
        "invincat_cli.middleware.auto_memory": {
            "RefreshableMemoryMiddleware": marker("refreshable-memory")
        },
        "invincat_cli.memory.agent": {"MemoryAgentMiddleware": marker("memory-agent")},
        "invincat_cli.scheduler.store": {
            "SchedulerStore": marker("scheduler-store"),
            "CwdScopedSchedulerStore": marker("cwd-scheduler-store"),
        },
        "invincat_cli.scheduler.tool": {"ScheduleMiddleware": marker("schedule")},
        "deepagents.middleware.summarization": {
            "create_summarization_tool_middleware": lambda model, backend: (
                SimpleNamespace(name="summarization", model=model, backend=backend)
            )
        },
        "deepagents.middleware.subagents": {
            "GENERAL_PURPOSE_SUBAGENT": {
                "name": "general-purpose",
                "description": "General",
                "system_prompt": "Prompt",
            },
            "SubAgent": dict,
        },
    }
    for module_name, attrs in module_fakes.items():
        module = SimpleNamespace(**attrs)
        monkeypatch.setitem(sys.modules, module_name, module)

    return events


def test_create_cli_agent_local_shell_memory_skills_and_restrictive_shell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events = _install_create_cli_agent_fakes(monkeypatch, base=tmp_path)
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    project_context = SimpleNamespace(
        user_cwd=tmp_path,
        project_root=tmp_path,
        project_skills_dir=lambda: tmp_path / ".invincat" / "skills",
        project_agent_skills_dir=lambda: tmp_path / ".agents" / "skills",
    )
    extra = SimpleNamespace(name="extra")

    graph, backend = agent_mod.create_cli_agent(
        model="model",
        assistant_id="agent",
        tools=["tool"],
        interrupt_shell_only=True,
        shell_allow_list=["ls"],
        project_context=project_context,
        scheduler_cwd_scope=tmp_path,
        async_subagents=[{"name": "remote", "description": "Remote", "graph_id": "g"}],
        extra_middleware=[extra],
        approve_plan_system_prompt="planner prompt",
    )

    created = events["created_agents"][0]
    middleware_names = [
        getattr(item, "name", type(item).__name__) for item in created["middleware"]
    ]
    assert graph is not None
    assert backend.routes.keys() == {"/large_tool_results/", "/conversation_history/"}
    assert created["system_prompt"] == "prompt"
    assert created["tools"] == ["tool"]
    assert created["interrupt_on"] == {}
    assert created["subagents"][0]["name"] == "general-purpose"
    assert created["subagents"][1]["name"] == "explorer"
    assert created["subagents"][2]["name"] == "worker"
    assert created["subagents"][3]["name"] == "researcher"
    assert created["subagents"][4]["name"] == "document-worker"
    assert created["subagents"][5]["name"] == "remote"
    assert created["subagents"][1]["middleware"]
    assert created["subagents"][2]["middleware"]
    assert created["subagents"][3]["middleware"]
    assert created["subagents"][4]["middleware"]
    assert type(created["subagents"][1]["middleware"][0]).__name__ == (
        "ReadOnlySubagentToolMiddleware"
    )
    assert type(created["subagents"][3]["middleware"][0]).__name__ == (
        "ReadOnlySubagentToolMiddleware"
    )
    assert type(created["subagents"][2]["middleware"][0]).__name__ == (
        "WorkerShellGuardMiddleware"
    )
    assert type(created["subagents"][4]["middleware"][0]).__name__ == (
        "DocumentWorkerFileGuardMiddleware"
    )
    assert all(
        type(item).__name__ != "ReadOnlySubagentToolMiddleware"
        for item in created["subagents"][2]["middleware"]
    )
    assert all(
        type(item).__name__ != "ReadOnlySubagentToolMiddleware"
        for item in created["subagents"][4]["middleware"]
    )
    assert "refreshable-memory" in middleware_names
    assert "memory-agent" in middleware_names
    assert "skills" in middleware_names
    assert "local-context" in middleware_names
    assert extra in created["middleware"]
    skills = next(item for item in created["middleware"] if item.name == "skills")
    assert str(tmp_path / ".claude" / "skills") in skills.kwargs["sources"]
    assert events["ensure_agent"] == ["agent"]


def test_create_cli_agent_shell_allow_list_and_memory_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events = _install_create_cli_agent_fakes(monkeypatch, base=tmp_path)

    _graph, _backend = agent_mod.create_cli_agent(
        model="model",
        assistant_id="agent",
        cwd=tmp_path,
        interrupt_shell_only=True,
        enable_skills=False,
        enable_ask_user=False,
    )

    created = events["created_agents"][0]
    middleware_names = [
        getattr(item, "name", type(item).__name__) for item in created["middleware"]
    ]
    assert created["interrupt_on"] == {}
    assert created["subagents"][0]["name"] == "general-purpose"
    assert created["subagents"][1]["name"] == "explorer"
    assert created["subagents"][2]["name"] == "worker"
    assert created["subagents"][3]["name"] == "researcher"
    assert created["subagents"][4]["name"] == "document-worker"
    assert type(created["subagents"][1]["middleware"][0]).__name__ == (
        "ReadOnlySubagentToolMiddleware"
    )
    assert type(created["subagents"][3]["middleware"][0]).__name__ == (
        "ReadOnlySubagentToolMiddleware"
    )
    assert type(created["subagents"][2]["middleware"][0]).__name__ == (
        "WorkerShellGuardMiddleware"
    )
    assert type(created["subagents"][4]["middleware"][0]).__name__ == (
        "DocumentWorkerFileGuardMiddleware"
    )
    assert all(
        type(item).__name__ != "ReadOnlySubagentToolMiddleware"
        for item in created["subagents"][2]["middleware"]
    )
    assert all(
        type(item).__name__ != "ReadOnlySubagentToolMiddleware"
        for item in created["subagents"][4]["middleware"]
    )
    assert "refreshable-memory" in middleware_names
    memory = next(item for item in created["middleware"] if item.name == "memory-agent")
    assert memory.kwargs["memory_store_paths"]["project"] == str(
        tmp_path / ".invincat" / "memory_project.json"
    )

    agent_mod.settings.shell_allow_list = None
    agent_mod.create_cli_agent(
        model="model",
        assistant_id="agent",
        cwd=tmp_path,
        interrupt_shell_only=True,
        enable_memory=False,
        enable_skills=False,
        enable_ask_user=False,
    )
    fallback = events["created_agents"][1]
    assert fallback["subagents"][0]["name"] == "explorer"
    assert fallback["subagents"][1]["name"] == "worker"
    assert fallback["subagents"][2]["name"] == "researcher"
    assert fallback["subagents"][3]["name"] == "document-worker"
    assert fallback["interrupt_on"]["execute"]["allowed_decisions"] == [
        "approve",
        "reject",
    ]


def test_create_cli_agent_adds_builtin_subagents_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events = _install_create_cli_agent_fakes(monkeypatch, base=tmp_path)

    agent_mod.create_cli_agent(
        model="model",
        assistant_id="agent",
        cwd=tmp_path,
        enable_memory=False,
        enable_skills=False,
        enable_ask_user=False,
    )

    created = events["created_agents"][0]
    assert created["subagents"][0]["name"] == "explorer"
    assert "Read-only codebase exploration agent" in created["subagents"][0][
        "description"
    ]
    assert "must not edit files" in created["subagents"][0]["description"]
    assert "read-only" in created["subagents"][0]["system_prompt"]
    assert "repository" in created["subagents"][0]["system_prompt"]
    assert "exploration" in created["subagents"][0]["system_prompt"]
    assert "Do not edit" in created["subagents"][0]["system_prompt"]
    assert "Do not write findings to files" in created["subagents"][0][
        "system_prompt"
    ]
    assert "Use explorer for local repository structure" in created["subagents"][0][
        "system_prompt"
    ]
    assert created["subagents"][1]["name"] == "worker"
    assert "Implementation-focused agent" in created["subagents"][1]["description"]
    assert "avoid git commits or pushes" in created["subagents"][1]["description"]
    assert type(created["subagents"][1]["middleware"][0]).__name__ == (
        "WorkerShellGuardMiddleware"
    )
    assert "clearly scoped implementation task" in created["subagents"][1][
        "system_prompt"
    ]
    assert "When to use this subagent" in created["subagents"][1]["system_prompt"]
    assert "When not to use this subagent" in created["subagents"][1][
        "system_prompt"
    ]
    assert "You are not alone in the codebase" in created["subagents"][1][
        "system_prompt"
    ]
    assert "Do not revert" in created["subagents"][1]["system_prompt"]
    assert "Do not run git commit" in created["subagents"][1]["system_prompt"]
    assert "Scope confirmation" in created["subagents"][1]["system_prompt"]
    assert created["subagents"][2]["name"] == "researcher"
    assert "external source gathering" in created["subagents"][2]["description"]
    assert "prefer the explorer subagent" in created["subagents"][2]["description"]
    assert "Do not edit" in created["subagents"][2]["system_prompt"]
    assert "belongs to explorer" in created["subagents"][2]["system_prompt"]
    assert created["subagents"][3]["name"] == "document-worker"
    assert "complex document work" in created["subagents"][3]["description"]
    assert "Simple short Markdown/README questions" in created["subagents"][3][
        "description"
    ]
    assert "modify source/configuration files" in created["subagents"][3][
        "description"
    ]
    assert "Do not implement code" in created["subagents"][3]["system_prompt"]
    assert type(created["subagents"][3]["middleware"][0]).__name__ == (
        "DocumentWorkerFileGuardMiddleware"
    )
    assert "When to use this subagent" in created["subagents"][3]["system_prompt"]
    assert "When not to use this subagent" in created["subagents"][3][
        "system_prompt"
    ]
    assert "Source references" in created["subagents"][3]["system_prompt"]
    assert "Scope confirmation" in created["subagents"][3]["system_prompt"]


def test_create_cli_agent_skips_builtin_researcher_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events = _install_create_cli_agent_fakes(monkeypatch, base=tmp_path)

    agent_mod.create_cli_agent(
        model="model",
        assistant_id="agent",
        cwd=tmp_path,
        enable_memory=False,
        enable_skills=False,
        enable_ask_user=False,
        async_subagents=[
            {"name": "researcher", "description": "Remote", "graph_id": "g"}
        ],
    )

    created = events["created_agents"][0]
    assert len(created["subagents"]) == 4
    assert created["subagents"][0]["name"] == "explorer"
    assert created["subagents"][1]["name"] == "worker"
    assert created["subagents"][2]["name"] == "document-worker"
    assert created["subagents"][3] == {
        "name": "researcher",
        "description": "Remote",
        "graph_id": "g",
    }


def test_create_cli_agent_skips_builtin_document_worker_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events = _install_create_cli_agent_fakes(monkeypatch, base=tmp_path)

    agent_mod.create_cli_agent(
        model="model",
        assistant_id="agent",
        cwd=tmp_path,
        enable_memory=False,
        enable_skills=False,
        enable_ask_user=False,
        async_subagents=[
            {"name": "document-worker", "description": "Remote", "graph_id": "g"}
        ],
    )

    created = events["created_agents"][0]
    assert len(created["subagents"]) == 4
    assert created["subagents"][0]["name"] == "explorer"
    assert created["subagents"][1]["name"] == "worker"
    assert created["subagents"][2]["name"] == "researcher"
    assert created["subagents"][3] == {
        "name": "document-worker",
        "description": "Remote",
        "graph_id": "g",
    }


def test_create_cli_agent_skips_builtin_explorer_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events = _install_create_cli_agent_fakes(monkeypatch, base=tmp_path)

    agent_mod.create_cli_agent(
        model="model",
        assistant_id="agent",
        cwd=tmp_path,
        enable_memory=False,
        enable_skills=False,
        enable_ask_user=False,
        async_subagents=[
            {"name": "explorer", "description": "Remote", "graph_id": "g"}
        ],
    )

    created = events["created_agents"][0]
    assert len(created["subagents"]) == 4
    assert created["subagents"][0]["name"] == "worker"
    assert created["subagents"][1]["name"] == "researcher"
    assert created["subagents"][2]["name"] == "document-worker"
    assert created["subagents"][3] == {
        "name": "explorer",
        "description": "Remote",
        "graph_id": "g",
    }


def test_create_cli_agent_skips_builtin_worker_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events = _install_create_cli_agent_fakes(monkeypatch, base=tmp_path)

    agent_mod.create_cli_agent(
        model="model",
        assistant_id="agent",
        cwd=tmp_path,
        enable_memory=False,
        enable_skills=False,
        enable_ask_user=False,
        async_subagents=[
            {"name": "worker", "description": "Remote", "graph_id": "g"}
        ],
    )

    created = events["created_agents"][0]
    assert len(created["subagents"]) == 4
    assert created["subagents"][0]["name"] == "explorer"
    assert created["subagents"][1]["name"] == "researcher"
    assert created["subagents"][2]["name"] == "document-worker"
    assert created["subagents"][3] == {
        "name": "worker",
        "description": "Remote",
        "graph_id": "g",
    }


def test_create_cli_agent_filesystem_and_sandbox_modes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events = _install_create_cli_agent_fakes(monkeypatch)
    sandbox = SimpleNamespace(id="sandbox")

    _graph, local_backend = agent_mod.create_cli_agent(
        model="model",
        assistant_id="agent",
        cwd=tmp_path,
        enable_memory=False,
        enable_skills=False,
        enable_shell=False,
        enable_ask_user=False,
        auto_approve=True,
        system_prompt="custom prompt",
    )
    _graph2, sandbox_backend = agent_mod.create_cli_agent(
        model="model",
        assistant_id="agent",
        sandbox=sandbox,
        sandbox_type="modal",
        enable_memory=False,
        enable_skills=False,
        auto_approve=False,
    )

    assert local_backend.default.kwargs["root_dir"] == tmp_path
    assert events["created_agents"][0]["system_prompt"] == "custom prompt"
    assert events["created_agents"][0]["interrupt_on"] == {}
    local_middleware = [
        type(item).__name__ for item in events["created_agents"][0]["middleware"]
    ]
    assert "FileManagementMiddleware" in local_middleware
    assert sandbox_backend.default is sandbox
    assert sandbox_backend.routes == {}
    sandbox_middleware = [
        type(item).__name__ for item in events["created_agents"][1]["middleware"]
    ]
    assert "FileManagementMiddleware" not in sandbox_middleware
    assert events["created_agents"][1]["interrupt_on"]["execute"][
        "allowed_decisions"
    ] == [
        "approve",
        "reject",
    ]
