from __future__ import annotations

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

from invincat_cli.app_runtime import shell_handlers
from invincat_cli.widgets.messages import AppMessage, ErrorMessage, UserMessage


class ShellApp:
    def __init__(self) -> None:
        self._cwd = "/repo"
        self._shell_running = False
        self._shell_worker = None
        self._shell_process = None
        self._chat_input = SimpleNamespace(
            active=[],
            set_cursor_active=lambda *, active: self._chat_input.active.append(active),
        )
        self.messages: list[object] = []
        self.workers: list[tuple[object, dict[str, object]]] = []
        self.cleaned = False
        self.killed = False
        self.drain_error: Exception | None = None
        self.drained = False
        self.processed_next = False

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    def run_worker(self, worker: object, **kwargs: object) -> object:
        self.workers.append((worker, kwargs))
        close = getattr(worker, "close", None)
        if callable(close):
            close()
        self._shell_worker = SimpleNamespace(is_cancelled=False)
        return self._shell_worker

    def suspend(self):
        return nullcontext()

    def query_one(self, *_args: object) -> object:
        raise shell_handlers.NoMatches("missing")

    async def _run_interactive_shell_task(self, command: str) -> None:
        self.messages.append(("interactive", command))

    async def _run_shell_task(self, command: str) -> None:
        self.messages.append(("shell", command))

    async def _cleanup_shell_task(self) -> None:
        self.cleaned = True

    async def _kill_shell_process(self) -> None:
        self.killed = True

    async def _maybe_drain_deferred(self) -> None:
        self.drained = True
        if self.drain_error is not None:
            raise self.drain_error

    async def _process_next_from_queue(self) -> None:
        self.processed_next = True


class FakeAssistantMessage:
    def __init__(self, content: str) -> None:
        self._content = content
        self.written = False

    async def write_initial_content(self) -> None:
        self.written = True


def test_handle_shell_command_selects_interactive_or_background(monkeypatch) -> None:
    app = ShellApp()

    asyncio.run(shell_handlers.handle_shell_command(app, "vim file.txt"))

    assert isinstance(app.messages[0], UserMessage)
    assert app._shell_running is True
    assert app._chat_input.active == [False]
    assert app.workers[0][1]["exclusive"] is False

    app = ShellApp()
    asyncio.run(shell_handlers.handle_shell_command(app, "echo hi"))

    assert app.workers[0][1]["exclusive"] is False


def test_run_interactive_shell_task_mounts_success_and_error(monkeypatch) -> None:
    app = ShellApp()
    monkeypatch.setattr(
        shell_handlers.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    asyncio.run(shell_handlers.run_interactive_shell_task(app, "echo hi"))

    assert isinstance(app.messages[-1], AppMessage)
    assert app.cleaned is True

    app = ShellApp()
    monkeypatch.setattr(
        shell_handlers.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=2),
    )

    asyncio.run(shell_handlers.run_interactive_shell_task(app, "false"))

    assert isinstance(app.messages[-1], ErrorMessage)
    assert app.cleaned is True


def test_run_interactive_shell_task_reports_os_error(monkeypatch) -> None:
    app = ShellApp()

    def fail(*_args, **_kwargs):
        raise OSError("no shell")

    monkeypatch.setattr(shell_handlers.subprocess, "run", fail)

    asyncio.run(shell_handlers.run_interactive_shell_task(app, "bad"))

    assert isinstance(app.messages[-1], ErrorMessage)
    assert app.cleaned is True


def test_run_interactive_shell_task_reports_missing_command(monkeypatch) -> None:
    app = ShellApp()

    def fail(*_args, **_kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(shell_handlers.subprocess, "run", fail)

    asyncio.run(shell_handlers.run_interactive_shell_task(app, "missing"))

    assert isinstance(app.messages[-1], ErrorMessage)
    assert app.cleaned is True


class FakeProc:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.pid = 123
        self.terminated = False
        self.killed = False
        self.waited = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout, self.stderr

    async def wait(self) -> None:
        self.waited = True

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def test_run_shell_task_mounts_output_and_exit_code(monkeypatch) -> None:
    app = ShellApp()
    proc = FakeProc(stdout=b"out", stderr=b"err", returncode=3)

    async def create_proc(*_args, **_kwargs) -> FakeProc:
        return proc

    monkeypatch.setattr(
        shell_handlers.asyncio,
        "create_subprocess_shell",
        create_proc,
    )
    monkeypatch.setattr(shell_handlers, "AssistantMessage", FakeAssistantMessage)

    asyncio.run(shell_handlers.run_shell_task(app, "echo hi"))

    assert isinstance(app.messages[0], FakeAssistantMessage)
    assert "out" in app.messages[0]._content
    assert "err" in app.messages[0]._content
    assert app.messages[0].written is True
    assert isinstance(app.messages[-1], ErrorMessage)
    assert app.cleaned is True


def test_run_shell_task_handles_no_output_timeout_and_os_error(monkeypatch) -> None:
    no_output_app = ShellApp()

    async def create_no_output_proc(*_args, **_kwargs) -> FakeProc:
        return FakeProc(returncode=0)

    monkeypatch.setattr(
        shell_handlers.asyncio,
        "create_subprocess_shell",
        create_no_output_proc,
    )

    asyncio.run(shell_handlers.run_shell_task(no_output_app, "true"))

    assert isinstance(no_output_app.messages[-1], AppMessage)

    timeout_app = ShellApp()
    proc = FakeProc()

    async def create_timeout_proc(*_args, **_kwargs) -> FakeProc:
        return proc

    monkeypatch.setattr(
        shell_handlers.asyncio,
        "create_subprocess_shell",
        create_timeout_proc,
    )

    async def timeout(awaitable, *, timeout: int):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise TimeoutError

    monkeypatch.setattr(shell_handlers.asyncio, "wait_for", timeout)

    asyncio.run(shell_handlers.run_shell_task(timeout_app, "sleep"))

    assert timeout_app.killed is True
    assert isinstance(timeout_app.messages[-1], ErrorMessage)

    os_error_app = ShellApp()

    async def raise_os_error(*_args, **_kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(
        shell_handlers.asyncio,
        "create_subprocess_shell",
        raise_os_error,
    )

    asyncio.run(shell_handlers.run_shell_task(os_error_app, "bad"))

    assert isinstance(os_error_app.messages[-1], ErrorMessage)


def test_run_shell_task_cancelled_kills_process_and_propagates(monkeypatch) -> None:
    app = ShellApp()
    proc = FakeProc()

    async def create_proc(*_args, **_kwargs) -> FakeProc:
        return proc

    async def cancelled(awaitable, *, timeout: int):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.CancelledError

    monkeypatch.setattr(
        shell_handlers.asyncio,
        "create_subprocess_shell",
        create_proc,
    )
    monkeypatch.setattr(shell_handlers.asyncio, "wait_for", cancelled)

    try:
        asyncio.run(shell_handlers.run_shell_task(app, "sleep"))
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("expected cancellation")

    assert app.killed is True
    assert app.cleaned is True


def test_cleanup_shell_task_resets_state_and_reports_interrupt() -> None:
    app = ShellApp()
    app._shell_process = object()
    app._shell_worker = SimpleNamespace(is_cancelled=True)
    app._shell_running = True

    asyncio.run(shell_handlers.cleanup_shell_task(app))

    assert app._shell_process is None
    assert app._shell_running is False
    assert app._shell_worker is None
    assert isinstance(app.messages[-1], AppMessage)
    assert app._chat_input.active == [True]
    assert app.drained is True
    assert app.processed_next is True


def test_cleanup_shell_task_reports_deferred_drain_failure() -> None:
    app = ShellApp()
    app.drain_error = RuntimeError("deferred failed")

    asyncio.run(shell_handlers.cleanup_shell_task(app))

    assert app.drained is True
    assert isinstance(app.messages[-1], ErrorMessage)
    assert app.processed_next is True


def test_kill_shell_process_ignores_absent_or_finished_process() -> None:
    app = ShellApp()

    asyncio.run(shell_handlers.kill_shell_process(app))

    proc = FakeProc()
    proc.returncode = 0
    app._shell_process = proc

    asyncio.run(shell_handlers.kill_shell_process(app))

    assert proc.terminated is False


def test_kill_shell_process_terminates_live_process(monkeypatch) -> None:
    app = ShellApp()
    proc = FakeProc()
    proc.returncode = None
    app._shell_process = proc
    monkeypatch.setattr(shell_handlers.sys, "platform", "win32")

    asyncio.run(shell_handlers.kill_shell_process(app))

    assert proc.terminated is True
    assert proc.waited is True


def test_kill_shell_process_handles_process_lookup_and_os_error(monkeypatch) -> None:
    app = ShellApp()
    proc = FakeProc()
    proc.returncode = None
    app._shell_process = proc
    monkeypatch.setattr(shell_handlers.sys, "platform", "win32")

    def missing_process() -> None:
        raise ProcessLookupError

    proc.terminate = missing_process  # type: ignore[method-assign]

    asyncio.run(shell_handlers.kill_shell_process(app))

    proc = FakeProc()
    proc.returncode = None
    app._shell_process = proc

    def os_error() -> None:
        raise OSError("cannot terminate")

    proc.terminate = os_error  # type: ignore[method-assign]

    asyncio.run(shell_handlers.kill_shell_process(app))

    assert proc.waited is False


def test_kill_shell_process_sends_kill_after_timeout(monkeypatch) -> None:
    app = ShellApp()
    proc = FakeProc()
    proc.returncode = None
    app._shell_process = proc
    monkeypatch.setattr(shell_handlers.sys, "platform", "win32")

    async def timeout_wait(awaitable, *, timeout: int):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise TimeoutError

    monkeypatch.setattr(shell_handlers.asyncio, "wait_for", timeout_wait)

    asyncio.run(shell_handlers.kill_shell_process(app))

    assert proc.terminated is True
    assert proc.killed is True
    assert proc.waited is True


def test_kill_shell_process_terminates_process_group_after_timeout(
    monkeypatch,
) -> None:
    app = ShellApp()
    proc = FakeProc()
    proc.returncode = None
    app._shell_process = proc
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(shell_handlers.sys, "platform", "linux")
    monkeypatch.setattr(shell_handlers.os, "getpgid", lambda pid: pid + 10)
    monkeypatch.setattr(
        shell_handlers.os,
        "killpg",
        lambda pgid, sig: sent.append((pgid, sig)),
    )

    async def timeout_wait(awaitable, *, timeout: int):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise TimeoutError

    monkeypatch.setattr(shell_handlers.asyncio, "wait_for", timeout_wait)

    asyncio.run(shell_handlers.kill_shell_process(app))

    assert sent == [
        (133, shell_handlers.signal.SIGTERM),
        (133, shell_handlers.signal.SIGKILL),
    ]
    assert proc.waited is True


def test_kill_shell_process_ignores_wait_os_error(monkeypatch) -> None:
    app = ShellApp()
    proc = FakeProc()
    proc.returncode = None
    app._shell_process = proc
    monkeypatch.setattr(shell_handlers.sys, "platform", "win32")

    async def fail_wait(awaitable, *, timeout: int):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise OSError("already gone")

    monkeypatch.setattr(shell_handlers.asyncio, "wait_for", fail_wait)

    asyncio.run(shell_handlers.kill_shell_process(app))

    assert proc.terminated is True
