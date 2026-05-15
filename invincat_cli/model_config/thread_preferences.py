"""Compatibility wrappers for thread selector preferences in model config."""

from __future__ import annotations

from pathlib import Path

from invincat_cli.thread_config import ThreadConfig


def _default_config_path() -> Path:
    from invincat_cli import model_config as _model_config

    return _model_config.DEFAULT_CONFIG_PATH


def load_thread_config(config_path: Path | None = None) -> ThreadConfig:
    """Load all thread-selector settings from one config file read."""
    from invincat_cli.thread_config import load_thread_config as _load_thread_config

    return _load_thread_config(config_path, default_config_path=_default_config_path())


def invalidate_thread_config_cache() -> None:
    """Clear the cached `ThreadConfig` so the next load re-reads disk."""
    from invincat_cli.thread_config import (
        invalidate_thread_config_cache as _invalidate_thread_config_cache,
    )

    _invalidate_thread_config_cache()


def load_thread_columns(config_path: Path | None = None) -> dict[str, bool]:
    """Load thread column visibility from config file."""
    from invincat_cli.thread_config import load_thread_columns as _load_thread_columns

    return _load_thread_columns(config_path, default_config_path=_default_config_path())


def save_thread_columns(
    columns: dict[str, bool], config_path: Path | None = None
) -> bool:
    """Save thread column visibility to config file."""
    from invincat_cli.thread_config import save_thread_columns as _save_thread_columns

    return _save_thread_columns(
        columns,
        config_path,
        default_config_path=_default_config_path(),
    )


def load_thread_relative_time(config_path: Path | None = None) -> bool:
    """Load the relative-time display preference for thread timestamps."""
    from invincat_cli.thread_config import (
        load_thread_relative_time as _load_thread_relative_time,
    )

    return _load_thread_relative_time(
        config_path,
        default_config_path=_default_config_path(),
    )


def save_thread_relative_time(enabled: bool, config_path: Path | None = None) -> bool:
    """Save the relative-time display preference for thread timestamps."""
    from invincat_cli.thread_config import (
        save_thread_relative_time as _save_thread_relative_time,
    )

    return _save_thread_relative_time(
        enabled,
        config_path,
        default_config_path=_default_config_path(),
    )


def load_thread_sort_order(config_path: Path | None = None) -> str:
    """Load the sort order preference for the thread selector."""
    from invincat_cli.thread_config import (
        load_thread_sort_order as _load_thread_sort_order,
    )

    return _load_thread_sort_order(
        config_path,
        default_config_path=_default_config_path(),
    )


def save_thread_sort_order(sort_order: str, config_path: Path | None = None) -> bool:
    """Save the sort order preference for the thread selector."""
    from invincat_cli.thread_config import (
        save_thread_sort_order as _save_thread_sort_order,
    )

    return _save_thread_sort_order(
        sort_order,
        config_path,
        default_config_path=_default_config_path(),
    )
