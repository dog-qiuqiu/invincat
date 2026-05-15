"""Streaming helpers for headless WeCom turns."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

logger = logging.getLogger(__name__)

STREAM_UPDATE_INTERVAL = 0.5


class DebouncedContentEmitter:
    """Coalesce high-frequency stream updates while preserving the latest text."""

    def __init__(
        self,
        on_content: Callable[[str], Awaitable[None]],
        *,
        interval: float = STREAM_UPDATE_INTERVAL,
    ) -> None:
        self._on_content = on_content
        self._interval = max(0.0, interval)
        self._latest: str | None = None
        self._last_sent: str | None = None
        self._last_sent_at = 0.0
        self._task: asyncio.Task[None] | None = None

    async def emit(self, content: str) -> None:
        """Send immediately when due; otherwise schedule one latest-text update."""
        self._latest = content
        now = asyncio.get_running_loop().time()
        if self._last_sent_at == 0.0 or now - self._last_sent_at >= self._interval:
            await self.flush()
            return
        if self._task is None or self._task.done():
            delay = max(0.0, self._interval - (now - self._last_sent_at))
            self._task = asyncio.create_task(self._send_later(delay))

    async def flush(self) -> None:
        """Cancel any delayed send and deliver the latest pending content now."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._task = None
        latest = self._latest
        if latest is None or latest == self._last_sent:
            return
        await self._send(latest)

    async def close(self) -> None:
        """Cancel a pending delayed send without emitting another update."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._task = None

    async def _send_later(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            latest = self._latest
            if latest is not None and latest != self._last_sent:
                await self._send(latest)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("on_content callback failed", exc_info=True)
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def _send(self, content: str) -> None:
        try:
            await self._on_content(content)
        except Exception:
            logger.debug("on_content callback failed", exc_info=True)
        finally:
            self._last_sent = content
            self._last_sent_at = asyncio.get_running_loop().time()
