"""Tests for lightweight session statistics helpers."""

from invincat_cli.core.session_stats import SessionStats, format_token_count


def test_session_stats_record_request_accumulates_totals_and_model_breakdown() -> None:
    stats = SessionStats()

    stats.record_request("gpt-test", 100, 25)
    stats.record_request("", 10, 5)

    assert stats.request_count == 2
    assert stats.input_tokens == 110
    assert stats.output_tokens == 30
    assert set(stats.per_model) == {"gpt-test"}
    assert stats.per_model["gpt-test"].request_count == 1
    assert stats.per_model["gpt-test"].input_tokens == 100
    assert stats.per_model["gpt-test"].output_tokens == 25


def test_session_stats_merge_combines_totals_and_per_model_values() -> None:
    left = SessionStats(wall_time_seconds=1.5)
    left.record_request("gpt-a", 100, 10)
    right = SessionStats(wall_time_seconds=2.5)
    right.record_request("gpt-a", 50, 5)
    right.record_request("gpt-b", 25, 2)

    left.merge(right)

    assert left.request_count == 3
    assert left.input_tokens == 175
    assert left.output_tokens == 17
    assert left.wall_time_seconds == 4.0
    assert left.per_model["gpt-a"].request_count == 2
    assert left.per_model["gpt-a"].input_tokens == 150
    assert left.per_model["gpt-b"].output_tokens == 2


def test_format_token_count_uses_short_suffixes() -> None:
    assert format_token_count(999) == "999"
    assert format_token_count(1000) == "1.0K"
    assert format_token_count(12_345) == "12.3K"
    assert format_token_count(1_250_000) == "1.2M"
