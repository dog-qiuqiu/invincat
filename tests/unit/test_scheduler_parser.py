"""Unit tests for schedule expression parsing."""

from __future__ import annotations

import builtins

import pytest

from invincat_cli.scheduler.parser import describe_schedule, parse_schedule


@pytest.mark.parametrize(
    ("expr", "message"),
    [
        ("daily 8am", "Invalid time format"),
        ("daily 24:00", "Time out of range"),
        ("daily 08:60", "Time out of range"),
        ("interval 0h", "Hour interval"),
        ("interval 24h", "Hour interval"),
        ("interval 0m", "Minute interval"),
        ("interval 60m", "Minute interval"),
        ("cron * * *", "Invalid cron expression after"),
        ("60 * * * *", "Invalid cron expression"),
        ("weekly", "requires a weekday"),
        ("weekly someday", "Unknown weekday"),
        ("monthly", "requires a day-of-month"),
        ("monthly first", "Invalid day-of-month"),
        ("monthly 0", "Day-of-month"),
        ("daily 08:00 extra", "daily accepts"),
        ("weekly mon 08:00 extra", "weekly accepts"),
        ("monthly 1 08:00 extra", "monthly accepts"),
        ("yearly 08:00", "Unrecognised"),
    ],
)
def test_parse_schedule_rejects_invalid_inputs(expr: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_schedule(expr)


def test_parse_schedule_accepts_long_weekday_and_interval_aliases() -> None:
    assert parse_schedule("* * * * *") == "* * * * *"
    assert parse_schedule("cron 0 9 * * 1") == "0 9 * * 1"
    assert parse_schedule("daily") == "0 8 * * *"
    assert parse_schedule("daily 09:30") == "30 9 * * *"
    assert parse_schedule("weekly thursday") == "0 8 * * 4"
    assert parse_schedule("weekly fri 10:45") == "45 10 * * 5"
    assert parse_schedule("monthly 15 09:30") == "30 9 15 * *"
    assert parse_schedule("interval 2hours") == "0 */2 * * *"
    assert parse_schedule("interval 15minutes") == "*/15 * * * *"


def test_parse_schedule_allows_cron_validation_dependency_to_be_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "croniter":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert parse_schedule("cron 60 * * * *") == "60 * * * *"


@pytest.mark.parametrize(
    ("cron", "expected"),
    [
        ("not cron", "not cron"),
        ("0 8 * * *", "daily 08:00"),
        ("0 */2 * * *", "every 2 hours"),
        ("*/15 * * * *", "every 15 minutes"),
        ("x 8 * * *", "x 8 * * *"),
        ("0 x * * 1", "0 x * * 1"),
        ("0 x 2 * *", "0 x 2 * *"),
        ("0 8 * * 7", "weekly 7 08:00"),
        ("1 2 3 4 5", "1 2 3 4 5"),
    ],
)
def test_describe_schedule_falls_back_for_unrecognised_shapes(
    cron: str,
    expected: str,
) -> None:
    assert describe_schedule(cron) == expected


def test_describe_schedule_formats_monthly() -> None:
    assert describe_schedule("30 9 15 * *") == "monthly 15 09:30"
