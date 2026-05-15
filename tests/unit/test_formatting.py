"""Tests for lightweight formatting helpers."""

from invincat_cli.presentation.formatting import format_duration


def test_format_duration_seconds_without_decimal_for_whole_values() -> None:
    assert format_duration(5) == "5s"
    assert format_duration(42.0) == "42s"


def test_format_duration_seconds_with_single_decimal() -> None:
    assert format_duration(2.34) == "2.3s"
    assert format_duration(2.35) == "2.4s"


def test_format_duration_minutes_and_hours() -> None:
    assert format_duration(65.4) == "1m 5s"
    assert format_duration(3661.2) == "1h 1m 1s"
