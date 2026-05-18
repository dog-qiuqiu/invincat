"""App-bound `/goal` mode handlers."""

from __future__ import annotations

import logging
from typing import Any

from invincat_cli.goal_mode.commands import parse_goal_command
from invincat_cli.goal_mode.models import GoalState
from invincat_cli.goal_mode.prompts import (
    build_goal_kickoff_prompt,
    render_goal_status,
    wrap_goal_context,
)
from invincat_cli.goal_mode.store import GoalStore
from invincat_cli.i18n import t
from invincat_cli.widgets.messages import AppMessage, ErrorMessage, UserMessage

logger = logging.getLogger(__name__)


def _goal_store(app: Any) -> GoalStore:  # noqa: ANN401
    store = getattr(app, "_goal_store", None)
    if store is None:
        store = GoalStore.from_cwd(getattr(app, "_cwd", "."))
        app._goal_store = store
    return store


def _current_goal(app: Any) -> GoalState | None:  # noqa: ANN401
    if app._session_state is None:
        return None
    goal = getattr(app._session_state, "goal", None)
    return goal if isinstance(goal, GoalState) else None


def _set_status_bar_goal(app: Any, *, enabled: bool) -> None:  # noqa: ANN401
    status_bar = getattr(app, "_status_bar", None)
    if status_bar is not None and hasattr(status_bar, "set_goal_mode"):
        status_bar.set_goal_mode(enabled=enabled)


def _set_session_goal(app: Any, goal: GoalState | None) -> None:  # noqa: ANN401
    if app._session_state is None:
        return
    app._session_state.goal = goal
    app._session_state.goal_mode = goal is not None and goal.is_active
    _set_status_bar_goal(app, enabled=bool(app._session_state.goal_mode))


def _save_goal(app: Any, goal: GoalState) -> None:  # noqa: ANN401
    try:
        _goal_store(app).save(goal)
    except OSError:
        logger.warning("Failed to save goal state", exc_info=True)


def active_goal_for_agent(app: Any) -> GoalState | None:  # noqa: ANN401
    """Return the active goal that should be injected into agent context."""
    goal = _current_goal(app)
    if goal is None or not goal.is_active or app._session_state is None:
        return None
    if goal.thread_id != app._session_state.thread_id:
        return None
    return goal


def apply_goal_context(app: Any, message: str) -> str:  # noqa: ANN401
    """Return the model-facing message with active goal context, if any."""
    return wrap_goal_context(message, active_goal_for_agent(app))


def update_goal_token_usage(app: Any) -> None:  # noqa: ANN401
    """Persist best-effort token usage for the active goal."""
    goal = active_goal_for_agent(app)
    if goal is None:
        return
    tokens_used = int(getattr(app, "_context_tokens", 0) or 0)
    updated = goal.with_tokens_used(tokens_used)
    _set_session_goal(app, updated)
    _save_goal(app, updated)


async def restore_goal_state(app: Any) -> None:  # noqa: ANN401
    """Load the current thread's active goal from durable storage."""
    goal = sync_goal_state_for_current_thread(app)
    if goal is not None:
        await app._mount_message(
            AppMessage(t("goal.restored").format(objective=goal.objective))
        )


def sync_goal_state_for_current_thread(app: Any) -> GoalState | None:  # noqa: ANN401
    """Synchronously align in-memory goal state with the current thread id."""
    if app._session_state is None:
        return None
    goal = _goal_store(app).load(app._session_state.thread_id)
    if goal is not None and goal.is_active:
        _set_session_goal(app, goal)
        return goal
    _set_session_goal(app, None)
    return None


def is_waiting_for_goal_objective(app: Any) -> bool:  # noqa: ANN401
    if app._session_state is None:
        return False
    return bool(getattr(app._session_state, "goal_mode", False)) and (
        _current_goal(app) is None
    )


async def create_goal_from_objective(
    app: Any,  # noqa: ANN401
    objective: str,
    *,
    token_budget: int | None = None,
    start_agent: bool = True,
) -> GoalState | None:
    """Create an active goal and optionally kick off the main agent."""
    if app._session_state is None:
        await app._mount_message(ErrorMessage(t("goal.no_session")))
        return None
    objective = objective.strip()
    if not objective:
        await app._mount_message(ErrorMessage(t("goal.empty_objective")))
        return None

    current = _current_goal(app)
    if current is not None and current.is_active:
        await app._mount_message(
            ErrorMessage(t("goal.already_active").format(objective=current.objective))
        )
        return None

    goal = GoalState.create(
        objective=objective,
        thread_id=app._session_state.thread_id,
        token_budget=token_budget,
    )
    _set_session_goal(app, goal)
    _save_goal(app, goal)
    await app._mount_message(AppMessage(t("goal.created").format(objective=objective)))
    if start_agent:
        await app._send_to_agent(build_goal_kickoff_prompt(goal))
    return goal


async def handle_goal_objective_message(app: Any, message: str) -> bool:  # noqa: ANN401
    """Create a goal from the next normal chat message after bare `/goal`."""
    if not is_waiting_for_goal_objective(app):
        return False
    await app._mount_message(UserMessage(message))
    goal = await create_goal_from_objective(app, message, start_agent=True)
    if goal is None and app._session_state is not None:
        app._session_state.goal_mode = False
        _set_status_bar_goal(app, enabled=False)
    return True


async def handle_goal_command(app: Any, command: str) -> None:  # noqa: ANN401
    """Execute a `/goal` slash command."""
    parsed = parse_goal_command(command)
    await app._mount_message(UserMessage(command))

    if parsed.kind == "error":
        await app._mount_message(ErrorMessage(parsed.error or t("goal.invalid")))
        return
    if app._session_state is None:
        await app._mount_message(ErrorMessage(t("goal.no_session")))
        return

    goal = _current_goal(app)
    if parsed.kind == "status":
        if goal is None:
            app._session_state.goal_mode = True
            _set_status_bar_goal(app, enabled=True)
            await app._mount_message(AppMessage(t("goal.entered")))
            return
        await app._mount_message(AppMessage(render_goal_status(goal)))
        return

    if parsed.kind == "create" and parsed.objective is not None:
        await create_goal_from_objective(
            app,
            parsed.objective,
            token_budget=parsed.token_budget,
            start_agent=True,
        )
        return

    if parsed.kind == "complete":
        if goal is None:
            await app._mount_message(AppMessage(t("goal.none")))
            return
        completed = goal.complete(summary=parsed.objective)
        _set_session_goal(app, None)
        _save_goal(app, completed)
        await app._mount_message(AppMessage(t("goal.completed")))
        return

    if parsed.kind == "cancel":
        if goal is None:
            if getattr(app._session_state, "goal_mode", False):
                app._session_state.goal_mode = False
                _set_status_bar_goal(app, enabled=False)
                await app._mount_message(AppMessage(t("goal.exited")))
            else:
                await app._mount_message(AppMessage(t("goal.none")))
            return
        cancelled = goal.cancel(summary=parsed.objective)
        _set_session_goal(app, None)
        _save_goal(app, cancelled)
        await app._mount_message(AppMessage(t("goal.cancelled")))
        return

    if parsed.kind == "clear":
        _set_session_goal(app, None)
        _goal_store(app).delete(app._session_state.thread_id)
        await app._mount_message(AppMessage(t("goal.cleared")))
