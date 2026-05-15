"""Shared paths for user-level Invincat configuration."""

from __future__ import annotations

from pathlib import Path

DEFAULT_CONFIG_DIR = Path.home() / ".invincat"
"""Directory for user-level Invincat configuration (`~/.invincat`)."""

DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.toml"
"""Path to the user's configuration file (`~/.invincat/config.toml`)."""
