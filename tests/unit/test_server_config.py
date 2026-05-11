"""Tests for CLI server subprocess configuration."""

from __future__ import annotations

import os

from invincat_cli.core.env_vars import SERVER_ENV_PREFIX
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
