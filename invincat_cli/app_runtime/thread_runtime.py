"""Thread switching runtime helpers for the Textual app."""

from __future__ import annotations

from typing import Literal

ThreadResumeBlockReason = Literal[
    "no_agent",
    "no_session",
    "already_on",
    "switching",
]


def thread_resume_block_reason(
    *,
    has_agent: bool,
    has_session: bool,
    current_thread_id: str | None,
    requested_thread_id: str,
    switching: bool,
) -> ThreadResumeBlockReason | None:
    """Return why a thread resume request should be blocked, if any."""
    if not has_agent:
        return "no_agent"
    if not has_session:
        return "no_session"
    if current_thread_id == requested_thread_id:
        return "already_on"
    if switching:
        return "switching"
    return None


def thread_resume_block_message_key(reason: ThreadResumeBlockReason) -> str:
    """Return i18n key for a blocked thread resume request."""
    return {
        "no_agent": "thread.switch_no_active_agent",
        "no_session": "thread.switch_no_active_session",
        "already_on": "thread.already_on",
        "switching": "app.thread_switch_in_progress",
    }[reason]


def thread_loading_status(thread_id: str) -> str:
    """Return the status text shown while a thread is loading."""
    return f"Loading thread: {thread_id}"


def thread_switch_failed_message(
    *,
    thread_id: str,
    error: BaseException,
    rollback_restore_failed: bool = False,
) -> str:
    """Build the user-facing thread switch failure message."""
    message = f"Failed to switch to thread {thread_id}: {error}."
    if rollback_restore_failed:
        message += " Previous thread history could not be restored."
    return message + " Use /threads to try again."
