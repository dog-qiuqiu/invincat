"""Tests for app exception helpers."""

from __future__ import annotations

from invincat_cli.app_errors import (
    format_exception_details,
    is_scheduled_retryable_error,
    looks_like_masked_internal_error,
)


class _RemoteError(Exception):
    pass


def test_format_exception_details_prefers_structured_payload() -> None:
    text = format_exception_details(
        _RemoteError({"message": "boom", "code": 500})
    )

    assert '"code": 500' in text
    assert '"message": "boom"' in text


def test_masked_internal_error_detection() -> None:
    assert looks_like_masked_internal_error(
        _RemoteError({"message": "An internal error occurred"})
    )
    assert not looks_like_masked_internal_error(ValueError("specific failure"))


def test_scheduled_retryable_error_detection() -> None:
    assert is_scheduled_retryable_error(TimeoutError("timed out"))
    assert is_scheduled_retryable_error(RuntimeError("HTTP 503 temporarily unavailable"))
    assert not is_scheduled_retryable_error(ValueError("bad user input"))
