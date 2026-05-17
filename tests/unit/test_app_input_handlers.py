from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace

from invincat_cli.app_runtime import input_handlers
from invincat_cli.app_runtime.state import QueuedMessage
from invincat_cli.goal_mode.store import GoalStore
from invincat_cli.widgets.messages import QueuedUserMessage, UserMessage


class InputApp:
    def __init__(self) -> None:
        self._session_state = SimpleNamespace(
            thread_id="thread-1",
            plan_mode=False,
            goal_mode=False,
            goal=None,
        )
        self._cwd = "."
        self._goal_store = None
        self._status_bar = None
        self._connecting = False
        self._agent_running = False
        self._shell_running = False
        self._thread_switching = False
        self._quit_pending = True
        self._pending_messages = deque()
        self._queued_widgets = deque()
        self.messages: list[object] = []
        self.notifications: list[tuple[str, dict[str, object]]] = []
        self.shell_commands: list[str] = []
        self.commands: list[str] = []
        self.processed: list[tuple[str, str]] = []
        self.sent: list[tuple[str, object, object]] = []
        self.planner_messages: list[str] = []
        self.plan_reset = False
        self.exited = False

    async def _handle_shell_command(self, command: str) -> None:
        self.shell_commands.append(command)

    async def _handle_command(self, command: str) -> None:
        self.commands.append(command)

    async def _process_message(self, value: str, mode: str) -> None:
        self.processed.append((value, mode))

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    async def _send_to_agent(
        self,
        message: str,
        *,
        on_text_delta: object | None = None,
        on_wecom_file_request: object | None = None,
    ) -> None:
        self.sent.append((message, on_text_delta, on_wecom_file_request))

    async def _run_planner(self, message: str) -> bool:
        self.planner_messages.append(message)
        return True

    def _reset_plan_mode_state(self) -> None:
        self.plan_reset = True

    def _can_bypass_queue(self, value: str) -> bool:
        return value == "/version"

    def notify(self, message: str, **kwargs: object) -> None:
        self.notifications.append((message, kwargs))

    def exit(self) -> None:
        self.exited = True


def event(value: str, mode: str = "normal") -> SimpleNamespace:
    return SimpleNamespace(value=value, mode=mode)


def test_process_message_routes_by_mode() -> None:
    app = InputApp()

    asyncio.run(input_handlers.process_message(app, "!pwd", "shell"))
    asyncio.run(input_handlers.process_message(app, "/help", "command"))
    asyncio.run(input_handlers.process_message(app, "hello", "normal"))
    asyncio.run(input_handlers.process_message(app, "fallback", "unknown"))

    assert app.shell_commands == ["pwd"]
    assert app.commands == ["/help"]
    assert [getattr(message, "_content", None) for message in app.messages] == [
        "hello",
        "fallback",
    ]
    assert [sent[0] for sent in app.sent] == ["hello", "fallback"]


def test_handle_user_message_routes_to_planner_and_resets_on_failure() -> None:
    app = InputApp()
    app._session_state.plan_mode = True

    asyncio.run(input_handlers.handle_user_message(app, "plan this"))

    assert isinstance(app.messages[-1], UserMessage)
    assert app.planner_messages == ["plan this"]
    assert app.plan_reset is False

    async def planner_failure(_message: str) -> bool:
        return False

    app._run_planner = planner_failure
    asyncio.run(input_handlers.handle_user_message(app, "plan failed"))

    assert app.plan_reset is True


def test_handle_user_message_passes_agent_callbacks() -> None:
    app = InputApp()
    on_text_delta = object()
    on_wecom_file_request = object()

    asyncio.run(
        input_handlers.handle_user_message(
            app,
            "hello",
            on_text_delta=on_text_delta,
            on_wecom_file_request=on_wecom_file_request,
        )
    )

    assert app.sent == [("hello", on_text_delta, on_wecom_file_request)]


def test_handle_user_message_creates_goal_when_waiting(tmp_path) -> None:
    app = InputApp()
    app._session_state.goal_mode = True
    app._goal_store = GoalStore(tmp_path)

    asyncio.run(input_handlers.handle_user_message(app, "Ship the MVP"))

    assert app._session_state.goal.objective == "Ship the MVP"
    assert app._session_state.goal_mode is True
    assert [sent[0] for sent in app.sent]
    assert not app.planner_messages


def test_can_bypass_queue_uses_busy_state() -> None:
    app = InputApp()
    app._connecting = True

    assert input_handlers.can_bypass_queue(app, "/version") is True
    assert input_handlers.can_bypass_queue(app, "/clear") is False


def test_handle_chat_input_submitted_exits_for_always_immediate() -> None:
    app = InputApp()

    asyncio.run(
        input_handlers.handle_chat_input_submitted(app, event("/quit", "command"))
    )

    assert app.exited is True
    assert app._quit_pending is False


def test_handle_chat_input_submitted_warns_during_thread_switch() -> None:
    app = InputApp()
    app._thread_switching = True

    asyncio.run(input_handlers.handle_chat_input_submitted(app, event("hello")))

    assert app.processed == []
    assert app.notifications[-1][1]["severity"] == "warning"


def test_handle_chat_input_submitted_bypasses_busy_queue_for_allowed_command() -> None:
    app = InputApp()
    app._connecting = True

    asyncio.run(
        input_handlers.handle_chat_input_submitted(app, event("/version", "command"))
    )

    assert app.processed == [("/version", "command")]
    assert not app._pending_messages


def test_handle_chat_input_submitted_queues_when_busy() -> None:
    app = InputApp()
    app._agent_running = True

    asyncio.run(
        input_handlers.handle_chat_input_submitted(app, event("wait", "normal"))
    )

    assert app._pending_messages.pop() == QueuedMessage(text="wait", mode="normal")
    assert isinstance(app._queued_widgets.pop(), QueuedUserMessage)
    assert isinstance(app.messages[-1], QueuedUserMessage)


def test_handle_chat_input_submitted_processes_when_idle() -> None:
    app = InputApp()

    asyncio.run(input_handlers.handle_chat_input_submitted(app, event("go", "normal")))

    assert app.processed == [("go", "normal")]
