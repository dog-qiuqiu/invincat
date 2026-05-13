"""Thread link helpers for the Textual app."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from textual.content import Content
from textual.style import Style as TStyle


async def build_thread_message(
    prefix: str,
    thread_id: str,
    *,
    build_url: Callable[[str], str | None] | None = None,
    timeout: float = 2.0,
) -> str | Content:
    """Build a thread status message, hyperlinking the ID when possible."""
    if build_url is None:
        from invincat_cli.config import build_langsmith_thread_url

        build_url = build_langsmith_thread_url

    try:
        url = await asyncio.wait_for(
            asyncio.to_thread(build_url, thread_id),
            timeout=timeout,
        )
    except (TimeoutError, Exception):  # noqa: BLE001
        url = None

    if url:
        return Content.assemble(
            f"{prefix}: ",
            (thread_id, TStyle(link=url)),
        )
    return f"{prefix}: {thread_id}"
