"""Unit tests for schedule display helpers."""

from __future__ import annotations

from datetime import datetime

from invincat_cli.scheduler.display import format_schedule_time_for_display


def test_format_schedule_time_for_display_handles_missing_and_unparseable() -> None:
    assert format_schedule_time_for_display(None, "UTC") == "unknown"
    assert format_schedule_time_for_display("", "UTC", missing="-") == "-"
    assert format_schedule_time_for_display("not-a-date", "UTC") == "not-a-date"
    assert format_schedule_time_for_display(123, "UTC") == "123"


def test_format_schedule_time_for_display_assumes_naive_datetime_is_utc() -> None:
    assert (
        format_schedule_time_for_display(
            datetime(2026, 5, 17, 14, 9),
            "Asia/Shanghai",
        )
        == "2026-05-17T22:09+08:00"
    )
