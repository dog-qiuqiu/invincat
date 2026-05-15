from __future__ import annotations

import asyncio

from invincat_cli.app_runtime import deferred_handlers
from invincat_cli.app_runtime.state import DeferredAction
from invincat_cli.widgets.messages import ErrorMessage


class DeferredApp:
    def __init__(self) -> None:
        self._deferred_actions: list[DeferredAction] = []
        self._connecting = False
        self._pending_plan_handoff_prompt = None
        self._agent_running = False
        self._shell_running = False
        self.messages: list[object] = []
        self.drained = False
        self.handoffs: list[object] = []

    async def _drain_deferred_actions(self) -> None:
        self.drained = True

    async def _execute_plan_handoff(self, prompt: object) -> None:
        self.handoffs.append(prompt)

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)


def action(kind: str, execute) -> DeferredAction:
    return DeferredAction(kind=kind, execute=execute)


def test_defer_action_replaces_existing_kind() -> None:
    app = DeferredApp()

    async def first() -> None:
        return None

    async def second() -> None:
        return None

    deferred_handlers.defer_action(app, action("thread_switch", first))
    deferred_handlers.defer_action(app, action("thread_switch", second))

    assert len(app._deferred_actions) == 1
    assert app._deferred_actions[0].execute is second


def test_maybe_drain_deferred_skips_while_connecting() -> None:
    app = DeferredApp()
    app._connecting = True

    asyncio.run(deferred_handlers.maybe_drain_deferred(app))

    assert app.drained is False


def test_maybe_drain_deferred_executes_pending_plan_handoff() -> None:
    app = DeferredApp()
    prompt = object()
    app._pending_plan_handoff_prompt = prompt

    asyncio.run(deferred_handlers.maybe_drain_deferred(app))

    assert app.drained is True
    assert app.handoffs == [prompt]
    assert app._pending_plan_handoff_prompt is None


def test_maybe_drain_deferred_restores_prompt_on_handoff_failure() -> None:
    app = DeferredApp()
    prompt = object()
    app._pending_plan_handoff_prompt = prompt

    async def fail(_prompt: object) -> None:
        raise RuntimeError("handoff failed")

    app._execute_plan_handoff = fail

    try:
        asyncio.run(deferred_handlers.maybe_drain_deferred(app))
    except RuntimeError:
        pass

    assert app._pending_plan_handoff_prompt is prompt


def test_drain_deferred_actions_executes_and_reports_failures() -> None:
    app = DeferredApp()
    calls: list[str] = []

    async def ok() -> None:
        calls.append("ok")

    async def fail() -> None:
        raise RuntimeError("deferred failed")

    app._deferred_actions.extend(
        [
            action("model_switch", ok),
            action("thread_switch", fail),
        ]
    )

    asyncio.run(deferred_handlers.drain_deferred_actions(app))

    assert calls == ["ok"]
    assert app._deferred_actions == []
    assert isinstance(app.messages[-1], ErrorMessage)
