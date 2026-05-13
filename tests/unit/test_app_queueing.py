"""Tests for input queueing decisions."""

from __future__ import annotations

from invincat_cli.app_queueing import can_bypass_busy_queue


def test_can_bypass_busy_queue_for_immediate_ui_bare_commands() -> None:
    assert can_bypass_busy_queue(
        "/model",
        connecting=False,
        agent_running=True,
        shell_running=False,
    )
    assert not can_bypass_busy_queue(
        "/model openai:gpt-test",
        connecting=False,
        agent_running=True,
        shell_running=False,
    )


def test_can_bypass_busy_queue_for_plan_only_bare_form() -> None:
    assert can_bypass_busy_queue(
        "/plan",
        connecting=False,
        agent_running=True,
        shell_running=False,
    )
    assert not can_bypass_busy_queue(
        "/plan refactor this",
        connecting=False,
        agent_running=True,
        shell_running=False,
    )


def test_can_bypass_busy_queue_for_connecting_only_commands() -> None:
    assert can_bypass_busy_queue(
        "/version",
        connecting=True,
        agent_running=False,
        shell_running=False,
    )
    assert not can_bypass_busy_queue(
        "/version",
        connecting=True,
        agent_running=True,
        shell_running=False,
    )


def test_can_bypass_busy_queue_for_side_effect_free_commands() -> None:
    assert can_bypass_busy_queue(
        "/docs",
        connecting=False,
        agent_running=True,
        shell_running=False,
    )
    assert not can_bypass_busy_queue(
        "/unknown",
        connecting=False,
        agent_running=True,
        shell_running=False,
    )
