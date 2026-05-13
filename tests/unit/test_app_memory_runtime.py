"""Tests for memory and offload runtime helpers."""

from __future__ import annotations

from pathlib import Path

from invincat_cli.app_runtime.memory import (
    AUTO_OFFLOAD_COOLDOWN_SECONDS,
    build_auto_offload_message,
    build_offload_budget_cache_key,
    build_offload_success_message,
    build_offload_threshold_not_met_message,
    format_memory_update_success,
    resolve_auto_offload_decision,
    resolve_memory_update_notification,
)


def test_resolve_auto_offload_decision() -> None:
    assert resolve_auto_offload_decision(
        tokens_approximate=False,
        now=10.0,
        cooldown_until=0.0,
        context_tokens=85,
        context_limit=100,
    ) is not None

    decision = resolve_auto_offload_decision(
        tokens_approximate=False,
        now=10.0,
        cooldown_until=0.0,
        context_tokens=85,
        context_limit=100,
    )

    assert decision is not None
    assert decision.usage_percent == 85
    assert decision.cooldown_until == 10.0 + AUTO_OFFLOAD_COOLDOWN_SECONDS
    assert "85% full" in build_auto_offload_message(decision)


def test_resolve_auto_offload_decision_skips_ineligible_state() -> None:
    assert resolve_auto_offload_decision(
        tokens_approximate=True,
        now=10.0,
        cooldown_until=0.0,
        context_tokens=90,
        context_limit=100,
    ) is None
    assert resolve_auto_offload_decision(
        tokens_approximate=False,
        now=10.0,
        cooldown_until=20.0,
        context_tokens=90,
        context_limit=100,
    ) is None
    assert resolve_auto_offload_decision(
        tokens_approximate=False,
        now=10.0,
        cooldown_until=0.0,
        context_tokens=70,
        context_limit=100,
    ) is None


def test_resolve_memory_update_notification_single_path() -> None:
    notification = resolve_memory_update_notification(
        ["/Users/example/memory_project.json"],
        home=Path("/Users/example"),
    )

    assert notification is not None
    assert notification.count == 1
    assert notification.short_path == "~/memory_project.json"
    assert format_memory_update_success(
        notification,
        single_template="Updated {path}",
        multiple_template="Updated {n} files",
    ) == "Updated ~/memory_project.json"


def test_resolve_memory_update_notification_multiple_paths() -> None:
    notification = resolve_memory_update_notification(
        ["/tmp/a.json", "/tmp/b.json"],
        home=Path("/Users/example"),
    )

    assert notification is not None
    assert notification.count == 2
    assert notification.short_path is None
    assert format_memory_update_success(
        notification,
        single_template="Updated {path}",
        multiple_template="Updated {n} files",
    ) == "Updated 2 files"


def test_build_offload_budget_cache_key_sorts_profile_override() -> None:
    assert build_offload_budget_cache_key(
        model_provider="openai",
        model_name="gpt-test",
        model_context_limit=200_000,
        profile_override={"z": 2, "a": 1},
    ) == ("openai", "gpt-test", 200_000, (("a", 1), ("z", 2)))


def test_build_offload_threshold_not_met_message_for_system_overhead() -> None:
    message = build_offload_threshold_not_met_message(
        conversation_tokens=2_000,
        total_context_tokens=210_000,
        context_limit=200_000,
        budget_str="20.0K",
    )

    assert "conversation is only ~2.0K tokens" in message
    assert "system overhead" in message


def test_build_offload_threshold_not_met_message_for_budget() -> None:
    message = build_offload_threshold_not_met_message(
        conversation_tokens=10_000,
        total_context_tokens=20_000,
        context_limit=200_000,
        budget_str="20.0K",
    )

    assert "conversation (~10.0K tokens)" in message
    assert "retention budget (20.0K)" in message


def test_build_offload_success_message() -> None:
    assert build_offload_success_message(
        messages_offloaded=3,
        tokens_before=100_000,
        tokens_after=40_000,
        pct_decrease=60,
        messages_kept=5,
    ) == (
        "Offloaded 3 older messages, freeing up context window space.\n"
        "Context: 100.0K \u2192 40.0K tokens (60% decrease), 5 messages kept."
    )
