"""State and output helpers for non-interactive execution."""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass, field

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner as RichSpinner
from rich.text import Text

from invincat_cli.config import build_langsmith_thread_url
from invincat_cli.textual_adapter import SessionStats

logger = logging.getLogger(__name__)


def _write_text(text: str) -> None:
    """Write agent response text to stdout without a trailing newline."""
    sys.stdout.write(text)
    sys.stdout.flush()


def _write_newline() -> None:
    """Write a newline to stdout and flush."""
    sys.stdout.write("\n")
    sys.stdout.flush()


class _ConsoleSpinner:
    """Animated spinner for non-interactive verbose output."""

    def __init__(self, console: Console) -> None:
        self._console = console
        self._live: Live | None = None

    def start(self, message: str = "Working...") -> None:
        """Start the spinner with the given message."""
        if self._live is not None:
            return
        renderable = RichSpinner(
            "dots",
            text=Text(f" {message}", style="dim"),
            style="dim",
        )
        try:
            self._live = Live(renderable, console=self._console, transient=True)
            self._live.start()
        except (AttributeError, TypeError, OSError) as exc:
            logger.warning("Spinner start failed: %s", exc)
            self._live = None

    def stop(self) -> None:
        """Stop the spinner if running."""
        if self._live is not None:
            try:
                self._live.stop()
            except (AttributeError, TypeError, OSError) as exc:
                logger.warning("Spinner stop failed: %s", exc)
            finally:
                self._live = None


@dataclass
class StreamState:
    """Mutable state accumulated while iterating over the agent stream."""

    quiet: bool = False
    stream: bool = True
    full_response: list[str] = field(default_factory=list)
    tool_call_buffers: dict[int | str, dict[str, str | None]] = field(
        default_factory=dict
    )
    pending_interrupts: dict[str, object] = field(default_factory=dict)
    hitl_response: dict[str, dict[str, list[dict[str, str]]]] = field(
        default_factory=dict
    )
    interrupt_occurred: bool = False
    stats: SessionStats = field(default_factory=SessionStats)
    spinner: _ConsoleSpinner | None = None


@dataclass
class ThreadUrlLookupState:
    """Best-effort background LangSmith thread URL lookup state."""

    done: threading.Event = field(default_factory=threading.Event)
    url: str | None = None


def _start_langsmith_thread_url_lookup(thread_id: str) -> ThreadUrlLookupState:
    """Start background LangSmith URL resolution without blocking."""
    state = ThreadUrlLookupState()

    def _resolve() -> None:
        try:
            state.url = build_langsmith_thread_url(thread_id)
        except Exception:
            logger.debug(
                "Could not resolve LangSmith thread URL for '%s'",
                thread_id,
                exc_info=True,
            )
        finally:
            state.done.set()

    threading.Thread(target=_resolve, daemon=True).start()
    return state
