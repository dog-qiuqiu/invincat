from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from invincat_cli.app_runtime import approval_handlers
from invincat_cli.widgets.messages import AppMessage


class FakeContainer:
    pass


class FakeChat:
    def __init__(self) -> None:
        self.anchored = False

    def anchor(self) -> None:
        self.anchored = True


class FakeChatInput:
    def __init__(self) -> None:
        self.focused = 0

    def focus_input(self) -> None:
        self.focused += 1


class FakeStatusBar:
    def __init__(self) -> None:
        self.auto_approve_states: list[bool] = []

    def set_auto_approve(self, *, enabled: bool) -> None:
        self.auto_approve_states.append(enabled)


class FakeWidget:
    def __init__(self, *, widget_id: str = "widget", attached: bool = True) -> None:
        self.id = widget_id
        self.is_attached = attached
        self.removed = 0
        self.scrolled = 0
        self.focused = 0
        self.active_focused = 0
        self.cancelled = 0

    async def remove(self) -> None:
        self.removed += 1
        self.is_attached = False

    def scroll_visible(self) -> None:
        self.scrolled += 1

    def focus(self) -> None:
        self.focused += 1

    def focus_active(self) -> None:
        self.active_focused += 1

    def action_cancel(self) -> None:
        self.cancelled += 1


class FakeApprovalMenu(FakeWidget):
    def __init__(
        self,
        action_requests: object,
        assistant_id: str | None,
        *,
        allow_auto_approve: bool,
        id: str,
    ) -> None:
        super().__init__(widget_id=id)
        self.action_requests = action_requests
        self.assistant_id = assistant_id
        self.allow_auto_approve = allow_auto_approve
        self.future: asyncio.Future | None = None

    def set_future(self, future: asyncio.Future) -> None:
        self.future = future


class FakeAskUserMenu(FakeWidget):
    def __init__(self, questions: object, *, id: str) -> None:
        super().__init__(widget_id=id)
        self.questions = questions
        self.future: asyncio.Future | None = None

    def set_future(self, future: asyncio.Future) -> None:
        self.future = future


class ApprovalApp:
    def __init__(self) -> None:
        self._session_state = SimpleNamespace(plan_mode=False, auto_approve=False)
        self._active_turn_is_planner = False
        self._cwd = "/repo"
        self._assistant_id = "assistant-1"
        self._pending_approval_widget: object | None = None
        self._approval_placeholder: object | None = None
        self._pending_ask_user_widget: object | None = None
        self._chat_input: FakeChatInput | None = FakeChatInput()
        self._status_bar: FakeStatusBar | None = FakeStatusBar()
        self._auto_approve = False
        self._planner_prompted_todos_fingerprint: str | None = None
        self.messages_container = FakeContainer()
        self.chat = FakeChat()
        self.mounted: list[object] = []
        self.messages: list[object] = []
        self.after_refresh: list[object] = []
        self.workers: list[object] = []
        self.plan_guard_rejections: list[list[str]] = []
        self.auto_approval_commands: list[list[str]] = []
        self.waited_for_approval = 0
        self.waited_for_ask_user = 0
        self.typing = False
        self.start_workers = True
        self.mount_approval_calls: list[object] = []
        self.mount_ask_user_calls: list[object] = []
        self.removed_ask_widgets: list[tuple[object, str]] = []
        self.approval_requests: list[tuple[object, str | None, bool, bool]] = []

    def query_one(self, selector: str, *_args: object) -> object:
        if selector == "#messages":
            return self.messages_container
        if selector == "#chat":
            return self.chat
        raise LookupError(selector)

    async def _mount_before_queued(self, _container: object, widget: object) -> None:
        self.mounted.append(widget)

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    def call_after_refresh(self, callback: object) -> None:
        self.after_refresh.append(callback)

    def run_worker(self, coroutine: object, *, exclusive: bool) -> object:
        assert exclusive is False
        if not self.start_workers:
            close = getattr(coroutine, "close", None)
            if close is not None:
                close()
            self.workers.append(coroutine)
            return coroutine
        task = asyncio.create_task(coroutine)  # type: ignore[arg-type]
        self.workers.append(task)
        return task

    def _is_user_typing(self) -> bool:
        return self.typing

    async def _handle_plan_guard_auto_reject(
        self,
        disallowed_tool_names: list[str],
    ) -> None:
        self.plan_guard_rejections.append(disallowed_tool_names)

    async def _mount_auto_approval_messages(self, commands: list[str]) -> None:
        self.auto_approval_commands.append(commands)

    async def _wait_for_pending_approval_widget(self) -> None:
        self.waited_for_approval += 1

    async def _mount_approval_widget(
        self,
        menu: object,
        _result_future: asyncio.Future,
    ) -> None:
        self.mount_approval_calls.append(menu)

    async def _deferred_show_approval(
        self,
        placeholder: object,
        menu: object,
        result_future: asyncio.Future,
    ) -> None:
        await approval_handlers.deferred_show_approval(
            self,
            placeholder,  # type: ignore[arg-type]
            menu,
            result_future,
        )

    async def _maybe_approve_current_planner_todos(self) -> None:
        self.messages.append("approve-current-todos")

    async def _request_approval(
        self,
        action_requests: object,
        assistant_id: str | None,
        *,
        bypass_plan_guard: bool,
        allow_auto_approve: bool,
    ) -> asyncio.Future:
        self.approval_requests.append(
            (
                action_requests,
                assistant_id,
                bypass_plan_guard,
                allow_auto_approve,
            )
        )
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        future.set_result({"type": "approve"})
        return future

    async def _wait_for_pending_ask_user_widget(self) -> None:
        self.waited_for_ask_user += 1

    async def _mount_ask_user_widget(
        self,
        menu: object,
        _result_future: asyncio.Future,
    ) -> None:
        self.mount_ask_user_calls.append(menu)

    async def _remove_ask_user_widget(self, widget: object, *, context: str) -> None:
        self.removed_ask_widgets.append((widget, context))
        await approval_handlers.remove_ask_user_widget(widget, context=context)


def install_fake_widget_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.widgets.approval",
        SimpleNamespace(ApprovalMenu=FakeApprovalMenu),
    )
    monkeypatch.setitem(
        sys.modules,
        "invincat_cli.widgets.ask_user",
        SimpleNamespace(AskUserMenu=FakeAskUserMenu),
    )


def message_contents(app: ApprovalApp) -> list[str]:
    return [str(getattr(message, "_content", message)) for message in app.messages]


def test_request_approval_auto_rejects_plan_guard_tools() -> None:
    app = ApprovalApp()
    app._session_state.plan_mode = True
    app._active_turn_is_planner = True

    future = asyncio.run(
        approval_handlers.request_approval(
            app,
            [{"name": "write_file", "args": {"file_path": "a.txt"}}],
            "assistant-1",
        )
    )

    assert future.done()
    assert future.result() == {"type": "reject"}
    assert app.plan_guard_rejections == [["write_file"]]
    assert app.mount_approval_calls == []


def test_request_approval_mounts_menu_when_user_is_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_widget_modules(monkeypatch)
    app = ApprovalApp()

    future = asyncio.run(
        approval_handlers.request_approval(
            app,
            [{"name": "read_file", "args": {"file_path": "a.txt"}}],
            "assistant-1",
            allow_auto_approve=False,
        )
    )

    assert future.done() is False
    assert app.waited_for_approval == 1
    menu = app.mount_approval_calls[0]
    assert isinstance(menu, FakeApprovalMenu)
    assert menu.action_requests == [
        {"name": "read_file", "args": {"file_path": "a.txt"}}
    ]
    assert menu.assistant_id == "assistant-1"
    assert menu.allow_auto_approve is False
    assert menu.future is future
    assert app._pending_approval_widget is menu


def test_request_approval_auto_approves_allowed_shell(monkeypatch) -> None:
    app = ApprovalApp()
    monkeypatch.setattr(
        "invincat_cli.config.settings",
        SimpleNamespace(shell_allow_list=["pytest"]),
    )

    future = asyncio.run(
        approval_handlers.request_approval(
            app,
            [{"name": "shell", "args": {"command": "pytest -q"}}],
            "assistant-1",
        )
    )

    assert future.done()
    assert future.result() == {"type": "approve"}
    assert app.auto_approval_commands == [["pytest -q"]]


def test_request_approval_defers_menu_when_user_is_typing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_widget_modules(monkeypatch)
    app = ApprovalApp()
    app.typing = True
    app.start_workers = False

    future = asyncio.run(
        approval_handlers.request_approval(
            app,
            [{"name": "read_file", "args": {}}],
            "assistant-1",
        )
    )

    assert future.done() is False
    assert app.mounted
    assert app._approval_placeholder is app.mounted[0]
    assert len(app.workers) == 1
    assert isinstance(app._pending_approval_widget, FakeApprovalMenu)


def test_request_approval_falls_back_when_placeholder_mount_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_widget_modules(monkeypatch)
    app = ApprovalApp()
    app.typing = True

    async def fail_mount(_container: object, _widget: object) -> None:
        raise RuntimeError("placeholder failed")

    app._mount_before_queued = fail_mount  # type: ignore[method-assign]

    future = asyncio.run(
        approval_handlers.request_approval(
            app,
            [{"name": "read_file", "args": {}}],
            "assistant-1",
        )
    )

    assert future.done() is False
    assert app._approval_placeholder is None
    assert isinstance(app.mount_approval_calls[-1], FakeApprovalMenu)


def test_deferred_show_approval_cancels_when_placeholder_detached() -> None:
    app = ApprovalApp()
    placeholder = FakeWidget(attached=False)
    menu = FakeWidget(widget_id="approval-menu")
    app._approval_placeholder = placeholder
    app._pending_approval_widget = menu

    async def run() -> asyncio.Future:
        future: asyncio.Future = asyncio.Future()
        await approval_handlers.deferred_show_approval(app, placeholder, menu, future)
        return future

    future = asyncio.run(run())

    assert future.cancelled()
    assert app._approval_placeholder is None
    assert app._pending_approval_widget is None
    assert app.mount_approval_calls == []


def test_deferred_show_approval_times_out_and_ignores_placeholder_remove_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingRemoveWidget(FakeWidget):
        async def remove(self) -> None:
            self.removed += 1
            raise RuntimeError("remove failed")

    app = ApprovalApp()
    app.typing = True
    placeholder = FailingRemoveWidget(attached=True)
    menu = FakeWidget(widget_id="approval-menu")
    times = iter([0.0, 1.0, 31.0])

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(approval_handlers, "_monotonic", lambda: next(times))
    monkeypatch.setattr(approval_handlers.asyncio, "sleep", no_sleep)

    async def run() -> asyncio.Future:
        future: asyncio.Future = asyncio.Future()
        await approval_handlers.deferred_show_approval(app, placeholder, menu, future)
        return future

    future = asyncio.run(run())

    assert placeholder.removed == 1
    assert app.mount_approval_calls == [menu]
    assert future.done() is False


def test_deferred_show_approval_cancels_future_on_base_exception() -> None:
    app = ApprovalApp()
    placeholder = FakeWidget(attached=True)
    menu = FakeWidget(widget_id="approval-menu")

    async def cancel_mount(_menu: object, _future: asyncio.Future) -> None:
        raise asyncio.CancelledError

    app._mount_approval_widget = cancel_mount  # type: ignore[method-assign]

    async def run() -> asyncio.Future:
        future: asyncio.Future = asyncio.Future()
        try:
            await approval_handlers.deferred_show_approval(
                app, placeholder, menu, future
            )
        except asyncio.CancelledError:
            pass
        return future

    future = asyncio.run(run())

    assert future.cancelled()
    assert app._pending_approval_widget is None
    assert app._approval_placeholder is None


def test_deferred_show_approval_swaps_placeholder_for_menu() -> None:
    app = ApprovalApp()
    placeholder = FakeWidget(attached=True)
    menu = FakeWidget(widget_id="approval-menu")
    app._approval_placeholder = placeholder

    async def run() -> asyncio.Future:
        future: asyncio.Future = asyncio.Future()
        await approval_handlers.deferred_show_approval(app, placeholder, menu, future)
        return future

    future = asyncio.run(run())

    assert placeholder.removed == 1
    assert app._approval_placeholder is None
    assert app.mount_approval_calls == [menu]
    assert future.done() is False


def test_handle_plan_guard_auto_reject_mounts_notice() -> None:
    app = ApprovalApp()

    asyncio.run(
        approval_handlers.handle_plan_guard_auto_reject(app, ["write_file", "shell"])
    )

    assert message_contents(app)[0] == "approve-current-todos"
    assert isinstance(app.mounted[-1], AppMessage)
    assert "write_file" in str(getattr(app.mounted[-1], "_content", ""))


def test_handle_plan_guard_auto_reject_tolerates_helper_and_mount_failures() -> None:
    app = ApprovalApp()

    async def fail_approve_current() -> None:
        raise RuntimeError("approval failed")

    def missing_messages(_selector: str, *_args: object) -> object:
        raise approval_handlers.NoMatches("missing")

    app._maybe_approve_current_planner_todos = fail_approve_current  # type: ignore[method-assign]
    app.query_one = missing_messages  # type: ignore[method-assign]

    asyncio.run(approval_handlers.handle_plan_guard_auto_reject(app, ["shell"]))

    assert app.mounted == []


def test_mount_auto_approval_messages_anchors_chat() -> None:
    app = ApprovalApp()

    asyncio.run(approval_handlers.mount_auto_approval_messages(app, ["pytest -q"]))

    assert isinstance(app.mounted[-1], AppMessage)
    assert "pytest -q" in str(getattr(app.mounted[-1], "_content", ""))
    assert app.chat.anchored is True


def test_mount_auto_approval_messages_tolerates_display_failure() -> None:
    app = ApprovalApp()

    def missing_messages(_selector: str, *_args: object) -> object:
        raise RuntimeError("missing")

    app.query_one = missing_messages  # type: ignore[method-assign]

    asyncio.run(approval_handlers.mount_auto_approval_messages(app, ["pytest -q"]))

    assert app.mounted == []


def test_wait_for_pending_approval_widget_returns_and_waits_until_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ApprovalApp()

    asyncio.run(approval_handlers.wait_for_pending_approval_widget(app))

    assert app._pending_approval_widget is None

    app._pending_approval_widget = FakeWidget()
    times = iter([0.0, 0.1])

    async def clear_after_sleep(_seconds: float) -> None:
        app._pending_approval_widget = None

    monkeypatch.setattr(approval_handlers, "_monotonic", lambda: next(times))
    monkeypatch.setattr(approval_handlers.asyncio, "sleep", clear_after_sleep)

    asyncio.run(approval_handlers.wait_for_pending_approval_widget(app))

    assert app._pending_approval_widget is None


def test_wait_for_pending_approval_widget_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ApprovalApp()
    app._pending_approval_widget = FakeWidget()
    times = iter([0.0, 31.0])

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(approval_handlers, "_monotonic", lambda: next(times))
    monkeypatch.setattr(approval_handlers.asyncio, "sleep", no_sleep)

    asyncio.run(approval_handlers.wait_for_pending_approval_widget(app))

    assert app._pending_approval_widget is not None


def test_mount_approval_widget_sets_future_exception_on_failure() -> None:
    app = ApprovalApp()
    menu = FakeWidget(widget_id="approval-menu")

    async def fail_mount(_container: object, _widget: object) -> None:
        raise RuntimeError("mount failed")

    app._mount_before_queued = fail_mount  # type: ignore[method-assign]

    async def run() -> asyncio.Future:
        future: asyncio.Future = asyncio.Future()
        await approval_handlers.mount_approval_widget(app, menu, future)
        return future

    future = asyncio.run(run())

    assert app._pending_approval_widget is None
    assert isinstance(future.exception(), RuntimeError)


def test_mount_approval_widget_success_schedules_visibility_and_focus() -> None:
    app = ApprovalApp()
    menu = FakeWidget(widget_id="approval-menu")

    async def run() -> asyncio.Future:
        future: asyncio.Future = asyncio.Future()
        await approval_handlers.mount_approval_widget(app, menu, future)
        return future

    future = asyncio.run(run())

    assert app.mounted == [menu]
    assert app.after_refresh == [menu.scroll_visible, menu.focus]
    assert future.done() is False


def test_remove_approval_placeholder_removes_attached_widget() -> None:
    app = ApprovalApp()
    placeholder = FakeWidget(attached=True)
    app._approval_placeholder = placeholder

    asyncio.run(
        approval_handlers.remove_approval_placeholder(app, context="test cleanup")
    )

    assert app._approval_placeholder is None
    assert placeholder.removed == 1


def test_remove_approval_placeholder_handles_absent_detached_and_remove_failure() -> (
    None
):
    app = ApprovalApp()

    asyncio.run(approval_handlers.remove_approval_placeholder(app, context="noop"))

    assert app._approval_placeholder is None

    detached = FakeWidget(attached=False)
    app._approval_placeholder = detached

    asyncio.run(approval_handlers.remove_approval_placeholder(app, context="detached"))

    assert detached.removed == 0
    assert app._approval_placeholder is None

    class FailingRemoveWidget(FakeWidget):
        async def remove(self) -> None:
            self.removed += 1
            raise RuntimeError("remove failed")

    failing = FailingRemoveWidget(attached=True)
    app._approval_placeholder = failing

    asyncio.run(approval_handlers.remove_approval_placeholder(app, context="failure"))

    assert failing.removed == 1
    assert app._approval_placeholder is None


def test_enable_auto_approve_updates_app_status_and_session() -> None:
    app = ApprovalApp()

    approval_handlers.enable_auto_approve(app)

    assert app._auto_approve is True
    assert app._status_bar is not None
    assert app._status_bar.auto_approve_states == [True]
    assert app._session_state.auto_approve is True


def test_request_approve_plan_maps_raw_decision() -> None:
    app = ApprovalApp()
    todos = [{"content": "Implement", "status": "pending"}]

    async def run() -> None:
        mapped = await approval_handlers.request_approve_plan(app, todos)
        await app.workers[-1]
        assert mapped.result() == {"type": "approved"}

    asyncio.run(run())

    assert app._planner_prompted_todos_fingerprint == (
        '[{"content": "Implement", "status": "pending"}]'
    )
    action_requests, assistant_id, bypass, allow_auto = app.approval_requests[0]
    assert assistant_id == "assistant-1"
    assert bypass is True
    assert allow_auto is False
    assert action_requests == [
        {
            "name": "approve_plan",
            "description": "Approve or refine this generated plan.",
            "args": {"todos": todos},
        }
    ]


def test_request_approve_plan_propagates_raw_future_failure() -> None:
    app = ApprovalApp()

    async def failed_request(
        _action_requests: object,
        _assistant_id: str | None,
        *,
        bypass_plan_guard: bool,
        allow_auto_approve: bool,
    ) -> asyncio.Future:
        future: asyncio.Future = asyncio.Future()
        future.set_exception(RuntimeError("approval failed"))
        return future

    app._request_approval = failed_request  # type: ignore[method-assign]

    async def run() -> asyncio.Future:
        mapped = await approval_handlers.request_approve_plan(
            app,
            [{"content": "Implement", "status": "pending"}],
        )
        await app.workers[-1]
        return mapped

    mapped = asyncio.run(run())

    assert isinstance(mapped.exception(), RuntimeError)


def test_request_ask_user_creates_menu_and_mounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_widget_modules(monkeypatch)
    app = ApprovalApp()
    questions = [{"question": "Continue?", "type": "text"}]

    future = asyncio.run(approval_handlers.request_ask_user(app, questions))

    assert future.done() is False
    assert app.waited_for_ask_user == 1
    menu = app.mount_ask_user_calls[0]
    assert isinstance(menu, FakeAskUserMenu)
    assert menu.questions == questions
    assert menu.future is future
    assert app._pending_ask_user_widget is menu


def test_wait_for_pending_ask_user_widget_cancels_and_removes_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ApprovalApp()
    widget = FakeWidget(widget_id="ask-user-menu")
    app._pending_ask_user_widget = widget
    times = iter([0.0, 31.0])

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(approval_handlers, "_monotonic", lambda: next(times))
    monkeypatch.setattr(approval_handlers.asyncio, "sleep", no_sleep)

    asyncio.run(approval_handlers.wait_for_pending_ask_user_widget(app))

    assert widget.cancelled == 1
    assert widget.removed == 1
    assert app._pending_ask_user_widget is None
    assert app.removed_ask_widgets == [(widget, "ask-user timeout cleanup")]


def test_remove_ask_user_widget_tolerates_remove_failure() -> None:
    class FailingRemoveWidget(FakeWidget):
        async def remove(self) -> None:
            self.removed += 1
            raise RuntimeError("remove failed")

    widget = FailingRemoveWidget(widget_id="ask-user-menu")

    asyncio.run(
        approval_handlers.remove_ask_user_widget(widget, context="test cleanup")
    )

    assert widget.removed == 1


def test_wait_for_pending_ask_user_widget_returns_and_waits_until_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ApprovalApp()

    asyncio.run(approval_handlers.wait_for_pending_ask_user_widget(app))

    assert app._pending_ask_user_widget is None

    app._pending_ask_user_widget = FakeWidget(widget_id="ask-user-menu")
    times = iter([0.0, 0.1])

    async def clear_after_sleep(_seconds: float) -> None:
        app._pending_ask_user_widget = None

    monkeypatch.setattr(approval_handlers, "_monotonic", lambda: next(times))
    monkeypatch.setattr(approval_handlers.asyncio, "sleep", clear_after_sleep)

    asyncio.run(approval_handlers.wait_for_pending_ask_user_widget(app))

    assert app._pending_ask_user_widget is None


def test_mount_ask_user_widget_focuses_active_field() -> None:
    app = ApprovalApp()
    menu = FakeWidget(widget_id="ask-user-menu")

    async def run() -> asyncio.Future:
        future: asyncio.Future = asyncio.Future()
        await approval_handlers.mount_ask_user_widget(app, menu, future)
        return future

    future = asyncio.run(run())

    assert app.mounted == [menu]
    assert app.after_refresh == [menu.scroll_visible, menu.focus_active]
    assert future.done() is False


def test_mount_ask_user_widget_sets_future_exception_on_failure() -> None:
    app = ApprovalApp()
    menu = FakeWidget(widget_id="ask-user-menu")

    async def fail_mount(_container: object, _widget: object) -> None:
        raise RuntimeError("mount failed")

    app._mount_before_queued = fail_mount  # type: ignore[method-assign]

    async def run() -> asyncio.Future:
        future: asyncio.Future = asyncio.Future()
        await approval_handlers.mount_ask_user_widget(app, menu, future)
        return future

    future = asyncio.run(run())

    assert app._pending_ask_user_widget is None
    assert isinstance(future.exception(), RuntimeError)


def test_ask_user_answered_and_cancelled_remove_widget_and_refocus() -> None:
    app = ApprovalApp()
    answered_widget = FakeWidget(widget_id="ask-user-menu")
    app._pending_ask_user_widget = answered_widget

    asyncio.run(approval_handlers.handle_ask_user_menu_answered(app))

    assert app._pending_ask_user_widget is None
    assert answered_widget.removed == 1
    assert app.after_refresh[-1] == app._chat_input.focus_input

    cancelled_widget = FakeWidget(widget_id="ask-user-menu")
    app._pending_ask_user_widget = cancelled_widget

    asyncio.run(approval_handlers.handle_ask_user_menu_cancelled(app))

    assert app._pending_ask_user_widget is None
    assert cancelled_widget.removed == 1
    assert app.after_refresh[-1] == app._chat_input.focus_input


def test_approve_widget_decisions_mount_messages_and_refocus() -> None:
    app = ApprovalApp()

    asyncio.run(approval_handlers.handle_approve_widget_approved(app))
    asyncio.run(approval_handlers.handle_approve_widget_rejected(app))

    contents = message_contents(app)
    assert any(
        "approved" in content.lower() or "确认" in content for content in contents
    )
    assert any(
        "rejected" in content.lower() or "拒绝" in content for content in contents
    )
    assert app.after_refresh[-2:] == [
        app._chat_input.focus_input,
        app._chat_input.focus_input,
    ]
