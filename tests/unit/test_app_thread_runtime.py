"""Tests for thread switching runtime helpers."""

from __future__ import annotations

from invincat_cli.app_runtime.thread_runtime import (
    capture_thread_switch_snapshot,
    should_handle_thread_switch_error_as_prefetch_failure,
    thread_loading_status,
    thread_resume_block_message_key,
    thread_resume_block_reason,
    thread_switch_banner_update,
    thread_switch_failed_message,
    thread_switch_failure_log,
    thread_switch_prefetch_failure_log,
    thread_switch_rollback_banner_update,
    thread_switch_rollback_restore_failure_log,
)


def test_thread_resume_block_reason() -> None:
    assert (
        thread_resume_block_reason(
            has_agent=False,
            has_session=False,
            current_thread_id=None,
            requested_thread_id="thread-1",
            switching=False,
        )
        == "no_agent"
    )
    assert (
        thread_resume_block_reason(
            has_agent=True,
            has_session=False,
            current_thread_id=None,
            requested_thread_id="thread-1",
            switching=False,
        )
        == "no_session"
    )
    assert (
        thread_resume_block_reason(
            has_agent=True,
            has_session=True,
            current_thread_id="thread-1",
            requested_thread_id="thread-1",
            switching=False,
        )
        == "already_on"
    )
    assert (
        thread_resume_block_reason(
            has_agent=True,
            has_session=True,
            current_thread_id="thread-1",
            requested_thread_id="thread-2",
            switching=True,
        )
        == "switching"
    )
    assert (
        thread_resume_block_reason(
            has_agent=True,
            has_session=True,
            current_thread_id="thread-1",
            requested_thread_id="thread-2",
            switching=False,
        )
        is None
    )


def test_thread_resume_block_message_key() -> None:
    assert thread_resume_block_message_key("no_agent") == (
        "thread.switch_no_active_agent"
    )
    assert thread_resume_block_message_key("no_session") == (
        "thread.switch_no_active_session"
    )
    assert thread_resume_block_message_key("already_on") == "thread.already_on"
    assert thread_resume_block_message_key("switching") == (
        "app.thread_switch_in_progress"
    )


def test_thread_loading_status() -> None:
    assert thread_loading_status("thread-1") == "Loading thread: thread-1"


def test_capture_thread_switch_snapshot() -> None:
    snapshot = capture_thread_switch_snapshot(
        lc_thread_id="lc-thread",
        session_thread_id="session-thread",
    )

    assert snapshot.lc_thread_id == "lc-thread"
    assert snapshot.session_thread_id == "session-thread"


def test_should_handle_thread_switch_error_as_prefetch_failure() -> None:
    assert should_handle_thread_switch_error_as_prefetch_failure(
        has_prefetched_payload=False,
    )
    assert not should_handle_thread_switch_error_as_prefetch_failure(
        has_prefetched_payload=True,
    )


def test_thread_switch_banner_updates() -> None:
    update = thread_switch_banner_update("thread-2")
    assert update.thread_id == "thread-2"
    assert update.missing_message == (
        "Welcome banner not found during thread switch to %s"
    )
    assert update.warn_if_missing is False

    rollback = thread_switch_rollback_banner_update("thread-1")
    assert rollback.thread_id == "thread-1"
    assert rollback.missing_message == (
        "Welcome banner not found during rollback to thread %s; "
        "banner may display stale thread ID"
    )
    assert rollback.warn_if_missing is True


def test_thread_switch_log_messages() -> None:
    assert thread_switch_prefetch_failure_log("thread-2") == (
        "Failed to prefetch history for thread thread-2"
    )
    assert thread_switch_failure_log("thread-2") == (
        "Failed to switch to thread thread-2"
    )
    assert thread_switch_rollback_restore_failure_log("thread-2") == (
        "Could not restore previous thread history after failed switch to thread-2"
    )


def test_thread_switch_failed_message() -> None:
    message = thread_switch_failed_message(
        thread_id="thread-1",
        error=RuntimeError("boom"),
    )
    assert message == (
        "Failed to switch to thread thread-1: boom. Use /threads to try again."
    )

    rollback_message = thread_switch_failed_message(
        thread_id="thread-1",
        error=RuntimeError("boom"),
        rollback_restore_failed=True,
    )
    assert "Previous thread history could not be restored." in rollback_message
