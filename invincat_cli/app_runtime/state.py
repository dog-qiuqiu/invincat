"""State dataclasses for the Textual app."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from invincat_cli.core.session_stats import SessionStats
from invincat_cli.widgets.message_store import MessageData

InputMode = Literal["normal", "shell", "command"]


def new_thread_id() -> str:
    """Generate a new thread id without importing sessions at module load."""
    from invincat_cli.sessions import generate_thread_id

    return generate_thread_id()


@dataclass(frozen=True, slots=True)
class QueuedMessage:
    """Represents a queued user message awaiting processing."""

    text: str
    mode: InputMode
    scheduled_run_id: str | None = None
    scheduled_task_id: str | None = None


DeferredActionKind = Literal[
    "model_switch", "thread_switch", "chat_output", "plan_handoff"
]


@dataclass(frozen=True, slots=True, kw_only=True)
class DeferredAction:
    """An action deferred until the current busy state resolves."""

    kind: DeferredActionKind
    execute: Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ThreadHistoryPayload:
    """Data returned by thread-history loading."""

    messages: list[MessageData]
    context_tokens: int


class TextualSessionState:
    """Session state for the Textual app."""

    def __init__(
        self,
        *,
        auto_approve: bool = False,
        thread_id: str | None = None,
    ) -> None:
        self.auto_approve = auto_approve
        self.thread_id = thread_id or new_thread_id()
        self.plan_mode: bool = False

    def reset_thread(self) -> str:
        """Reset to a new thread and return its id."""
        self.thread_id = new_thread_id()
        return self.thread_id


@dataclass(frozen=True)
class AppResult:
    """Result from running the Textual application."""

    return_code: int
    thread_id: str | None
    session_stats: SessionStats = field(default_factory=SessionStats)
    update_available: tuple[bool, str | None] = (False, None)
