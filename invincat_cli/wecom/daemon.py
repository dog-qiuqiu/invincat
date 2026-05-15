"""WeCom background daemon lifecycle management.

Provides start/stop/status for a per-project daemon process that keeps the
WeCom bridge alive independently of the Textual UI.

State file:  {cwd}/.invincat/wecom_daemon.json
Log file:    {cwd}/.invincat/wecom_daemon.log
Lock file:   {cwd}/.invincat/wecom_daemon.lock  (authoritative liveness via flock)
Unix socket: {cwd}/.invincat/wecom_daemon.sock
"""

from __future__ import annotations

import asyncio as asyncio
import fcntl as fcntl
import json as json
import logging as logging
import os as os
import resource as resource
import select as select
import signal as signal
import sys as sys
from pathlib import Path
from typing import Any

from invincat_cli.wecom.daemon_config import WeComDaemonConfig
from invincat_cli.wecom.daemon_constants import (
    _BRIDGE_STARTUP_READY_TIMEOUT as _BRIDGE_STARTUP_READY_TIMEOUT,
)
from invincat_cli.wecom.daemon_constants import (
    _DELIVERY_READY_TIMEOUT as _DELIVERY_READY_TIMEOUT,
)
from invincat_cli.wecom.daemon_constants import (
    _DELIVERY_REQUEST_TIMEOUT as _DELIVERY_REQUEST_TIMEOUT,
)
from invincat_cli.wecom.daemon_constants import (
    _DELIVERY_RETRIES as _DELIVERY_RETRIES,
)
from invincat_cli.wecom.daemon_constants import (
    _DELIVERY_RETRY_DELAY as _DELIVERY_RETRY_DELAY,
)
from invincat_cli.wecom.daemon_constants import (
    _FILE_PERMS as _FILE_PERMS,
)
from invincat_cli.wecom.daemon_constants import (
    _LOCK_FILENAME as _LOCK_FILENAME,
)
from invincat_cli.wecom.daemon_constants import (
    _LOG_FILENAME as _LOG_FILENAME,
)
from invincat_cli.wecom.daemon_constants import (
    _SOCKET_FILENAME as _SOCKET_FILENAME,
)
from invincat_cli.wecom.daemon_constants import (
    _SOCKET_TIMEOUT as _SOCKET_TIMEOUT,
)
from invincat_cli.wecom.daemon_constants import (
    _STARTUP_TIMEOUT as _STARTUP_TIMEOUT,
)
from invincat_cli.wecom.daemon_constants import (
    _STATE_FILENAME as _STATE_FILENAME,
)

logger = logging.getLogger(__name__)


def read_daemon_state(cwd: Path) -> dict[str, Any] | None:
    from invincat_cli.wecom.daemon_state import read_daemon_state as impl

    return impl(cwd)


def _write_daemon_state(config: WeComDaemonConfig) -> None:
    from invincat_cli.wecom.daemon_state import _write_daemon_state as impl

    return impl(config)


def _remove_daemon_state(config: WeComDaemonConfig) -> None:
    from invincat_cli.wecom.daemon_state import _remove_daemon_state as impl

    return impl(config)


def _open_lock_fd(cwd: Path) -> int:
    from invincat_cli.wecom.daemon_state import _open_lock_fd as impl

    return impl(cwd)


def acquire_daemon_lock(cwd: Path) -> int:
    from invincat_cli.wecom.daemon_state import acquire_daemon_lock as impl

    return impl(cwd)


def _read_lockfile_pid(cwd: Path) -> int | None:
    from invincat_cli.wecom.daemon_state import _read_lockfile_pid as impl

    return impl(cwd)


def is_daemon_running(cwd: Path) -> bool:
    from invincat_cli.wecom.daemon_state import is_daemon_running as impl

    return impl(cwd)


def _state_pid(state: dict[str, Any]) -> int | None:
    from invincat_cli.wecom.daemon_state import _state_pid as impl

    return impl(state)


def _verified_lock_owner_pid(cwd: Path, state: dict[str, Any]) -> int | None:
    from invincat_cli.wecom.daemon_state import _verified_lock_owner_pid as impl

    return impl(cwd, state)


def _signal_verified_daemon_owner(cwd: Path, state: dict[str, Any]) -> bool:
    from invincat_cli.wecom.daemon_state import _signal_verified_daemon_owner as impl

    return impl(cwd, state)


async def _socket_rpc(socket_path: Path, request: dict[str, Any]) -> dict[str, Any]:
    from invincat_cli.wecom.daemon_control import _socket_rpc as impl

    return await impl(socket_path, request)


async def get_daemon_status(cwd: Path) -> dict[str, Any]:
    from invincat_cli.wecom.daemon_control import get_daemon_status as impl

    return await impl(cwd)


async def stop_daemon(cwd: Path) -> bool:
    from invincat_cli.wecom.daemon_control import stop_daemon as impl

    return await impl(cwd)


def _write_startup_status(fd: int | None, status: str) -> None:
    from invincat_cli.wecom.daemon_process import _write_startup_status as impl

    return impl(fd, status)


def _read_startup_status(fd: int, *, timeout: float) -> str:
    from invincat_cli.wecom.daemon_process import _read_startup_status as impl

    return impl(fd, timeout=timeout)


def _wait_for_startup_result(startup_read_fd: int) -> None:
    from invincat_cli.wecom.daemon_process import _wait_for_startup_result as impl

    return impl(startup_read_fd)


def _fork_daemon(config: WeComDaemonConfig) -> int:
    from invincat_cli.wecom.daemon_process import _fork_daemon as impl

    return impl(config)


def start_daemon(config: WeComDaemonConfig) -> None:
    from invincat_cli.wecom.daemon_process import start_daemon as impl

    return impl(config)


async def start_daemon_async(config: WeComDaemonConfig) -> None:
    from invincat_cli.wecom.daemon_process import start_daemon_async as impl

    return await impl(config)


def run_daemon_foreground(config: WeComDaemonConfig) -> None:
    from invincat_cli.wecom.daemon_process import run_daemon_foreground as impl

    return impl(config)


def _redirect_stdio(log_file: Path, *, preserve_fds: tuple[int, ...] = ()) -> None:
    from invincat_cli.wecom.daemon_process import _redirect_stdio as impl

    return impl(log_file, preserve_fds=preserve_fds)


async def _daemon_main(
    config: WeComDaemonConfig, *, startup_fd: int | None = None
) -> None:
    from invincat_cli.wecom.daemon_runtime import _daemon_main as impl

    return await impl(config, startup_fd=startup_fd)


async def _wait_for_bridge_startup(
    bridge: Any, bridge_task: asyncio.Task[None]
) -> None:
    from invincat_cli.wecom.daemon_runtime import _wait_for_bridge_startup as impl

    return await impl(bridge, bridge_task)


async def _bridge_send_request(
    bridge_holder: list[Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    from invincat_cli.wecom.daemon_runtime import _bridge_send_request as impl

    return await impl(bridge_holder, payload)


def _make_build_agent_input(cwd: Path):
    from invincat_cli.wecom.daemon_runtime import _make_build_agent_input as impl

    return impl(cwd)


def _scheduled_task_wecom_chatid(task: Any) -> str:
    from invincat_cli.wecom.daemon_scheduler import scheduled_task_wecom_chatid

    return scheduled_task_wecom_chatid(task)


def _task_visible_to_wecom_daemon(task: Any, cwd: Path) -> bool:
    from invincat_cli.wecom.daemon_scheduler import task_visible_to_wecom_daemon

    return task_visible_to_wecom_daemon(task, cwd)


async def _run_scheduler(
    config: WeComDaemonConfig,
    handler: Any,
    bridge_holder: list[Any],
    stop_event: asyncio.Event,
    runner_holder: list[Any],
) -> None:
    from invincat_cli.wecom.daemon_scheduler import run_scheduler

    await run_scheduler(config, handler, bridge_holder, stop_event, runner_holder)


async def _deliver_scheduled_timeout_result(
    store: Any,
    bridge_holder: list[Any],
    *,
    task_id: str,
) -> bool:
    from invincat_cli.wecom.daemon_scheduler import deliver_scheduled_timeout_result

    return await deliver_scheduled_timeout_result(
        store,
        bridge_holder,
        task_id=task_id,
    )


async def _deliver_scheduled_text(
    bridge: Any,
    chatid: str,
    content: str,
    *,
    label: str,
    task_title: str,
) -> bool:
    from invincat_cli.wecom.daemon_scheduler import deliver_scheduled_text

    return await deliver_scheduled_text(
        bridge,
        chatid,
        content,
        label=label,
        task_title=task_title,
    )


async def _noop_on_content(_content: str) -> None:
    """No-op on_content for scheduled tasks - active push only, no streaming."""


async def _handle_socket_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    bridge: Any,
    handler: Any,
    stop_event: asyncio.Event,
) -> None:
    from invincat_cli.wecom.daemon_control import _handle_socket_client as impl

    return await impl(reader, writer, bridge, handler, stop_event)
