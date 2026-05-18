"""Tests for app-bound goal handlers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from invincat_cli.app_runtime import goal_handlers
from invincat_cli.goal_mode.models import GoalState
from invincat_cli.goal_mode.store import GoalStore


class _Status:
    def __init__(self) -> None:
        self.goal_states: list[bool] = []

    def set_goal_mode(self, *, enabled: bool) -> None:
        self.goal_states.append(enabled)


class _App:
    def __init__(self, tmp_path) -> None:  # noqa: ANN001
        self._session_state = SimpleNamespace(
            thread_id="thread-1",
            goal_mode=False,
            goal=None,
        )
        self._goal_store = GoalStore(tmp_path)
        self._cwd = str(tmp_path)
        self._status_bar = _Status()
        self._context_tokens = 0
        self.messages: list[object] = []
        self.sent: list[str] = []

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    async def _send_to_agent(self, message: str, **_kwargs: object) -> bool:
        self.sent.append(message)
        return True


def test_create_goal_from_objective_saves_and_kicks_off(tmp_path) -> None:
    app = _App(tmp_path)

    goal = asyncio.run(
        goal_handlers.create_goal_from_objective(
            app,
            "Ship the MVP",
            token_budget=100,
        )
    )

    assert goal is not None
    assert app._session_state.goal.objective == "Ship the MVP"
    assert app._session_state.goal_mode is True
    assert app._status_bar.goal_states == [True]
    assert app.sent and "Ship the MVP" in app.sent[0]
    assert app._goal_store.load("thread-1") == goal


def test_restore_goal_state_only_restores_active_goal(tmp_path) -> None:
    app = _App(tmp_path)
    goal = GoalState.create(objective="Keep going", thread_id="thread-1")
    app._goal_store.save(goal)

    asyncio.run(goal_handlers.restore_goal_state(app))

    assert app._session_state.goal == goal
    assert app._session_state.goal_mode is True
    assert app._status_bar.goal_states == [True]

    app2 = _App(tmp_path)
    app2._goal_store.save(goal.complete())
    asyncio.run(goal_handlers.restore_goal_state(app2))

    assert app2._session_state.goal is None
    assert app2._session_state.goal_mode is False


def test_sync_goal_state_for_current_thread_ignores_completed_or_mismatched_goal(
    tmp_path,
) -> None:
    app = _App(tmp_path)
    current = GoalState.create(objective="Current", thread_id="thread-1")
    other = GoalState.create(objective="Other", thread_id="other-thread")
    app._goal_store.save(current.complete())
    app._goal_store.save(other)

    assert goal_handlers.sync_goal_state_for_current_thread(app) is None
    assert app._session_state.goal is None
    assert app._session_state.goal_mode is False

    app._session_state.goal = other
    app._session_state.goal_mode = True
    assert goal_handlers.active_goal_for_agent(app) is None
    assert goal_handlers.apply_goal_context(app, "continue") == "continue"


def test_handle_goal_objective_message_waiting_path(tmp_path) -> None:
    app = _App(tmp_path)
    app._session_state.goal_mode = True

    handled = asyncio.run(
        goal_handlers.handle_goal_objective_message(app, "Build the feature")
    )

    assert handled is True
    assert app._session_state.goal.objective == "Build the feature"
    assert app.sent


def test_handle_goal_command_complete_cancel_clear_and_errors(tmp_path) -> None:
    app = _App(tmp_path)

    asyncio.run(goal_handlers.handle_goal_command(app, "/goal complete done"))
    assert app._session_state.goal is None
    assert app._session_state.goal_mode is False

    app._session_state.goal = GoalState.create(
        objective="Complete it",
        thread_id="thread-1",
    )
    asyncio.run(goal_handlers.handle_goal_command(app, "/goal complete done"))
    completed = app._goal_store.load("thread-1")
    assert completed is not None
    assert completed.status == "complete"
    assert completed.summary == "done"
    assert app._session_state.goal is None
    assert app._status_bar.goal_states[-1] is False

    app._session_state.goal = GoalState.create(
        objective="Cancel it",
        thread_id="thread-1",
    )
    asyncio.run(goal_handlers.handle_goal_command(app, "/goal cancel because blocked"))
    cancelled = app._goal_store.load("thread-1")
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.summary == "because blocked"
    assert app._session_state.goal is None

    app._goal_store.save(GoalState.create(objective="Clear it", thread_id="thread-1"))
    app._session_state.goal = app._goal_store.load("thread-1")
    asyncio.run(goal_handlers.handle_goal_command(app, "/goal clear"))
    assert app._goal_store.load("thread-1") is None
    assert app._session_state.goal is None

    app_no_session = _App(tmp_path)
    app_no_session._session_state = None
    asyncio.run(goal_handlers.handle_goal_command(app_no_session, "/goal Ship it"))
    assert app_no_session.messages


def test_create_goal_rejects_empty_and_duplicate_objective(tmp_path) -> None:
    app = _App(tmp_path)

    assert asyncio.run(goal_handlers.create_goal_from_objective(app, "  ")) is None
    assert app._session_state.goal is None

    app._session_state.goal = GoalState.create(
        objective="Existing",
        thread_id="thread-1",
    )
    app._session_state.goal_mode = True
    assert (
        asyncio.run(goal_handlers.create_goal_from_objective(app, "Replacement"))
        is None
    )
    assert app._session_state.goal.objective == "Existing"


def test_apply_goal_context_and_token_update(tmp_path) -> None:
    app = _App(tmp_path)
    goal = GoalState.create(objective="Ship MVP", thread_id="thread-1")
    app._session_state.goal = goal
    app._session_state.goal_mode = True
    app._context_tokens = 321

    wrapped = goal_handlers.apply_goal_context(app, "continue")
    goal_handlers.update_goal_token_usage(app)

    assert "<active_goal>" in wrapped
    assert "Ship MVP" in wrapped
    assert app._session_state.goal.tokens_used == 321
    assert app._goal_store.load("thread-1").tokens_used == 321
