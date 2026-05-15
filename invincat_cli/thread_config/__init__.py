"""Thread selector configuration persistence.

This module owns the user preferences used by the thread selector and the
`threads` CLI output. Keeping these helpers out of model configuration avoids
mixing UI/session preferences with model-provider state.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any, NamedTuple

import tomli_w

from invincat_cli.config.paths import DEFAULT_CONFIG_PATH

logger = logging.getLogger(__name__)

THREAD_COLUMN_DEFAULTS: dict[str, bool] = {
    "thread_id": False,
    "messages": True,
    "created_at": True,
    "updated_at": True,
    "git_branch": False,
    "cwd": False,
    "initial_prompt": True,
    "agent_name": False,
}
"""Default visibility for thread selector columns."""


class ThreadConfig(NamedTuple):
    """Coalesced thread-selector configuration read from a single TOML parse."""

    columns: dict[str, bool]
    """Column visibility settings."""

    relative_time: bool
    """Whether to display timestamps as relative time."""

    sort_order: str
    """`'updated_at'` or `'created_at'`."""


_thread_config_cache: tuple[Path, ThreadConfig] | None = None


def _resolve_config_path(
    config_path: Path | None,
    *,
    default_config_path: Path = DEFAULT_CONFIG_PATH,
) -> Path:
    """Resolve an optional config path against the caller's default path."""
    return default_config_path if config_path is None else config_path


def _read_config_data(config_path: Path) -> dict[str, Any] | None:
    """Read TOML config data, returning None when the file cannot be used."""
    try:
        if not config_path.exists():
            return {}
        with config_path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return data if isinstance(data, dict) else {}


def _write_config_data(config_path: Path, data: dict[str, Any]) -> bool:
    """Atomically write TOML config data."""
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=config_path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(data, f)
            Path(tmp_path).replace(config_path)
        except BaseException:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()
            raise
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return True


def _threads_section(data: dict[str, Any]) -> dict[str, Any]:
    """Return the mutable `[threads]` section, creating it if necessary."""
    section = data.setdefault("threads", {})
    if not isinstance(section, dict):
        section = {}
        data["threads"] = section
    return section


def load_thread_config(
    config_path: Path | None = None,
    *,
    default_config_path: Path = DEFAULT_CONFIG_PATH,
) -> ThreadConfig:
    """Load all thread-selector settings from one config file read."""
    global _thread_config_cache  # noqa: PLW0603

    use_default = config_path is None
    resolved_path = _resolve_config_path(
        config_path,
        default_config_path=default_config_path,
    )
    if use_default and _thread_config_cache is not None:
        cached_path, cached_config = _thread_config_cache
        if cached_path == resolved_path:
            return cached_config

    columns = dict(THREAD_COLUMN_DEFAULTS)
    relative_time = True
    sort_order = "updated_at"

    data = _read_config_data(resolved_path)
    if data is None:
        logger.warning("Could not read thread config; using defaults", exc_info=True)
        return ThreadConfig(columns, relative_time, sort_order)
    if not data:
        result = ThreadConfig(columns, relative_time, sort_order)
        if use_default:
            _thread_config_cache = (resolved_path, result)
        return result

    threads_section = data.get("threads", {})
    if not isinstance(threads_section, dict):
        threads_section = {}

    raw_columns = threads_section.get("columns", {})
    if isinstance(raw_columns, dict):
        for key in columns:
            if key in raw_columns and isinstance(raw_columns[key], bool):
                columns[key] = raw_columns[key]

    rt_value = threads_section.get("relative_time")
    if isinstance(rt_value, bool):
        relative_time = rt_value

    so_value = threads_section.get("sort_order")
    if so_value in {"updated_at", "created_at"}:
        sort_order = so_value

    result = ThreadConfig(columns, relative_time, sort_order)
    if use_default:
        _thread_config_cache = (resolved_path, result)
    return result


def invalidate_thread_config_cache() -> None:
    """Clear the cached `ThreadConfig` so the next load re-reads disk."""
    global _thread_config_cache  # noqa: PLW0603
    _thread_config_cache = None


def load_thread_columns(
    config_path: Path | None = None,
    *,
    default_config_path: Path = DEFAULT_CONFIG_PATH,
) -> dict[str, bool]:
    """Load thread column visibility from config file."""
    return dict(
        load_thread_config(
            config_path,
            default_config_path=default_config_path,
        ).columns
    )


def save_thread_columns(
    columns: dict[str, bool],
    config_path: Path | None = None,
    *,
    default_config_path: Path = DEFAULT_CONFIG_PATH,
) -> bool:
    """Save thread column visibility to config file."""
    resolved_path = _resolve_config_path(
        config_path,
        default_config_path=default_config_path,
    )
    data = _read_config_data(resolved_path)
    if data is None:
        logger.error("Could not save thread column preferences")
        return False
    _threads_section(data)["columns"] = columns
    if not _write_config_data(resolved_path, data):
        logger.error("Could not save thread column preferences")
        return False
    invalidate_thread_config_cache()
    return True


def load_thread_relative_time(
    config_path: Path | None = None,
    *,
    default_config_path: Path = DEFAULT_CONFIG_PATH,
) -> bool:
    """Load the relative-time display preference for thread timestamps."""
    return load_thread_config(
        config_path,
        default_config_path=default_config_path,
    ).relative_time


def save_thread_relative_time(
    enabled: bool,
    config_path: Path | None = None,
    *,
    default_config_path: Path = DEFAULT_CONFIG_PATH,
) -> bool:
    """Save the relative-time display preference for thread timestamps."""
    resolved_path = _resolve_config_path(
        config_path,
        default_config_path=default_config_path,
    )
    data = _read_config_data(resolved_path)
    if data is None:
        logger.error("Could not save thread relative_time preference")
        return False
    _threads_section(data)["relative_time"] = enabled
    if not _write_config_data(resolved_path, data):
        logger.error("Could not save thread relative_time preference")
        return False
    invalidate_thread_config_cache()
    return True


def load_thread_sort_order(
    config_path: Path | None = None,
    *,
    default_config_path: Path = DEFAULT_CONFIG_PATH,
) -> str:
    """Load the sort order preference for the thread selector."""
    return load_thread_config(
        config_path,
        default_config_path=default_config_path,
    ).sort_order


def save_thread_sort_order(
    sort_order: str,
    config_path: Path | None = None,
    *,
    default_config_path: Path = DEFAULT_CONFIG_PATH,
) -> bool:
    """Save the sort order preference for the thread selector."""
    if sort_order not in {"updated_at", "created_at"}:
        msg = (
            f"Invalid sort_order {sort_order!r}; expected 'updated_at' or 'created_at'"
        )
        raise ValueError(msg)

    resolved_path = _resolve_config_path(
        config_path,
        default_config_path=default_config_path,
    )
    data = _read_config_data(resolved_path)
    if data is None:
        logger.error("Could not save thread sort_order preference")
        return False
    _threads_section(data)["sort_order"] = sort_order
    if not _write_config_data(resolved_path, data):
        logger.error("Could not save thread sort_order preference")
        return False
    invalidate_thread_config_cache()
    return True
