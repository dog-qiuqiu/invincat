"""Terminal charset detection, UI glyph sets, and banner rendering."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import StrEnum


class CharsetMode(StrEnum):
    """Character set mode for TUI display."""

    UNICODE = "unicode"
    ASCII = "ascii"
    AUTO = "auto"


@dataclass(frozen=True)
class Glyphs:
    """Character glyphs for TUI display."""

    tool_prefix: str
    ellipsis: str
    checkmark: str
    error: str
    circle_empty: str
    circle_filled: str
    output_prefix: str
    spinner_frames: tuple[str, ...]
    pause: str
    newline: str
    warning: str
    question: str
    arrow_up: str
    arrow_down: str
    bullet: str
    cursor: str
    box_vertical: str
    box_horizontal: str
    box_double_horizontal: str
    gutter_bar: str
    git_branch: str


UNICODE_GLYPHS = Glyphs(
    tool_prefix="⏺",
    ellipsis="…",
    checkmark="✓",
    error="✗",
    circle_empty="○",
    circle_filled="●",
    output_prefix="⎿",
    spinner_frames=("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"),
    pause="⏸",
    newline="⏎",
    warning="⚠",
    question="?",
    arrow_up="↑",
    arrow_down="↓",
    bullet="•",
    cursor="›",  # noqa: RUF001  # Intentional Unicode glyph
    box_vertical="│",
    box_horizontal="─",
    box_double_horizontal="═",
    gutter_bar="▌",
    git_branch="↗",
)
"""Glyph set for terminals with full Unicode support."""

ASCII_GLYPHS = Glyphs(
    tool_prefix="(*)",
    ellipsis="...",
    checkmark="[OK]",
    error="[X]",
    circle_empty="[ ]",
    circle_filled="[*]",
    output_prefix="L",
    spinner_frames=("(-)", "(\\)", "(|)", "(/)"),
    pause="||",
    newline="\\n",
    warning="[!]",
    question="[?]",
    arrow_up="^",
    arrow_down="v",
    bullet="-",
    cursor=">",
    box_vertical="|",
    box_horizontal="-",
    box_double_horizontal="=",
    gutter_bar="|",
    git_branch="git:",
)
"""Glyph set for terminals limited to 7-bit ASCII."""

_BANNER_TEMPLATE = """
▒▓█▒░░▒▓█▒░▒▓█▒░▒▓█▒░▒▓█▒░▒▓█▒░▒▓█▒░▒▓█▒░▒▓█▒░▒▓█▒░▒▓█▒░▒░▒▓█▒░▒▓█▒░▒░▒▓█▒░▒▓█▒
╔═════════════════════════════════════════════════════════════════════════════╗
║                                                                             ║
║   ██╗  ███╗  ██╗  ██╗   ██╗  ██╗  ███╗  ██╗   ██████╗   █████╗   ████████╗  ║
║   ██║  ████╗ ██║  ██║   ██║  ██║  ████╗ ██║  ██╔════╝  ██╔══██╗  ╚══██╔══╝  ║
║   ██║  ██╔██╗██║  ██║   ██║  ██║  ██╔██╗██║  ██║       ███████║     ██║     ║
║   ██║  ██║╚████║  ██║   ██║  ██║  ██║╚████║  ██║       ██╔══██║     ██║     ║
║   ██║  ██║ ╚███║  ╚██████╔╝  ██║  ██║ ╚███║  ╚██████╗  ██║  ██║     ██║     ║
║   ╚═╝  ╚═╝  ╚══╝   ╚═════╝   ╚═╝  ╚═╝  ╚══╝   ╚═════╝  ╚═╝  ╚═╝     ╚═╝     ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
░▒▓█▒░▒▓█▒░▒▓█▒░▒▓█▒░▒▓█▒░▒▓█▒░▒▓█▒░▒▓█▒░▒▓█▒░▒▓█▒░▒▓█▒░▒░▒▓█▒░▒▓█▒░▒░▒▓█▒░▒▓█▒

version: {version}
"""


def detect_charset_mode() -> CharsetMode:
    """Auto-detect terminal charset capabilities."""
    env_mode = os.environ.get("UI_CHARSET_MODE", "auto").lower()
    if env_mode == "unicode":
        return CharsetMode.UNICODE
    if env_mode == "ascii":
        return CharsetMode.ASCII

    encoding = getattr(sys.stdout, "encoding", "") or ""
    if "utf" in encoding.lower():
        return CharsetMode.UNICODE
    lang = os.environ.get("LANG", "") or os.environ.get("LC_ALL", "")
    if "utf" in lang.lower():
        return CharsetMode.UNICODE
    return CharsetMode.ASCII


def newline_shortcut() -> str:
    """Return the platform-native label for the newline keyboard shortcut."""
    return "Ctrl+J"


def render_banner(version: str, *, editable: bool = False) -> str:
    """Render the CLI banner for the supplied package version."""
    suffix = " (local)" if editable else ""
    return _BANNER_TEMPLATE.format(version=f"{version}{suffix}")
