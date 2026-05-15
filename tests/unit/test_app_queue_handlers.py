from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace

from invincat_cli.app_runtime import queue_handlers
from invincat_cli.app_runtime.state import QueuedMessage
from invincat_cli.widgets.messages import ErrorMessage


class AsyncWidget:
    def __init__(self) -> None:
        self.removed = False

    async def remove(self) -> None:
        self.removed = True


class SyncWidget:
    def __init__(self) -> None:
        self.removed = False

    def remove(self) -> None:
        self.removed = True


class QueueApp:
    def __init__(self) -> None:
        self._processing_pending = False
        self._pending_messages = deque()
        self._queued_widgets = deque()
        self._deferred_actions = deque()
        self._exit = False
        self._message_store = SimpleNamespace(total_count=7)
        self._agent_running = False
        self._shell_running = False
        self._chat_input = SimpleNamespace(value="")
        self._active_scheduled_run = None
        self._scheduled_run_message_offset = None
        self._scheduled_turn_status = None
        self._scheduled_turn_error = None
        self._scheduled_turn_retry_used = None
        self.processed: list[tuple[str, str]] = []
        self.messages: list[object] = []
        self.notifications: list[tuple[str, dict[str, object]]] = []
        self.failed_runs: list[str] = []
        self.recursed = False

    async def _process_message(self, text: str, mode: str) -> None:
        self.processed.append((text, mode))

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    async def _process_next_from_queue(self) -> None:
        self.recursed = True

    def _finish_active_scheduled_run_as_failed(self, error: str) -> None:
        self.failed_runs.append(error)

    def notify(self, message: str, **kwargs: object) -> None:
        self.notifications.append((message, kwargs))


def test_process_next_from_queue_runs_message_and_scheduled_state() -> None:
    app = QueueApp()
    widget = AsyncWidget()
    app._pending_messages.append(
        QueuedMessage(
            text="run",
            mode="normal",
            scheduled_run_id="run-1",
            scheduled_task_id="task-1",
        )
    )
    app._queued_widgets.append(widget)

    asyncio.run(queue_handlers.process_next_from_queue(app))

    assert app.processed == [("run", "normal")]
    assert widget.removed is True
    assert app._active_scheduled_run == ("run-1", "task-1")
    assert app._scheduled_run_message_offset == 7
    assert app._scheduled_turn_status == "success"
    assert app._scheduled_turn_error is None
    assert app._scheduled_turn_retry_used is False
    assert app._processing_pending is False


def test_process_next_from_queue_mounts_error_on_failure() -> None:
    app = QueueApp()
    app._pending_messages.append(QueuedMessage(text="broken message", mode="normal"))

    async def fail(_text: str, _mode: str) -> None:
        raise RuntimeError("boom")

    app._process_message = fail

    asyncio.run(queue_handlers.process_next_from_queue(app))

    assert app.failed_runs == ["boom"]
    assert isinstance(app.messages[-1], ErrorMessage)
    assert app._processing_pending is False


def test_process_next_from_queue_continues_when_more_messages_remain() -> None:
    app = QueueApp()
    app._pending_messages.extend(
        [
            QueuedMessage(text="first", mode="normal"),
            QueuedMessage(text="second", mode="shell"),
        ]
    )

    asyncio.run(queue_handlers.process_next_from_queue(app))

    assert app.processed == [("first", "normal")]
    assert app.recursed is True


def test_process_next_from_queue_skips_when_busy_or_exiting() -> None:
    app = QueueApp()
    app._processing_pending = True
    app._pending_messages.append(QueuedMessage(text="run", mode="normal"))

    asyncio.run(queue_handlers.process_next_from_queue(app))

    assert app.processed == []


def test_pop_last_queued_message_noops_when_empty() -> None:
    app = QueueApp()

    queue_handlers.pop_last_queued_message(app)

    assert app.notifications == []


def test_pop_last_queued_message_restores_empty_input() -> None:
    app = QueueApp()
    widget = SyncWidget()
    app._pending_messages.append(QueuedMessage(text="restore me", mode="normal"))
    app._queued_widgets.append(widget)

    queue_handlers.pop_last_queued_message(app)

    assert widget.removed is True
    assert app._chat_input.value == "restore me"
    assert app.notifications[-1][1]["timeout"] == 2


def test_pop_last_queued_message_handles_mismatch_and_missing_input() -> None:
    mismatched = QueueApp()
    mismatched._pending_messages.append(QueuedMessage(text="run", mode="normal"))

    queue_handlers.pop_last_queued_message(mismatched)

    assert len(mismatched._pending_messages) == 1

    no_input = QueueApp()
    no_input._chat_input = None
    widget = SyncWidget()
    no_input._pending_messages.append(QueuedMessage(text="discard me", mode="normal"))
    no_input._queued_widgets.append(widget)

    queue_handlers.pop_last_queued_message(no_input)

    assert widget.removed is True
    assert no_input.notifications[-1][1]["timeout"] == 2


def test_pop_last_queued_message_discards_when_input_not_empty() -> None:
    app = QueueApp()
    app._chat_input.value = "existing"
    widget = SyncWidget()
    app._pending_messages.append(QueuedMessage(text="queued", mode="normal"))
    app._queued_widgets.append(widget)

    queue_handlers.pop_last_queued_message(app)

    assert app._chat_input.value == "existing"
    assert app.notifications[-1][1]["timeout"] == 3


def test_discard_queue_clears_pending_widgets_and_deferred_actions() -> None:
    app = QueueApp()
    widgets = [SyncWidget(), SyncWidget()]
    app._pending_messages.extend(
        [
            QueuedMessage(text="first", mode="normal"),
            QueuedMessage(text="second", mode="shell"),
        ]
    )
    app._queued_widgets.extend(widgets)
    app._deferred_actions.extend([object(), object()])

    queue_handlers.discard_queue(app)

    assert not app._pending_messages
    assert not app._queued_widgets
    assert not app._deferred_actions
    assert [widget.removed for widget in widgets] == [True, True]
