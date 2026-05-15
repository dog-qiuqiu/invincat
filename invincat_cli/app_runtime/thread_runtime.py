"""Thread switching runtime helpers for the Textual app."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ThreadResumeBlockReason = Literal[
    "no_agent",
    "no_session",
    "already_on",
    "switching",
]


@dataclass(frozen=True, slots=True)
class ThreadSwitchSnapshot:
    """Previous thread identifiers kept for rollback."""

    lc_thread_id: str
    session_thread_id: str


@dataclass(frozen=True, slots=True)
class ThreadBannerUpdate:
    """Arguments for updating the welcome banner during thread switches."""

    thread_id: str
    missing_message: str
    warn_if_missing: bool


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


def capture_thread_switch_snapshot(
    *,
    lc_thread_id: str,
    session_thread_id: str,
) -> ThreadSwitchSnapshot:
    """Capture active thread identifiers before switching threads."""
    return ThreadSwitchSnapshot(
        lc_thread_id=lc_thread_id,
        session_thread_id=session_thread_id,
    )


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


def should_handle_thread_switch_error_as_prefetch_failure(
    *,
    has_prefetched_payload: bool,
) -> bool:
    """Return whether a switch error happened before history prefetch succeeded."""
    return not has_prefetched_payload


def thread_switch_banner_update(thread_id: str) -> ThreadBannerUpdate:
    """Build banner update args for a successful thread ID switch."""
    return ThreadBannerUpdate(
        thread_id=thread_id,
        missing_message="Welcome banner not found during thread switch to %s",
        warn_if_missing=False,
    )


def thread_switch_rollback_banner_update(thread_id: str) -> ThreadBannerUpdate:
    """Build banner update args for rolling back to the previous thread ID."""
    return ThreadBannerUpdate(
        thread_id=thread_id,
        missing_message=(
            "Welcome banner not found during rollback to thread %s; "
            "banner may display stale thread ID"
        ),
        warn_if_missing=True,
    )


def thread_switch_prefetch_failure_log(thread_id: str) -> str:
    """Return log message for prefetch failures."""
    return f"Failed to prefetch history for thread {thread_id}"


def thread_switch_failure_log(thread_id: str) -> str:
    """Return log message for post-prefetch switch failures."""
    return f"Failed to switch to thread {thread_id}"


def thread_switch_rollback_restore_failure_log(thread_id: str) -> str:
    """Return log message for failed rollback history restoration."""
    return (
        f"Could not restore previous thread history after failed switch to {thread_id}"
    )


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
