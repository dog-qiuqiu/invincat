"""Data models for Unicode and URL safety checks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UnicodeIssue:
    """A dangerous Unicode character found in text."""

    position: int
    character: str
    codepoint: str
    name: str

    def __post_init__(self) -> None:  # noqa: D105
        if len(self.character) != 1:
            msg = (
                "character must be a single code point, "
                f"got length {len(self.character)}"
            )
            raise ValueError(msg)
        expected = f"U+{ord(self.character):04X}"
        if self.codepoint != expected:
            msg = (
                f"codepoint {self.codepoint!r} does not match "
                f"character (expected {expected})"
            )
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class UrlSafetyResult:
    """Safety analysis output for a URL string."""

    safe: bool
    decoded_domain: str | None
    warnings: tuple[str, ...]
    issues: tuple[UnicodeIssue, ...]
