"""Theme registry construction and user theme loading."""

from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar

from invincat_cli.theme.colors import DARK_COLORS, LIGHT_COLORS, ThemeColors

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ThemeEntry:
    """Metadata for a registered theme."""

    label: str
    dark: bool
    colors: ThemeColors
    custom: bool = True

    REGISTRY: ClassVar[MappingProxyType[str, ThemeEntry]]

    def __post_init__(self) -> None:
        if not self.label.strip():
            msg = "ThemeEntry.label must be a non-empty string"
            raise ValueError(msg)


def _builtin_themes() -> dict[str, ThemeEntry]:
    """Return the built-in theme entries as a mutable dict."""
    r: dict[str, ThemeEntry] = {
        "langchain": ThemeEntry(
            label="LangChain Dark",
            dark=True,
            colors=DARK_COLORS,
        ),
        "langchain-light": ThemeEntry(
            label="LangChain Light",
            dark=False,
            colors=LIGHT_COLORS,
        ),
    }

    def _bi(label: str, *, is_dark: bool) -> ThemeEntry:
        return ThemeEntry(
            label=label,
            dark=is_dark,
            colors=DARK_COLORS if is_dark else LIGHT_COLORS,
            custom=False,
        )

    r["textual-dark"] = _bi("Textual Dark", is_dark=True)
    r["textual-light"] = _bi("Textual Light", is_dark=False)
    r["textual-ansi"] = _bi("Terminal (ANSI)", is_dark=False)
    r["atom-one-dark"] = _bi("Atom One Dark", is_dark=True)
    r["atom-one-light"] = _bi("Atom One Light", is_dark=False)
    r["catppuccin-frappe"] = _bi("Catppuccin Frappé", is_dark=True)
    r["catppuccin-latte"] = _bi("Catppuccin Latte", is_dark=False)
    r["catppuccin-macchiato"] = _bi("Catppuccin Macchiato", is_dark=True)
    r["catppuccin-mocha"] = _bi("Catppuccin Mocha", is_dark=True)
    r["dracula"] = _bi("Dracula", is_dark=True)
    r["flexoki"] = _bi("Flexoki", is_dark=True)
    r["gruvbox"] = _bi("Gruvbox", is_dark=True)
    r["monokai"] = _bi("Monokai", is_dark=True)
    r["nord"] = _bi("Nord", is_dark=True)
    r["rose-pine"] = _bi("Rosé Pine", is_dark=True)
    r["rose-pine-dawn"] = _bi("Rosé Pine Dawn", is_dark=False)
    r["rose-pine-moon"] = _bi("Rosé Pine Moon", is_dark=True)
    r["solarized-dark"] = _bi("Solarized Dark", is_dark=True)
    r["solarized-light"] = _bi("Solarized Light", is_dark=False)
    r["tokyo-night"] = _bi("Tokyo Night", is_dark=True)
    return r


_BUILTIN_NAMES: frozenset[str] = frozenset(_builtin_themes())


def _load_user_themes(
    builtins: dict[str, ThemeEntry],
    *,
    config_path: Path | None = None,
) -> None:
    """Load user-defined themes from `config.toml` into `builtins`."""
    if config_path is None:
        try:
            config_path = Path.home() / ".invincat" / "config.toml"
        except RuntimeError:
            logger.debug("Cannot determine home directory; skipping user theme loading")
            return

    import tomllib

    try:
        if not config_path.exists():
            return

        with config_path.open("rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, PermissionError, OSError) as exc:
        logger.warning(
            "Could not read %s for user themes: %s",
            config_path,
            exc,
        )
        return

    themes_section: Any = data.get("themes")
    if not isinstance(themes_section, dict) or not themes_section:
        return

    valid_color_names = {f.name for f in fields(ThemeColors)}
    reserved = {"label", "dark"}

    for name, section in themes_section.items():
        if not isinstance(section, dict):
            logger.warning("Ignoring non-table [themes.%s]", name)
            continue

        color_overrides: dict[str, str] = {}
        for k, v in section.items():
            if k in reserved:
                continue
            if not isinstance(v, str):
                logger.warning(
                    "User theme '%s' field '%s' must be a string, got %s; ignoring",
                    name,
                    k,
                    type(v).__name__,
                )
                continue
            if k in valid_color_names:
                color_overrides[k] = v
            else:
                logger.warning(
                    "User theme '%s' has unknown color field '%s'; ignoring",
                    name,
                    k,
                )

        if name in _BUILTIN_NAMES:
            _apply_builtin_theme_override(builtins, name, color_overrides)
            continue

        _add_user_theme(builtins, name, section, color_overrides)


def _apply_builtin_theme_override(
    builtins: dict[str, ThemeEntry],
    name: str,
    color_overrides: dict[str, str],
) -> None:
    existing = builtins.get(name)
    if existing is None:
        logger.warning("Built-in theme '%s' not in builtins dict; skipping override", name)
        return
    if not color_overrides:
        return
    try:
        colors = ThemeColors.merged(existing.colors, color_overrides)
    except ValueError as exc:
        logger.warning(
            "Built-in theme '%s' color override invalid: %s; skipping",
            name,
            exc,
        )
        return
    builtins[name] = ThemeEntry(
        label=existing.label,
        dark=existing.dark,
        colors=colors,
        custom=existing.custom,
    )


def _add_user_theme(
    builtins: dict[str, ThemeEntry],
    name: str,
    section: dict[str, Any],
    color_overrides: dict[str, str],
) -> None:
    label = section.get("label")
    if not isinstance(label, str) or not label.strip():
        logger.warning(
            "User theme '%s' missing required 'label' (str); skipping",
            name,
        )
        return

    dark = section.get("dark", False)
    if not isinstance(dark, bool):
        logger.warning(
            "User theme '%s': 'dark' must be true or false, got %s (%r);"
            " defaulting to light",
            name,
            type(dark).__name__,
            dark,
        )
        dark = False

    base = DARK_COLORS if dark else LIGHT_COLORS
    try:
        colors = ThemeColors.merged(base, color_overrides)
    except ValueError as exc:
        logger.warning(
            "User theme '%s' has invalid colors: %s; skipping",
            name,
            exc,
        )
        return

    builtins[name] = ThemeEntry(
        label=label,
        dark=dark,
        colors=colors,
        custom=True,
    )


def _build_registry(
    *, config_path: Path | None = None
) -> MappingProxyType[str, ThemeEntry]:
    """Build and freeze the theme registry."""
    r = _builtin_themes()
    _load_user_themes(r, config_path=config_path)
    return MappingProxyType(r)
