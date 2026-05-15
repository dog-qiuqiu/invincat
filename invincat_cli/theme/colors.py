"""Theme color constants and dataclasses."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields

LC_DARK = "#11121D"
LC_CARD = "#1A1B2E"
LC_BORDER_DK = "#25283B"
LC_BORDER_LT = "#3A3E57"
LC_BODY = "#C0CAF5"
LC_BLUE = "#7AA2F7"
LC_PURPLE = "#BB9AF7"
LC_GREEN = "#9ECE6A"
LC_AMBER = "#EB8B46"
LC_PINK = "#F7768E"
LC_MUTED = "#545C7E"
LC_GREEN_BG = "#1C2A38"
LC_PINK_BG = "#2A1F32"
LC_PANEL = "#25283B"
LC_SKILL = "#A78BFA"
LC_SKILL_HOVER = "#C4B5FD"
LC_TOOL = LC_AMBER
LC_TOOL_HOVER = "#FFCB91"

LC_LIGHT_BG = "#F5F5F7"
LC_LIGHT_SURFACE = "#EAEAEE"
LC_LIGHT_BORDER = "#C8CAD0"
LC_LIGHT_BORDER_HVR = "#A0A4B0"
LC_LIGHT_BODY = "#24283B"
LC_LIGHT_BLUE = "#2E5EAA"
LC_LIGHT_PURPLE = "#7C3AED"
LC_LIGHT_GREEN = "#3A7D0A"
LC_LIGHT_AMBER = "#B45309"
LC_LIGHT_PINK = "#BE185D"
LC_LIGHT_MUTED = "#6B7280"
LC_LIGHT_GREEN_BG = "#DCFCE7"
LC_LIGHT_PINK_BG = "#FEE2E2"
LC_LIGHT_PANEL = "#E0E1E6"
LC_LIGHT_SKILL = "#7C3AED"
LC_LIGHT_SKILL_HOVER = "#6D28D9"
LC_LIGHT_TOOL = LC_LIGHT_AMBER
LC_LIGHT_TOOL_HOVER = "#78350F"

PRIMARY = "blue"
PRIMARY_DEV = "bright_red"
SUCCESS = "green"
WARNING = "yellow"
MUTED = "bright_black"
MODE_BASH = "red"
MODE_COMMAND = "magenta"
DIFF_ADD_FG = "green"
DIFF_ADD_BG = "green"
DIFF_REMOVE_FG = "red"
DIFF_REMOVE_BG = "red"
DIFF_CONTEXT = "bright_black"
TOOL_BORDER = "bright_black"
TOOL_HEADER = "yellow"
FILE_PYTHON = "blue"
FILE_CONFIG = "yellow"
FILE_DIR = "green"
SPINNER = "blue"

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True, slots=True)
class ThemeColors:
    """Complete set of semantic colors for one theme variant."""

    primary: str
    secondary: str
    accent: str
    panel: str
    success: str
    warning: str
    error: str
    muted: str
    mode_bash: str
    mode_command: str
    skill: str
    skill_hover: str
    tool: str
    tool_hover: str
    foreground: str
    background: str
    surface: str

    def __post_init__(self) -> None:
        for f in fields(self):
            val = getattr(self, f.name)
            if not _HEX_RE.match(val):
                msg = (
                    f"ThemeColors.{f.name} must be a 7-char hex color"
                    f" (#RRGGBB), got {val!r}"
                )
                raise ValueError(msg)

    @classmethod
    def merged(cls, base: ThemeColors, overrides: dict[str, str]) -> ThemeColors:
        """Create a new color set by overlaying valid overrides onto a base."""
        valid_names = {f.name for f in fields(cls)}
        kwargs = {f.name: getattr(base, f.name) for f in fields(cls)}
        kwargs.update({k: v for k, v in overrides.items() if k in valid_names})
        return cls(**kwargs)


DARK_COLORS = ThemeColors(
    primary=LC_BLUE,
    secondary=LC_PURPLE,
    accent=LC_GREEN,
    panel=LC_PANEL,
    success=LC_GREEN,
    warning=LC_AMBER,
    error=LC_PINK,
    muted=LC_MUTED,
    mode_bash=LC_PINK,
    mode_command=LC_PURPLE,
    skill=LC_SKILL,
    skill_hover=LC_SKILL_HOVER,
    tool=LC_TOOL,
    tool_hover=LC_TOOL_HOVER,
    foreground=LC_BODY,
    background=LC_DARK,
    surface=LC_CARD,
)

LIGHT_COLORS = ThemeColors(
    primary=LC_LIGHT_BLUE,
    secondary=LC_LIGHT_PURPLE,
    accent=LC_LIGHT_GREEN,
    panel=LC_LIGHT_PANEL,
    success=LC_LIGHT_GREEN,
    warning=LC_LIGHT_AMBER,
    error=LC_LIGHT_PINK,
    muted=LC_LIGHT_MUTED,
    mode_bash=LC_LIGHT_PINK,
    mode_command=LC_LIGHT_PURPLE,
    skill=LC_LIGHT_SKILL,
    skill_hover=LC_LIGHT_SKILL_HOVER,
    tool=LC_LIGHT_TOOL,
    tool_hover=LC_LIGHT_TOOL_HOVER,
    foreground=LC_LIGHT_BODY,
    background=LC_LIGHT_BG,
    surface=LC_LIGHT_SURFACE,
)
