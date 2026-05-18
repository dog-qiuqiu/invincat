"""Runtime metadata and skills-directory helpers for config."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _get_git_branch() -> str | None:
    """Return the current git branch name, or `None` if not in a repo."""
    import subprocess  # noqa: S404

    from invincat_cli import config as _config

    try:
        cwd = str(_config.Path.cwd())
    except OSError:
        _config.logger.debug(
            "Could not determine cwd for git branch lookup", exc_info=True
        )
        return None
    if cwd in _config._git_branch_cache:  # noqa: SLF001
        return _config._git_branch_cache[cwd]  # noqa: SLF001

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            branch = result.stdout.strip() or None
            _config._git_branch_cache[cwd] = branch  # noqa: SLF001
            return branch
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        _config.logger.debug("Could not determine git branch", exc_info=True)
    _config._git_branch_cache[cwd] = None  # noqa: SLF001
    return None


def build_stream_config(
    thread_id: str,
    assistant_id: str | None,
    *,
    sandbox_type: str | None = None,
) -> Any:
    """Build the LangGraph stream config dict."""
    import contextlib
    import importlib.metadata as importlib_metadata
    from datetime import UTC, datetime

    from invincat_cli import config as _config

    try:
        cwd = str(_config.Path.cwd())
    except OSError:
        _config.logger.warning("Could not determine working directory", exc_info=True)
        cwd = ""

    versions: dict[str, str] = {"invincat-cli": _config.__version__}
    with contextlib.suppress(importlib_metadata.PackageNotFoundError):
        versions["deepagents"] = importlib_metadata.version("deepagents")

    metadata: dict[str, Any] = {
        "versions": versions,
        "ls_integration": "invincat-cli",
    }
    from invincat_cli.core.env_vars import USER_ID

    user_id = _config.os.environ.get(USER_ID)
    if user_id:
        metadata["user_id"] = user_id
    if cwd:
        metadata["cwd"] = cwd
    if assistant_id:
        metadata.update(
            {
                "assistant_id": assistant_id,
                "agent_name": assistant_id,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
    branch = _config._get_git_branch()  # noqa: SLF001
    if branch:
        metadata["git_branch"] = branch
    if sandbox_type and sandbox_type != "none":
        metadata["sandbox_type"] = sandbox_type
    return {
        "configurable": {"thread_id": thread_id},
        "metadata": metadata,
    }


def _read_config_toml_skills_dirs() -> list[str] | None:
    """Read `[skills].extra_allowed_dirs` from `~/.invincat/config.toml`."""
    import tomllib

    from invincat_cli import config as _config
    from invincat_cli.model_config import DEFAULT_CONFIG_PATH

    try:
        with DEFAULT_CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return None
    except (PermissionError, OSError, tomllib.TOMLDecodeError):
        _config.logger.warning(
            "Could not read skills config from %s",
            DEFAULT_CONFIG_PATH,
            exc_info=True,
        )
        return None

    skills_section = data.get("skills", {})
    dirs = skills_section.get("extra_allowed_dirs")
    if isinstance(dirs, list):
        return dirs
    return None


def _parse_extra_skills_dirs(
    env_raw: str | None,
    config_toml_dirs: list[str] | None = None,
) -> list[Path] | None:
    """Merge extra skill directories from env var and config.toml."""
    from invincat_cli import config as _config

    if env_raw:
        dirs = [
            _config.Path(p.strip()).expanduser().resolve()
            for p in env_raw.split(":")
            if p.strip()
        ]
        return dirs or None

    if config_toml_dirs:
        dirs = [
            _config.Path(p).expanduser().resolve()
            for p in config_toml_dirs
            if isinstance(p, str) and p.strip()
        ]
        return dirs or None

    return None
