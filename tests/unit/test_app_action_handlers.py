from __future__ import annotations

import asyncio
import sys
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace

from invincat_cli.app_runtime import action_handlers


class DummyApp:
    def __init__(self) -> None:
        self._shell_running = False
        self._shell_worker = None
        self._pending_approval_widget = None
        self._pending_ask_user_widget = None
        self._agent_running = False
        self._agent_worker = None
        self._quit_pending = False
        self._chat_input = None
        self._pending_messages = []
        self._auto_approve = False
        self._status_bar = None
        self._session_state = None
        self.screen = object()
        self.cancelled: list[object] = []
        self.exited = False
        self.armed: list[str] = []
        self.popped = False

    def _cancel_worker(self, worker: object) -> None:
        self.cancelled.append(worker)

    def _arm_quit_pending(self, source: str) -> None:
        self.armed.append(source)
        self._quit_pending = True

    def _pop_last_queued_message(self) -> None:
        self.popped = True

    def exit(self) -> None:
        self.exited = True


class DummyPrompt:
    def __init__(self) -> None:
        self.rejected = False
        self.cancelled = False
        self.previous = False

    def action_select_reject(self) -> None:
        self.rejected = True

    def action_cancel(self) -> None:
        self.cancelled = True

    def action_previous_question(self) -> None:
        self.previous = True


class DummyStatus:
    def __init__(self) -> None:
        self.enabled: bool | None = None

    def set_auto_approve(self, *, enabled: bool) -> None:
        self.enabled = enabled


def test_quit_or_interrupt_cancels_shell_worker_first() -> None:
    app = DummyApp()
    app._shell_running = True
    app._shell_worker = object()
    app._quit_pending = True

    action_handlers.quit_or_interrupt(app)

    assert app.cancelled == [app._shell_worker]
    assert app._quit_pending is False
    assert app.exited is False


def test_quit_or_interrupt_rejects_pending_approval() -> None:
    app = DummyApp()
    prompt = DummyPrompt()
    app._pending_approval_widget = prompt

    action_handlers.quit_or_interrupt(app)

    assert prompt.rejected is True
    assert app.exited is False


def test_quit_or_interrupt_cancels_pending_question_and_agent() -> None:
    app = DummyApp()
    question = DummyPrompt()
    app._pending_ask_user_widget = question

    action_handlers.quit_or_interrupt(app)

    assert question.cancelled is True

    app = DummyApp()
    app._agent_running = True
    app._agent_worker = object()

    action_handlers.quit_or_interrupt(app)

    assert app.cancelled == [app._agent_worker]


def test_quit_or_interrupt_arms_then_exits_on_second_press() -> None:
    app = DummyApp()

    action_handlers.quit_or_interrupt(app)
    action_handlers.quit_or_interrupt(app)

    assert app.armed == ["Ctrl+C"]
    assert app.exited is True


def test_interrupt_cancels_chat_input_completion_before_work() -> None:
    app = DummyApp()
    chat_input = SimpleNamespace(
        dismissed=False,
        exited=False,
        dismiss_completion=lambda: True,
        exit_mode=lambda: False,
    )
    app._chat_input = chat_input
    app._agent_running = True
    app._agent_worker = object()

    action_handlers.interrupt(app)

    assert app.cancelled == []


def test_interrupt_rejects_pending_prompts_and_cancels_workers() -> None:
    approval_app = DummyApp()
    approval = DummyPrompt()
    approval_app._pending_approval_widget = approval

    action_handlers.interrupt(approval_app)

    assert approval.rejected is True

    question_app = DummyApp()
    question = DummyPrompt()
    question_app._pending_ask_user_widget = question

    action_handlers.interrupt(question_app)

    assert question.cancelled is True

    agent_app = DummyApp()
    agent_app._agent_running = True
    agent_app._agent_worker = object()

    action_handlers.interrupt(agent_app)

    assert agent_app.cancelled == [agent_app._agent_worker]


def test_interrupt_cancels_modal_screen(monkeypatch) -> None:
    class DummyModal:
        def __init__(self) -> None:
            self.cancelled = False

        def action_cancel(self) -> None:
            self.cancelled = True

    fake_thread_selector = ModuleType("invincat_cli.widgets.thread_selector")
    fake_thread_selector.ThreadSelectorScreen = type("ThreadSelectorScreen", (), {})
    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.widgets.thread_selector",
        fake_thread_selector,
    )
    monkeypatch.setattr(action_handlers, "ModalScreen", DummyModal)
    app = DummyApp()
    app.screen = DummyModal()

    action_handlers.interrupt(app)

    assert app.screen.cancelled is True


def test_interrupt_routes_thread_selector_delete_confirmation(monkeypatch) -> None:
    class FakeThreadSelectorScreen:
        is_delete_confirmation_open = True

        def __init__(self) -> None:
            self.cancelled = False

        def action_cancel(self) -> None:
            self.cancelled = True

    fake_thread_selector = ModuleType("invincat_cli.widgets.thread_selector")
    fake_thread_selector.ThreadSelectorScreen = FakeThreadSelectorScreen
    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.widgets.thread_selector",
        fake_thread_selector,
    )
    app = DummyApp()
    app.screen = FakeThreadSelectorScreen()

    action_handlers.interrupt(app)

    assert app.screen.cancelled is True


def test_interrupt_dismisses_modal_without_cancel(monkeypatch) -> None:
    class DummyModal:
        def __init__(self) -> None:
            self.dismissed: object | None = "unset"

        def dismiss(self, value: object) -> None:
            self.dismissed = value

    fake_thread_selector = ModuleType("invincat_cli.widgets.thread_selector")
    fake_thread_selector.ThreadSelectorScreen = type("ThreadSelectorScreen", (), {})
    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.widgets.thread_selector",
        fake_thread_selector,
    )
    monkeypatch.setattr(action_handlers, "ModalScreen", DummyModal)
    app = DummyApp()
    app.screen = DummyModal()

    action_handlers.interrupt(app)

    assert app.screen.dismissed is None


def test_interrupt_exits_chat_input_mode_and_cancels_shell_worker() -> None:
    mode_app = DummyApp()
    mode_app._chat_input = SimpleNamespace(
        dismiss_completion=lambda: False,
        exit_mode=lambda: True,
    )
    mode_app._shell_running = True
    mode_app._shell_worker = object()

    action_handlers.interrupt(mode_app)

    assert mode_app.cancelled == []

    shell_app = DummyApp()
    shell_app._shell_running = True
    shell_app._shell_worker = object()

    action_handlers.interrupt(shell_app)

    assert shell_app.cancelled == [shell_app._shell_worker]


def test_interrupt_pops_pending_message_when_idle() -> None:
    app = DummyApp()
    app._pending_messages = ["queued"]

    action_handlers.interrupt(app)

    assert app.popped is True


def test_toggle_auto_approve_updates_status_and_session_state() -> None:
    app = DummyApp()
    app._status_bar = DummyStatus()
    app._session_state = SimpleNamespace(auto_approve=False)

    action_handlers.toggle_auto_approve(app)

    assert app._auto_approve is True
    assert app._status_bar.enabled is True
    assert app._session_state.auto_approve is True


def test_toggle_auto_approve_routes_pending_ask_user_prompt() -> None:
    app = DummyApp()
    prompt = DummyPrompt()
    app._pending_ask_user_widget = prompt

    action_handlers.toggle_auto_approve(app)

    assert prompt.previous is True
    assert app._auto_approve is False


def test_toggle_auto_approve_routes_thread_selector_and_modal(monkeypatch) -> None:
    class FakeThreadSelectorScreen:
        def __init__(self) -> None:
            self.previous = False

        def action_focus_previous_filter(self) -> None:
            self.previous = True

    fake_thread_selector = ModuleType("invincat_cli.widgets.thread_selector")
    fake_thread_selector.ThreadSelectorScreen = FakeThreadSelectorScreen
    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.widgets.thread_selector",
        fake_thread_selector,
    )
    app = DummyApp()
    app.screen = FakeThreadSelectorScreen()

    action_handlers.toggle_auto_approve(app)

    assert app.screen.previous is True

    class DummyModal:
        pass

    monkeypatch.setattr(action_handlers, "ModalScreen", DummyModal)
    modal_app = DummyApp()
    modal_app.screen = DummyModal()

    action_handlers.toggle_auto_approve(modal_app)

    assert modal_app._auto_approve is False


def test_quit_app_routes_thread_selector_delete_and_confirmation(monkeypatch) -> None:
    class FakeThreadSelectorScreen:
        def __init__(self) -> None:
            self.deleted = False

        def action_delete_thread(self) -> None:
            self.deleted = True

    class FakeDeleteThreadConfirmScreen:
        pass

    fake_thread_selector = ModuleType("invincat_cli.widgets.thread_selector")
    fake_thread_selector.ThreadSelectorScreen = FakeThreadSelectorScreen
    fake_thread_selector.DeleteThreadConfirmScreen = FakeDeleteThreadConfirmScreen
    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.widgets.thread_selector",
        fake_thread_selector,
    )

    selector_app = DummyApp()
    selector_app.screen = FakeThreadSelectorScreen()
    action_handlers.quit_app(selector_app)
    assert selector_app.screen.deleted is True

    confirm_app = DummyApp()
    confirm_app.screen = FakeDeleteThreadConfirmScreen()
    action_handlers.quit_app(confirm_app)
    assert confirm_app.armed == ["Ctrl+D"]

    confirm_app._quit_pending = True
    action_handlers.quit_app(confirm_app)
    assert confirm_app.exited is True

    plain_app = DummyApp()
    action_handlers.quit_app(plain_app)
    assert plain_app.exited is True


def test_toggle_tool_output_prefers_last_skill_body() -> None:
    class Skill:
        def __init__(self, body: str) -> None:
            self._stripped_body = body
            self.toggled = False

        def toggle_body(self) -> None:
            self.toggled = True

    first = Skill("one")
    last = Skill("two")
    app = SimpleNamespace(query=lambda _selector: [first, last])

    action_handlers.toggle_tool_output(app)

    assert first.toggled is False
    assert last.toggled is True


def test_toggle_tool_output_falls_back_to_tool_output() -> None:
    class Tool:
        def __init__(self, has_output: bool) -> None:
            self.has_output = has_output
            self.toggled = False

        def toggle_output(self) -> None:
            self.toggled = True

    tool = Tool(has_output=True)

    def query(selector: type) -> list[object]:
        if selector.__name__ == "SkillMessage":
            return []
        return [Tool(has_output=False), tool]

    action_handlers.toggle_tool_output(SimpleNamespace(query=query))

    assert tool.toggled is True


def test_open_editor_updates_prompt_text(monkeypatch) -> None:
    text_area = SimpleNamespace(
        text="before",
        cursor=None,
        move_cursor=lambda value: setattr(text_area, "cursor", value),
    )
    chat_input = SimpleNamespace(
        _text_area=text_area,
        focused=False,
        focus_input=lambda: setattr(chat_input, "focused", True),
    )
    app = SimpleNamespace(_chat_input=chat_input, suspend=lambda: nullcontext())

    monkeypatch.setattr("invincat_cli.io.editor.open_in_editor", lambda _text: "a\nb")

    asyncio.run(action_handlers.open_editor(app))

    assert text_area.text == "a\nb"
    assert text_area.cursor == (1, 1)
    assert chat_input.focused is True


def test_open_editor_notifies_and_refocuses_on_failure(monkeypatch) -> None:
    notifications: list[tuple[str, dict[str, object]]] = []
    text_area = SimpleNamespace(text="before")
    chat_input = SimpleNamespace(
        _text_area=text_area,
        focused=False,
        focus_input=lambda: setattr(chat_input, "focused", True),
    )
    app = SimpleNamespace(
        _chat_input=chat_input,
        suspend=lambda: nullcontext(),
        notify=lambda message, **kwargs: notifications.append((message, kwargs)),
    )

    def fail(_text: str) -> None:
        raise RuntimeError("editor failed")

    monkeypatch.setattr("invincat_cli.io.editor.open_in_editor", fail)

    asyncio.run(action_handlers.open_editor(app))

    assert text_area.text == "before"
    assert chat_input.focused is True
    assert notifications[0][1]["severity"] == "error"


def test_open_editor_returns_without_chat_input_or_text_area() -> None:
    asyncio.run(action_handlers.open_editor(SimpleNamespace(_chat_input=None)))

    chat_input = SimpleNamespace(_text_area=None)
    asyncio.run(action_handlers.open_editor(SimpleNamespace(_chat_input=chat_input)))
