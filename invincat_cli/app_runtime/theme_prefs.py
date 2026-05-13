"""Theme preference persistence for the Textual app."""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from pathlib import Path

from invincat_cli import theme
from invincat_cli.model_config import DEFAULT_CONFIG_PATH

logger = logging.getLogger(__name__)


def load_theme_preference() -> str:
    """Load the saved theme name from config, or return the default."""
    import tomllib

    try:
        if not DEFAULT_CONFIG_PATH.exists():
            return theme.DEFAULT_THEME

        with DEFAULT_CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, PermissionError, OSError) as exc:
        logger.warning("Could not read config for theme preference: %s", exc)
        return theme.DEFAULT_THEME

    name = data.get("ui", {}).get("theme")
    if isinstance(name, str) and name in theme.ThemeEntry.REGISTRY:
        return name
    if isinstance(name, str):
        logger.warning(
            "Unknown theme '%s' in config; falling back to default",
            name,
        )
    return theme.DEFAULT_THEME


def save_theme_preference(name: str) -> bool:
    """Persist theme preference to `~/.invincat/config.toml`."""
    if name not in theme.ThemeEntry.REGISTRY:
        logger.warning("Refusing to save unknown theme '%s'", name)
        return False

    try:
        import tomllib

        import tomli_w

        DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if DEFAULT_CONFIG_PATH.exists():
            with DEFAULT_CONFIG_PATH.open("rb") as f:
                data = tomllib.load(f)
        else:
            data = {}

        if "ui" not in data:
            data["ui"] = {}
        data["ui"]["theme"] = name

        fd, tmp_path = tempfile.mkstemp(dir=DEFAULT_CONFIG_PATH.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(data, f)
            Path(tmp_path).replace(DEFAULT_CONFIG_PATH)
        except BaseException:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()
            raise
    except Exception:
        logger.exception("Could not save theme preference")
        return False
    return True
