"""Health polling for local LangGraph server startup."""

from __future__ import annotations

import asyncio
import logging
import subprocess  # noqa: S404
import time
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


async def wait_for_server_healthy(
    url: str,
    *,
    timeout: float,
    process: subprocess.Popen | None = None,
    read_log: Callable[[], str] | None = None,
    local: bool = False,
    local_poll_interval: float,
    remote_poll_interval: float,
    asyncio_module: ModuleType = asyncio,
    time_module: ModuleType = time,
    logger: logging.Logger,
) -> None:
    """Poll a LangGraph server health endpoint until it responds."""
    import httpx

    poll_interval = local_poll_interval if local else remote_poll_interval
    health_url = f"{url}/ok"
    deadline = time_module.monotonic() + timeout
    last_status: int | None = None
    last_exc: Exception | None = None

    async with httpx.AsyncClient() as client:
        while time_module.monotonic() < deadline:
            if process and process.poll() is not None:
                output = read_log() if read_log else ""
                msg = f"Server process exited with code {process.returncode}"
                if output:
                    msg += f"\n{output[-3000:]}"
                raise RuntimeError(msg)

            try:
                resp = await client.get(health_url, timeout=2)
                if resp.status_code == 200:  # noqa: PLR2004
                    logger.info("Server is healthy at %s", url)
                    return
                last_status = resp.status_code
                logger.debug("Health check returned status %d", resp.status_code)
            except (httpx.TransportError, OSError) as exc:
                logger.debug("Health check attempt failed: %s", exc)
                last_exc = exc

            await asyncio_module.sleep(poll_interval)

    msg = f"Server did not become healthy within {timeout}s"
    if last_status is not None:
        msg += f" (last status: {last_status})"
    elif last_exc is not None:
        msg += f" (last error: {last_exc})"
    raise RuntimeError(msg)
