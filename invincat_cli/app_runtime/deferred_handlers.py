"""Deferred action queue helpers for the Textual app."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

from invincat_cli.app_runtime.state import DeferredAction
from invincat_cli.widgets.messages import ErrorMessage

logger = logging.getLogger(__name__)


def defer_action(app: Any, action: DeferredAction) -> None:  # noqa: ANN401
    """Queue a deferred action, replacing any existing action of the same kind."""
    app._deferred_actions = [
        a for a in app._deferred_actions if a.kind != action.kind
    ]
    app._deferred_actions.append(action)


async def maybe_drain_deferred(app: Any) -> None:  # noqa: ANN401
    """Drain deferred actions unless a server connection is still in progress."""
    if app._connecting:
        return
    await app._drain_deferred_actions()
    if app._pending_plan_handoff_prompt and not (
        app._agent_running or app._shell_running or app._connecting
    ):
        prompt = app._pending_plan_handoff_prompt
        app._pending_plan_handoff_prompt = None
        try:
            await app._execute_plan_handoff(prompt)
        except Exception:
            app._pending_plan_handoff_prompt = prompt
            raise


async def drain_deferred_actions(app: Any) -> None:  # noqa: ANN401
    """Execute deferred actions queued while busy."""
    while app._deferred_actions:
        action = app._deferred_actions.pop(0)
        try:
            await action.execute()
        except Exception:
            logger.exception(
                "Failed to execute deferred action %r (callable=%r)",
                action.kind,
                action.execute,
            )
            label = action.kind.replace("_", " ")
            with suppress(Exception):
                await app._mount_message(
                    ErrorMessage(
                        f"Deferred {label} failed unexpectedly. "
                        "You may need to retry the operation."
                    )
                )
