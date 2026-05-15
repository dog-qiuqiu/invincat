"""Tests for app exception helpers."""

from __future__ import annotations

from invincat_cli.app_runtime.errors import (
    format_exception_details,
    is_scheduled_retryable_error,
    looks_like_masked_internal_error,
)


class _RemoteError(Exception):
    pass


class _DataError(Exception):
    def __init__(self, data: dict) -> None:
        super().__init__("wrapped")
        self.data = data


def test_format_exception_details_prefers_structured_payload() -> None:
    text = format_exception_details(_RemoteError({"message": "boom", "code": 500}))

    assert '"code": 500' in text
    assert '"message": "boom"' in text


def test_format_exception_details_uses_data_attribute_payload() -> None:
    text = format_exception_details(_DataError({"message": "from data"}))

    assert '"message": "from data"' in text


def test_format_exception_details_handles_non_json_payload() -> None:
    text = format_exception_details(_RemoteError({"items": {1, 2}}))

    assert "{1, 2}" in text


def test_format_exception_details_falls_back_to_exception_type_for_empty_text() -> None:
    assert format_exception_details(Exception()) == "Exception"


def test_format_exception_details_does_not_duplicate_type_name() -> None:
    assert format_exception_details(ValueError("ValueError: bad")) == "ValueError: bad"


def test_masked_internal_error_detection() -> None:
    assert looks_like_masked_internal_error(
        _RemoteError({"message": "An internal error occurred"})
    )
    assert looks_like_masked_internal_error(RuntimeError("An internal error occurred"))
    assert not looks_like_masked_internal_error(ValueError("specific failure"))


def test_scheduled_retryable_error_detection() -> None:
    assert is_scheduled_retryable_error(TimeoutError("timed out"))
    assert is_scheduled_retryable_error(
        RuntimeError("HTTP 503 temporarily unavailable")
    )
    assert not is_scheduled_retryable_error(ValueError("bad user input"))


def test_scheduled_retryable_error_detection_uses_class_name_and_payload() -> None:
    class RateLimitError(Exception):
        pass

    assert is_scheduled_retryable_error(RateLimitError("quota"))
    assert is_scheduled_retryable_error(_RemoteError({"status": 429}))
