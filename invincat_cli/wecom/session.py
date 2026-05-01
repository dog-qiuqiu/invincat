"""Helpers for WeCom turn progress and user-facing messages."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from invincat_cli.wecom.protocol import build_wecom_stream_frame

logger = logging.getLogger(__name__)

WECOM_IDLE_TIMEOUT = 30.0
WECOM_AGENT_TIMEOUT = 30 * 60.0
WECOM_PROGRESS_MAX_INTERVAL = 0.25
WECOM_FILE_NOTIFY_HOLD = 2.0
WECOM_STREAM_BLINK_DELAY = 1.0
WECOM_BLINK_INTERVAL = 0.6


def wecom_user_facing_error(exc: Exception) -> str:
    text = str(exc).strip()
    if text:
        return text
    return type(exc).__name__


def format_wecom_progress_line(
    *,
    running_tool: str | None,
    completed_tools: int,
    assistant_started: bool,
    tick: int = 0,
) -> str:
    """Format the one-line in-place progress update shown before final output."""
    dots = "." * (tick % 3 + 1)
    if running_tool:
        if completed_tools:
            return f"处理中：正在执行工具 `{running_tool}`，已完成 {completed_tools} 个{dots}"
        return f"处理中：正在执行工具 `{running_tool}`{dots}"
    if assistant_started:
        if completed_tools:
            return f"处理中：已完成 {completed_tools} 个工具调用，正在整理回复{dots}"
        return f"处理中：正在整理回复{dots}"
    if completed_tools:
        return f"处理中：已完成 {completed_tools} 个工具调用，正在继续分析{dots}"
    return f"处理中：正在分析问题{dots}"


class WeComMessageResponder:
    """Convert one inbound WeCom frame into streaming and final response frames."""

    def __init__(
        self,
        *,
        enqueue: Callable[[dict[str, Any]], None],
        flush: Callable[[], Awaitable[bool]],
        build_agent_input: Callable[[dict[str, Any]], Awaitable[str]],
        run_turn: Callable[
            [str, dict[str, Any], Callable[[str], Awaitable[None]]],
            Awaitable[str],
        ],
        report_error: Callable[[str], Awaitable[None]],
    ) -> None:
        self._enqueue = enqueue
        self._flush = flush
        self._build_agent_input = build_agent_input
        self._run_turn = run_turn
        self._report_error = report_error

    async def handle(self, frame: dict[str, Any]) -> None:
        """Process one inbound WeCom message and deliver a streaming reply."""
        stream_id = uuid.uuid4().hex
        self._enqueue(
            build_wecom_stream_frame(frame, stream_id, "⏳ 正在处理，请稍候…", finish=False)
        )
        ack_sent = await self._flush()
        logger.debug("wecom stream ACK sent=%s stream_id=%s", ack_sent, stream_id)

        async def _on_content(content: str) -> None:
            self._enqueue(
                build_wecom_stream_frame(frame, stream_id, content, finish=False)
            )
            try:
                await self._flush()
            except Exception as exc:
                logger.warning("wecom stream content update failed: %s", exc)

        try:
            text = await self._build_agent_input(frame)
            answer = await self._run_turn(text, frame, _on_content)
        except Exception as exc:
            logger.warning("wecom message process failed: %s", exc, exc_info=True)
            detail = wecom_user_facing_error(exc)
            with suppress(Exception):
                await self._report_error(f"WeCom message failed: {detail}")
            answer = f"处理消息时发生异常：{detail}"

        self._enqueue(build_wecom_stream_frame(frame, stream_id, answer, finish=True))
        await self._flush()
