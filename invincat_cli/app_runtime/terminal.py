"""Terminal compatibility helpers for the Textual app."""

from __future__ import annotations

import atexit
import os

# iTerm2 cursor guide escape sequences (OSC 1337)
# Format: OSC 1337 ; HighlightCursorLine=<yes|no> ST
_ITERM_CURSOR_GUIDE_OFF = "\x1b]1337;HighlightCursorLine=no\x1b\\"
_ITERM_CURSOR_GUIDE_ON = "\x1b]1337;HighlightCursorLine=yes\x1b\\"
_atexit_registered = False


def _is_iterm_tty() -> bool:
    """Return whether stderr points at an iTerm2 terminal."""
    return (
        (
            os.environ.get("LC_TERMINAL", "") == "iTerm2"
            or os.environ.get("TERM_PROGRAM", "") == "iTerm.app"
        )
        and hasattr(os, "isatty")
        and os.isatty(2)
    )


def _write_iterm_escape(sequence: str) -> None:
    """Write an iTerm2 escape sequence to stderr, ignoring cosmetic failures."""
    if not _is_iterm_tty():
        return
    try:
        import sys

        if sys.__stderr__ is not None:
            sys.__stderr__.write(sequence)
            sys.__stderr__.flush()
    except OSError:
        pass


def restore_cursor_guide() -> None:
    """Restore iTerm2 cursor guide on exit."""
    _write_iterm_escape(_ITERM_CURSOR_GUIDE_ON)


def disable_cursor_guide() -> None:
    """Disable iTerm2 cursor guide while Textual owns the terminal."""
    global _atexit_registered

    _write_iterm_escape(_ITERM_CURSOR_GUIDE_OFF)
    if _is_iterm_tty() and not _atexit_registered:
        atexit.register(restore_cursor_guide)
        _atexit_registered = True
