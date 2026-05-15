"""Tests for CLI server subprocess configuration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from invincat_cli.core.env_vars import SERVER_ENV_PREFIX
from invincat_cli.project_utils import ProjectContext
from invincat_cli.server.config import ServerConfig


def test_server_config_round_trips_scheduler_cwd_scope(monkeypatch) -> None:
    for key in list(os.environ):
        if key.startswith(SERVER_ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)

    config = ServerConfig(scheduler_cwd_scope="/tmp/project-a")
    for suffix, value in config.to_env().items():
        if value is not None:
            monkeypatch.setenv(f"{SERVER_ENV_PREFIX}{suffix}", value)

    loaded = ServerConfig.from_env()

    assert loaded.scheduler_cwd_scope == "/tmp/project-a"


def test_server_config_round_trips_all_serialized_fields(monkeypatch) -> None:
    for key in list(os.environ):
        if key.startswith(SERVER_ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)

    config = ServerConfig(
        model="openai:gpt-test",
        model_params={"temperature": 0.2},
        assistant_id="assistant-1",
        system_prompt="Be direct.",
        auto_approve=True,
        interrupt_shell_only=True,
        shell_allow_list=["pytest", "ruff"],
        interactive=False,
        enable_shell=False,
        enable_ask_user=True,
        enable_memory=False,
        enable_skills=False,
        sandbox_type="docker",
        sandbox_id="sandbox-1",
        sandbox_setup="/tmp/setup.sh",
        cwd="/tmp/work",
        project_root="/tmp/work/project",
        scheduler_cwd_scope="/tmp/work/project",
        mcp_config_path="/tmp/work/mcp.json",
        no_mcp=True,
        trust_project_mcp=False,
    )
    for suffix, value in config.to_env().items():
        if value is not None:
            monkeypatch.setenv(f"{SERVER_ENV_PREFIX}{suffix}", value)

    loaded = ServerConfig.from_env()

    assert loaded == config


def test_server_config_rejects_empty_shell_allow_list() -> None:
    with pytest.raises(ValueError, match="shell_allow_list"):
        ServerConfig(shell_allow_list=[])


def test_server_config_normalizes_none_sandbox_type() -> None:
    assert ServerConfig(sandbox_type="none").sandbox_type is None


def test_server_config_from_env_rejects_invalid_json(monkeypatch) -> None:
    for key in list(os.environ):
        if key.startswith(SERVER_ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(f"{SERVER_ENV_PREFIX}MODEL_PARAMS", "{not json")

    with pytest.raises(ValueError, match="MODEL_PARAMS"):
        ServerConfig.from_env()


def test_server_config_from_env_uses_defaults_when_values_are_absent(
    monkeypatch,
) -> None:
    for key in list(os.environ):
        if key.startswith(SERVER_ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)

    loaded = ServerConfig.from_env()

    assert loaded.assistant_id == "agent"
    assert loaded.interactive is True
    assert loaded.enable_shell is True
    assert loaded.enable_memory is True
    assert loaded.enable_skills is True
    assert loaded.auto_approve is False
    assert loaded.trust_project_mcp is None


def test_server_config_from_cli_args_resolves_paths_against_user_cwd(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    user_cwd = project_root / "nested"
    user_cwd.mkdir(parents=True)
    context = ProjectContext(user_cwd=user_cwd.resolve(), project_root=project_root)

    config = ServerConfig.from_cli_args(
        project_context=context,
        model_name="openai:gpt-test",
        model_params={"temperature": 0},
        assistant_id="assistant-1",
        auto_approve=True,
        interrupt_shell_only=True,
        shell_allow_list=["pytest"],
        sandbox_type="none",
        sandbox_id="sandbox-1",
        sandbox_setup="scripts/setup.sh",
        enable_shell=True,
        enable_ask_user=True,
        mcp_config_path="config/mcp.json",
        no_mcp=False,
        trust_project_mcp=True,
        interactive=False,
        scheduler_cwd_scope="reports",
    )

    assert config.sandbox_type is None
    assert config.cwd == str(user_cwd.resolve())
    assert config.project_root == str(project_root)
    assert config.mcp_config_path == str((user_cwd / "config/mcp.json").resolve())
    assert config.sandbox_setup == str((user_cwd / "scripts/setup.sh").resolve())
    assert config.scheduler_cwd_scope == str((user_cwd / "reports").resolve())
    assert config.model == "openai:gpt-test"
    assert config.model_params == {"temperature": 0}


def test_server_config_from_cli_args_resolves_paths_without_project_context(
    tmp_path: Path,
) -> None:
    setup = tmp_path / "setup.sh"
    mcp = tmp_path / "mcp.json"

    config = ServerConfig.from_cli_args(
        project_context=None,
        model_name=None,
        model_params=None,
        assistant_id="agent",
        auto_approve=False,
        sandbox_id=None,
        sandbox_setup=str(setup),
        enable_shell=False,
        enable_ask_user=False,
        mcp_config_path=str(mcp),
        no_mcp=False,
        trust_project_mcp=None,
        interactive=True,
        scheduler_cwd_scope=str(tmp_path),
    )

    assert config.sandbox_setup == str(setup.resolve())
    assert config.mcp_config_path == str(mcp.resolve())
    assert config.scheduler_cwd_scope == str(tmp_path.resolve())


def test_server_config_from_cli_args_wraps_path_resolution_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_resolve(self: Path) -> Path:
        raise OSError(f"cannot resolve {self}")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    with pytest.raises(ValueError, match="Could not resolve MCP config path"):
        ServerConfig.from_cli_args(
            project_context=None,
            model_name=None,
            model_params=None,
            assistant_id="agent",
            auto_approve=False,
            sandbox_id=None,
            sandbox_setup=None,
            enable_shell=False,
            enable_ask_user=False,
            mcp_config_path="/tmp/mcp.json",
            no_mcp=False,
            trust_project_mcp=None,
            interactive=True,
        )


def test_server_config_from_cli_args_allows_missing_optional_paths() -> None:
    config = ServerConfig.from_cli_args(
        project_context=None,
        model_name=None,
        model_params=None,
        assistant_id="agent",
        auto_approve=False,
        sandbox_id=None,
        sandbox_setup=None,
        enable_shell=False,
        enable_ask_user=False,
        mcp_config_path=None,
        no_mcp=True,
        trust_project_mcp=None,
        interactive=True,
    )

    assert config.cwd is None
    assert config.project_root is None
    assert config.mcp_config_path is None
    assert config.sandbox_setup is None
    assert config.scheduler_cwd_scope is None
