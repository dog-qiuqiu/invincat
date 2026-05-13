"""App-bound shell command handlers."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess  # noqa: S404
import sys
from contextlib import suppress
from typing import Any

from textual.app import ScreenStackError
from textual.containers import VerticalScroll
from textual.css.query import NoMatches

from invincat_cli.app_runtime.shell import (
    format_shell_output,
    is_interactive_command,
    shell_termination_strategy,
    should_start_new_shell_session,
)
from invincat_cli.i18n import t
from invincat_cli.widgets.messages import (
    AppMessage,
    AssistantMessage,
    ErrorMessage,
    UserMessage,
)

logger = logging.getLogger(__name__)


async def handle_shell_command(app: Any, command: str) -> None:  # noqa: ANN401
    """Handle a shell command and spawn its worker."""
    await app._mount_message(UserMessage(f"!{command}"))
    app._shell_running = True

    if app._chat_input:
        app._chat_input.set_cursor_active(active=False)

    if is_interactive_command(command):
        app._shell_worker = app.run_worker(
            app._run_interactive_shell_task(command),
            exclusive=False,
        )
    else:
        app._shell_worker = app.run_worker(
            app._run_shell_task(command),
            exclusive=False,
        )


async def run_interactive_shell_task(app: Any, command: str) -> None:  # noqa: ANN401
    """Run an interactive shell command using Textual suspend."""
    try:
        with app.suspend():
            result = subprocess.run(  # noqa: S603
                command,
                shell=True,
                cwd=app._cwd,
                check=False,
            )

        if result.returncode != 0:
            await app._mount_message(
                ErrorMessage(t("shell.exit_code").format(code=result.returncode))
            )
        else:
            await app._mount_message(AppMessage(t("shell.command_completed")))

        with suppress(NoMatches, ScreenStackError):
            app.query_one("#chat", VerticalScroll).anchor()

    except FileNotFoundError:
        await app._mount_message(
            ErrorMessage(t("shell.command_not_found").format(command=command))
        )
    except OSError as exc:
        logger.exception("Failed to execute interactive shell command: %s", command)
        await app._mount_message(
            ErrorMessage(t("shell.command_failed").format(error=str(exc)))
        )
    finally:
        await app._cleanup_shell_task()


async def run_shell_task(app: Any, command: str) -> None:  # noqa: ANN401
    """Run a shell command in a background worker."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=app._cwd,
            start_new_session=should_start_new_shell_session(sys.platform),
        )
        app._shell_process = proc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=60
            )
        except TimeoutError:
            await app._kill_shell_process()
            await app._mount_message(
                ErrorMessage(t("shell.command_timeout").format(seconds=60))
            )
            return
        except asyncio.CancelledError:
            await app._kill_shell_process()
            raise

        output = format_shell_output(stdout_bytes, stderr_bytes)

        if output:
            msg = AssistantMessage(f"```\n{output}\n```")
            await app._mount_message(msg)
            await msg.write_initial_content()
        else:
            await app._mount_message(AppMessage(t("shell.command_completed_no_output")))

        if proc.returncode and proc.returncode != 0:
            await app._mount_message(
                ErrorMessage(t("shell.exit_code").format(code=proc.returncode))
            )

        with suppress(NoMatches, ScreenStackError):
            app.query_one("#chat", VerticalScroll).anchor()

    except OSError as exc:
        logger.exception("Failed to execute shell command: %s", command)
        err_msg = t("shell.command_failed").format(error=str(exc))
        await app._mount_message(ErrorMessage(err_msg))
    finally:
        await app._cleanup_shell_task()


async def cleanup_shell_task(app: Any) -> None:  # noqa: ANN401
    """Clean up after shell command task completes or is cancelled."""
    was_interrupted = app._shell_process is not None and (
        app._shell_worker is not None and app._shell_worker.is_cancelled
    )
    app._shell_process = None
    app._shell_running = False
    app._shell_worker = None
    if was_interrupted:
        await app._mount_message(AppMessage(t("shell.command_interrupted")))
    if app._chat_input:
        app._chat_input.set_cursor_active(active=True)
    try:
        await app._maybe_drain_deferred()
    except Exception:
        logger.exception("Failed to drain deferred actions during shell cleanup")
        with suppress(Exception):
            await app._mount_message(
                ErrorMessage(
                    "A deferred action failed after task completion. "
                    "You may need to retry the operation."
                )
            )
    await app._process_next_from_queue()


async def kill_shell_process(app: Any) -> None:  # noqa: ANN401
    """Terminate the running shell command process."""
    proc = app._shell_process
    if proc is None or proc.returncode is not None:
        return

    try:
        strategy = shell_termination_strategy(sys.platform)
        if strategy == "process_group":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        return
    except OSError:
        logger.warning(
            "Failed to terminate shell process (pid=%s)", proc.pid, exc_info=True
        )
        return

    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except TimeoutError:
        logger.warning(
            "Shell process (pid=%s) did not exit after SIGTERM; sending SIGKILL",
            proc.pid,
        )
        with suppress(ProcessLookupError, OSError):
            if strategy == "process_group":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        with suppress(ProcessLookupError, OSError):
            await proc.wait()
    except (ProcessLookupError, OSError):
        pass
