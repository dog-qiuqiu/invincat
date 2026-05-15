"""Theme facade for CLI colors, registry loading, and runtime color lookup."""

from __future__ import annotations

import logging
from dataclasses import fields as fields
from types import MappingProxyType
from typing import TYPE_CHECKING

from invincat_cli.theme.colors import (
    _HEX_RE as _HEX_RE,
)
from invincat_cli.theme.colors import (
    DARK_COLORS as DARK_COLORS,
)
from invincat_cli.theme.colors import (
    DIFF_ADD_BG as DIFF_ADD_BG,
)
from invincat_cli.theme.colors import (
    DIFF_ADD_FG as DIFF_ADD_FG,
)
from invincat_cli.theme.colors import (
    DIFF_CONTEXT as DIFF_CONTEXT,
)
from invincat_cli.theme.colors import (
    DIFF_REMOVE_BG as DIFF_REMOVE_BG,
)
from invincat_cli.theme.colors import (
    DIFF_REMOVE_FG as DIFF_REMOVE_FG,
)
from invincat_cli.theme.colors import (
    FILE_CONFIG as FILE_CONFIG,
)
from invincat_cli.theme.colors import (
    FILE_DIR as FILE_DIR,
)
from invincat_cli.theme.colors import (
    FILE_PYTHON as FILE_PYTHON,
)
from invincat_cli.theme.colors import (
    LC_AMBER as LC_AMBER,
)
from invincat_cli.theme.colors import (
    LC_BLUE as LC_BLUE,
)
from invincat_cli.theme.colors import (
    LC_BODY as LC_BODY,
)
from invincat_cli.theme.colors import (
    LC_BORDER_DK as LC_BORDER_DK,
)
from invincat_cli.theme.colors import (
    LC_BORDER_LT as LC_BORDER_LT,
)
from invincat_cli.theme.colors import (
    LC_CARD as LC_CARD,
)
from invincat_cli.theme.colors import (
    LC_DARK as LC_DARK,
)
from invincat_cli.theme.colors import (
    LC_GREEN as LC_GREEN,
)
from invincat_cli.theme.colors import (
    LC_GREEN_BG as LC_GREEN_BG,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_AMBER as LC_LIGHT_AMBER,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_BG as LC_LIGHT_BG,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_BLUE as LC_LIGHT_BLUE,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_BODY as LC_LIGHT_BODY,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_BORDER as LC_LIGHT_BORDER,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_BORDER_HVR as LC_LIGHT_BORDER_HVR,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_GREEN as LC_LIGHT_GREEN,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_GREEN_BG as LC_LIGHT_GREEN_BG,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_MUTED as LC_LIGHT_MUTED,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_PANEL as LC_LIGHT_PANEL,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_PINK as LC_LIGHT_PINK,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_PINK_BG as LC_LIGHT_PINK_BG,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_PURPLE as LC_LIGHT_PURPLE,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_SKILL as LC_LIGHT_SKILL,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_SKILL_HOVER as LC_LIGHT_SKILL_HOVER,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_SURFACE as LC_LIGHT_SURFACE,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_TOOL as LC_LIGHT_TOOL,
)
from invincat_cli.theme.colors import (
    LC_LIGHT_TOOL_HOVER as LC_LIGHT_TOOL_HOVER,
)
from invincat_cli.theme.colors import (
    LC_MUTED as LC_MUTED,
)
from invincat_cli.theme.colors import (
    LC_PANEL as LC_PANEL,
)
from invincat_cli.theme.colors import (
    LC_PINK as LC_PINK,
)
from invincat_cli.theme.colors import (
    LC_PINK_BG as LC_PINK_BG,
)
from invincat_cli.theme.colors import (
    LC_PURPLE as LC_PURPLE,
)
from invincat_cli.theme.colors import (
    LC_SKILL as LC_SKILL,
)
from invincat_cli.theme.colors import (
    LC_SKILL_HOVER as LC_SKILL_HOVER,
)
from invincat_cli.theme.colors import (
    LC_TOOL as LC_TOOL,
)
from invincat_cli.theme.colors import (
    LC_TOOL_HOVER as LC_TOOL_HOVER,
)
from invincat_cli.theme.colors import (
    LIGHT_COLORS as LIGHT_COLORS,
)
from invincat_cli.theme.colors import (
    MODE_BASH as MODE_BASH,
)
from invincat_cli.theme.colors import (
    MODE_COMMAND as MODE_COMMAND,
)
from invincat_cli.theme.colors import (
    MUTED as MUTED,
)
from invincat_cli.theme.colors import (
    PRIMARY as PRIMARY,
)
from invincat_cli.theme.colors import (
    PRIMARY_DEV as PRIMARY_DEV,
)
from invincat_cli.theme.colors import (
    SPINNER as SPINNER,
)
from invincat_cli.theme.colors import (
    SUCCESS as SUCCESS,
)
from invincat_cli.theme.colors import (
    TOOL_BORDER as TOOL_BORDER,
)
from invincat_cli.theme.colors import (
    TOOL_HEADER as TOOL_HEADER,
)
from invincat_cli.theme.colors import (
    WARNING as WARNING,
)
from invincat_cli.theme.colors import (
    ThemeColors as ThemeColors,
)
from invincat_cli.theme.registry import (
    _BUILTIN_NAMES as _BUILTIN_NAMES,
)
from invincat_cli.theme.registry import (
    ThemeEntry as ThemeEntry,
)
from invincat_cli.theme.registry import (
    _build_registry as _build_registry,
)
from invincat_cli.theme.registry import (
    _builtin_themes as _builtin_themes,
)
from invincat_cli.theme.registry import (
    _load_user_themes as _load_user_themes,
)

if TYPE_CHECKING:
    from textual.app import App

logger = logging.getLogger(__name__)

ThemeEntry.REGISTRY = _build_registry()

DEFAULT_THEME = "textual-dark"
"""Theme name used when no preference is saved."""


def reload_registry() -> MappingProxyType[str, ThemeEntry]:
    """Rebuild the theme registry from disk and update `ThemeEntry.REGISTRY`."""
    ThemeEntry.REGISTRY = _build_registry()
    return ThemeEntry.REGISTRY


def get_css_variable_defaults(
    *, dark: bool = True, colors: ThemeColors | None = None
) -> dict[str, str]:
    """Return app-specific CSS variable defaults for the given theme colors."""
    c = colors if colors is not None else (DARK_COLORS if dark else LIGHT_COLORS)
    return {
        "mode-bash": c.mode_bash,
        "mode-command": c.mode_command,
        "skill": c.skill,
        "skill-hover": c.skill_hover,
        "tool": c.tool,
        "tool-hover": c.tool_hover,
    }


def _resolve_app(widget_or_app: object) -> object:
    """Resolve a widget or App to the App instance."""
    return (
        widget_or_app.app  # type: ignore[attr-defined]
        if hasattr(type(widget_or_app), "app")
        else widget_or_app
    )


def _colors_from_textual_theme(app: object) -> ThemeColors:
    """Construct `ThemeColors` from the app's active Textual theme."""
    ct = app.current_theme  # type: ignore[attr-defined]
    dark: bool = ct.dark
    base = DARK_COLORS if dark else LIGHT_COLORS

    def _hex_or(val: str | None, fallback: str) -> str:
        if val is not None and _HEX_RE.match(val):
            return val
        return fallback

    return ThemeColors(
        primary=_hex_or(ct.primary, base.primary),
        secondary=_hex_or(ct.secondary, base.secondary),
        accent=_hex_or(ct.accent, base.accent),
        panel=_hex_or(ct.panel, base.panel),
        success=_hex_or(ct.success, base.success),
        warning=_hex_or(ct.warning, base.warning),
        error=_hex_or(ct.error, base.error),
        muted=base.muted,
        mode_bash=_hex_or(ct.error, base.mode_bash),
        mode_command=_hex_or(ct.secondary, base.mode_command),
        skill=base.skill,
        skill_hover=base.skill_hover,
        tool=_hex_or(ct.warning, base.tool),
        tool_hover=base.tool_hover,
        foreground=_hex_or(ct.foreground, base.foreground),
        background=_hex_or(ct.background, base.background),
        surface=_hex_or(ct.surface, base.surface),
    )


def get_theme_colors(widget_or_app: App | object | None = None) -> ThemeColors:
    """Return the `ThemeColors` for the active Textual theme."""
    if widget_or_app is None:
        try:
            from textual._context import active_app  # noqa: PLC2701

            widget_or_app = active_app.get()
        except (ImportError, LookupError):
            return DARK_COLORS

    app = _resolve_app(widget_or_app)
    entry = ThemeEntry.REGISTRY.get(app.theme)  # type: ignore[attr-defined]
    if entry is not None and entry.custom:
        return entry.colors

    try:
        return _colors_from_textual_theme(app)
    except Exception:
        logger.warning("Could not resolve theme colors dynamically", exc_info=True)
        if entry is not None:
            return entry.colors
        return DARK_COLORS
