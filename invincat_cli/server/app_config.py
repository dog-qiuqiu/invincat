"""Config, command, and environment builders for the app server."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def generate_langgraph_json(
    output_dir: str | Path,
    *,
    graph_ref: str = "./server_graph.py:graph",
    env_file: str | None = None,
    checkpointer_path: str | None = None,
) -> Path:
    """Generate a `langgraph.json` config file for `langgraph dev`."""
    config: dict[str, Any] = {
        "dependencies": ["."],
        "graphs": {
            "agent": graph_ref,
        },
    }
    if env_file:
        config["env"] = env_file
    if checkpointer_path:
        config["checkpointer"] = {"path": checkpointer_path}

    output_path = Path(output_dir) / "langgraph.json"
    output_path.write_text(json.dumps(config, indent=2))
    return output_path


def build_server_cmd(config_path: Path, *, host: str, port: int) -> list[str]:
    """Build the `langgraph dev` command line."""
    return [
        sys.executable,
        "-m",
        "langgraph_cli",
        "dev",
        "--host",
        host,
        "--port",
        str(port),
        "--no-browser",
        "--no-reload",
        "--config",
        str(config_path),
    ]


def build_server_env(config_path: Path | None = None) -> dict[str, str]:
    """Build the environment dict for the server subprocess."""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["LANGGRAPH_AUTH_TYPE"] = "noop"

    if config_path is not None and config_path.exists():
        try:
            config_data = json.loads(config_path.read_text())
            checkpointer = config_data.get("checkpointer")
            if checkpointer:
                env["LANGGRAPH_CHECKPOINTER"] = json.dumps(checkpointer)
        except Exception:
            pass

    for key in (
        "LANGGRAPH_AUTH",
        "LANGGRAPH_CLOUD_LICENSE_KEY",
        "LANGSMITH_CONTROL_PLANE_API_KEY",
        "LANGSMITH_TENANT_ID",
    ):
        env.pop(key, None)
    return env
