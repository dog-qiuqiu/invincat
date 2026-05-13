"""Unit tests for MCP configuration trust handling."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from invincat_cli.mcp.tools import extract_server_summaries, resolve_and_load_mcp_tools
from invincat_cli.project_utils import ProjectContext


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
