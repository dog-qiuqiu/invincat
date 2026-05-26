"""Tests for goal mode models, parsing, storage, and prompts."""

from __future__ import annotations

from invincat_cli.goal_mode.commands import parse_goal_command
from invincat_cli.goal_mode.models import GoalState
from invincat_cli.goal_mode.prompts import wrap_goal_context
from invincat_cli.goal_mode.store import GoalStore


def test_parse_goal_command_lifecycle_actions() -> None:
    assert parse_goal_command("/goal").kind == "status"
    assert parse_goal_command("/goal status").kind == "status"
    assert parse_goal_command("/goal complete shipped").kind == "complete"
    assert parse_goal_command("/goal cancel paused").kind == "cancel"
    assert parse_goal_command("/goal clear").kind == "clear"
    assert parse_goal_command("/exit-goal").kind == "cancel"
    assert parse_goal_command("/EXIT-GOAL").kind == "cancel"


def test_parse_goal_command_create_with_budget() -> None:
    parsed = parse_goal_command('/goal "Ship MVP" --budget=1200')

    assert parsed.kind == "create"
    assert parsed.objective == "Ship MVP"
    assert parsed.token_budget == 1200


def test_parse_goal_command_invalid_budget() -> None:
    parsed = parse_goal_command("/goal Ship --budget no")

    assert parsed.kind == "error"
    assert parsed.error == "Invalid --budget value"


def test_goal_state_lifecycle_and_store(tmp_path) -> None:
    store = GoalStore(tmp_path)
    goal = GoalState.create(
        objective="Refactor plan mode",
        thread_id="thread/1",
        token_budget=200,
    )

    path = store.save(goal)
    restored = store.load("thread/1")

    assert path.name == "thread_1.json"
    assert restored == goal

    completed = goal.with_tokens_used(50).complete(summary="done")
    assert completed.status == "complete"
    assert completed.tokens_used == 50
    assert completed.completed_at is not None

    assert store.delete("thread/1") is True
    assert store.load("thread/1") is None


def test_goal_state_from_dict_ignores_non_int_like_token_values() -> None:
    state = GoalState.from_dict(
        {
            "objective": "Ship",
            "status": "active",
            "thread_id": "thread-1",
            "created_at": "created",
            "updated_at": "updated",
            "token_budget": object(),
            "tokens_used": object(),
        }
    )

    assert state.token_budget is None
    assert state.tokens_used == 0


def test_wrap_goal_context_only_for_active_goal() -> None:
    goal = GoalState.create(objective="Ship MVP", thread_id="thread-1")

    wrapped = wrap_goal_context("continue", goal)

    assert "<active_goal>" in wrapped
    assert "Ship MVP" in wrapped
    assert "User message:\ncontinue" in wrapped
    assert wrap_goal_context("continue", goal.complete()) == "continue"
