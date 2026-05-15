"""Detection and rendering of deceptive Unicode control characters."""

from __future__ import annotations

import unicodedata

from invincat_cli.unicode_security.models import UnicodeIssue

_DANGEROUS_CODEPOINTS: frozenset[int] = frozenset(
    {
        *range(0x202A, 0x202F),
        *range(0x2066, 0x206A),
        0x200B,
        0x200C,
        0x200D,
        0x200E,
        0x200F,
        0x2060,
        0xFEFF,
        0x00AD,
        0x034F,
        0x115F,
        0x1160,
    }
)
"""Code points that should be treated as deceptive/invisible for CLI safety."""

_DANGEROUS_CHARACTERS: frozenset[str] = frozenset(
    chr(codepoint) for codepoint in _DANGEROUS_CODEPOINTS
)


def detect_dangerous_unicode(text: str) -> list[UnicodeIssue]:
    """Detect deceptive or hidden Unicode code points in text."""
    issues: list[UnicodeIssue] = []
    for position, character in enumerate(text):
        if character not in _DANGEROUS_CHARACTERS:
            continue
        issues.append(
            UnicodeIssue(
                position=position,
                character=character,
                codepoint=_format_codepoint(character),
                name=_unicode_name(character),
            )
        )
    return issues


def strip_dangerous_unicode(text: str) -> str:
    """Remove known dangerous/invisible Unicode characters from text."""
    return "".join(ch for ch in text if ch not in _DANGEROUS_CHARACTERS)


def render_with_unicode_markers(text: str) -> str:
    """Render hidden Unicode characters as explicit markers."""
    rendered_parts: list[str] = []
    for character in text:
        if character not in _DANGEROUS_CHARACTERS:
            rendered_parts.append(character)
            continue
        rendered_parts.append(
            f"<{_format_codepoint(character)} {_unicode_name(character)}>"
        )
    return "".join(rendered_parts)


def summarize_issues(issues: list[UnicodeIssue], *, max_items: int = 3) -> str:
    """Summarize Unicode issues for warning messages."""
    unique_entries: list[str] = []
    seen: set[str] = set()
    for issue in issues:
        entry = f"{issue.codepoint} {issue.name}"
        if entry in seen:
            continue
        seen.add(entry)
        unique_entries.append(entry)

    if len(unique_entries) <= max_items:
        return ", ".join(unique_entries)

    displayed = ", ".join(unique_entries[:max_items])
    remainder = len(unique_entries) - max_items
    suffix = "entry" if remainder == 1 else "entries"
    return f"{displayed}, +{remainder} more {suffix}"


def format_warning_detail(warnings: tuple[str, ...], *, max_shown: int = 2) -> str:
    """Join safety warnings into a display string with overflow indicator."""
    shown = warnings[:max_shown]
    detail = "; ".join(shown)
    remaining = len(warnings) - max_shown
    if remaining > 0:
        detail += f"; +{remaining} more"
    return detail


def _format_codepoint(character: str) -> str:
    """Format character code point in `U+XXXX` uppercase form."""
    return f"U+{ord(character):04X}"


def _unicode_name(character: str) -> str:
    """Return a stable Unicode name with a fallback for unknown code points."""
    return unicodedata.name(character, "UNKNOWN CHARACTER")
