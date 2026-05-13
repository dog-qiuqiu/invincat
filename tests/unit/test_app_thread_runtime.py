"""Tests for thread switching runtime helpers."""

from __future__ import annotations

from invincat_cli.app_runtime.thread_runtime import (
    thread_loading_status,
    thread_resume_block_message_key,
    thread_resume_block_reason,
    thread_switch_failed_message,
)


def test_thread_resume_block_reason() -> None:
    assert thread_resume_block_reason(
        has_agent=False,
        has_session=False,
        current_thread_id=None,
        requested_thread_id="thread-1",
        switching=False,
    ) == "no_agent"
    assert thread_resume_block_reason(
        has_agent=True,
        has_session=False,
        current_thread_id=None,
        requested_thread_id="thread-1",
        switching=False,
    ) == "no_session"
    assert thread_resume_block_reason(
        has_agent=True,
        has_session=True,
        current_thread_id="thread-1",
        requested_thread_id="thread-1",
        switching=False,
    ) == "already_on"
    assert thread_resume_block_reason(
        has_agent=True,
        has_session=True,
        current_thread_id="thread-1",
        requested_thread_id="thread-2",
        switching=True,
    ) == "switching"
    assert thread_resume_block_reason(
        has_agent=True,
        has_session=True,
        current_thread_id="thread-1",
        requested_thread_id="thread-2",
        switching=False,
    ) is None


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


def test_thread_switch_failed_message() -> None:
    message = thread_switch_failed_message(
        thread_id="thread-1",
        error=RuntimeError("boom"),
    )
    assert message == (
        "Failed to switch to thread thread-1: boom. "
        "Use /threads to try again."
    )

    rollback_message = thread_switch_failed_message(
        thread_id="thread-1",
        error=RuntimeError("boom"),
        rollback_restore_failed=True,
    )
    assert "Previous thread history could not be restored." in rollback_message
