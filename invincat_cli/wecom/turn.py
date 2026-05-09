"""WeCom turn runner that adapts inbound messages to the local CLI session."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from invincat_cli.wecom.media import send_wecom_file_from_tool_payload
from invincat_cli.wecom.session import (
    WECOM_AGENT_TIMEOUT,
    WECOM_BLINK_INTERVAL,
    WECOM_FILE_NOTIFY_HOLD,
    WECOM_IDLE_TIMEOUT,
    WECOM_PROGRESS_MAX_INTERVAL,
    WECOM_STREAM_BLINK_DELAY,
    format_wecom_progress_line,
)
from invincat_cli.widgets.message_store import MessageData, MessageType, ToolStatus

logger = logging.getLogger(__name__)


class WeComTurnRunner:
    """Run one serialized WeCom-injected CLI turn."""

    def __init__(
        self,
        *,
        lock: asyncio.Lock,
        cwd: str | Path,
        is_busy: Callable[[], bool],
        get_messages: Callable[[], list[MessageData]],
        handle_user_message: Callable[
            [str, Callable[[str, str], Awaitable[None]], Callable[[dict[str, Any]], Awaitable[None]]],
            Awaitable[None],
        ],
        send_request: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        cancel_timed_out_turn: Callable[[], None],
        on_content: Callable[[str], Awaitable[None]] | None = None,
        enter_turn_context: Callable[[], None] | None = None,
        exit_turn_context: Callable[[], None] | None = None,
    ) -> None:
        self._lock = lock
        self._cwd = cwd
        self._is_busy = is_busy
        self._get_messages = get_messages
        self._handle_user_message = handle_user_message
        self._send_request = send_request
        self._cancel_timed_out_turn = cancel_timed_out_turn
        self._on_content = on_content
        self._enter_turn_context = enter_turn_context
        self._exit_turn_context = exit_turn_context

    async def run(self, text: str, *, inbound_frame: dict[str, Any]) -> str:
        """Inject one WeCom message into the current session and return the final answer."""
        async with self._lock:
            if self._enter_turn_context is not None:
                self._enter_turn_context()
            try:
                return await self._run_locked(text, inbound_frame=inbound_frame)
            finally:
                if self._exit_turn_context is not None:
                    self._exit_turn_context()

    async def _run_locked(self, text: str, *, inbound_frame: dict[str, Any]) -> str:
        """Run one WeCom turn after the caller-owned lock has been acquired."""
        answer_started = False
        cursor_visible = False
        last_file_notified_mono: float = 0.0
        last_delta_mono: float = 0.0
        last_streamed_text: str = ""
        idle_waited = 0.0
        while self._is_busy():
            if idle_waited >= WECOM_IDLE_TIMEOUT:
                return "当前会话忙碌，请稍后再试。"
            await asyncio.sleep(0.1)
            idle_waited += 0.1

        before = self._get_messages()
        before_ids: set[str] = {m.id for m in before}
        before_assistant_count = sum(1 for m in before if m.type == MessageType.ASSISTANT)
        before_error_count = sum(1 for m in before if m.type == MessageType.ERROR)

        async def _on_text_delta(delta: str, accumulated: str) -> None:
            nonlocal answer_started, last_delta_mono, last_streamed_text, cursor_visible
            answer_started = True
            last_delta_mono = asyncio.get_running_loop().time()
            last_streamed_text = accumulated
            cursor_visible = False
            logger.debug(
                "wecom text delta received chars=%d accumulated=%d",
                len(delta),
                len(accumulated),
            )
            if self._on_content is not None:
                await self._on_content(accumulated)

        async def _on_wecom_file_request(payload: dict[str, Any]) -> None:
            nonlocal last_file_notified_mono
            try:
                await send_wecom_file_from_tool_payload(
                    inbound_frame,
                    payload,
                    cwd=self._cwd,
                    send_request=self._send_request,
                )
                if self._on_content is not None:
                    filename = payload.get("filename") or payload.get("path") or "文件"
                    await self._on_content(f"已发送文件：{filename}")
                last_file_notified_mono = asyncio.get_running_loop().time()
            except Exception as exc:
                logger.warning("wecom file send failed: %s", exc, exc_info=True)
                if self._on_content is not None:
                    await self._on_content(f"文件发送失败：{exc}")
                last_file_notified_mono = asyncio.get_running_loop().time()

        await self._handle_user_message(
            text,
            _on_text_delta,
            _on_wecom_file_request,
        )

        sent_tool_ids: set[str] = set()
        completed_tools = 0
        last_pushed: str = ""
        last_push_mono: float = 0.0
        progress_tick = 0
        last_progress_key: tuple[str | None, int, bool] | None = None

        agent_waited = 0.0
        while self._is_busy():
            if agent_waited >= WECOM_AGENT_TIMEOUT:
                self._cancel_timed_out_turn()
                return "处理超时，请稍后再试。"
            await asyncio.sleep(0.1)
            agent_waited += 0.1

            (
                running_tool,
                assistant_started,
                completed_tools,
            ) = self._observe_progress(before_ids, sent_tool_ids, completed_tools)

            if self._on_content is not None and (
                not answer_started or running_tool is not None
            ):
                now = asyncio.get_running_loop().time()
                if (now - last_file_notified_mono) < WECOM_FILE_NOTIFY_HOLD:
                    continue
                progress_key = (running_tool, completed_tools, assistant_started)
                if (
                    last_pushed == ""
                    or progress_key != last_progress_key
                    or (now - last_push_mono) >= WECOM_PROGRESS_MAX_INTERVAL
                ):
                    display = format_wecom_progress_line(
                        running_tool=running_tool,
                        completed_tools=completed_tools,
                        assistant_started=assistant_started,
                        tick=progress_tick,
                    )
                    if not answer_started or running_tool is not None:
                        await self._on_content(display)
                        last_pushed = display
                        last_progress_key = progress_key
                        last_push_mono = now
                        progress_tick += 1
            elif self._on_content is not None and answer_started and last_streamed_text:
                now = asyncio.get_running_loop().time()
                idle = now - last_delta_mono
                if (
                    idle >= WECOM_STREAM_BLINK_DELAY
                    and (now - last_push_mono) >= WECOM_BLINK_INTERVAL
                ):
                    cursor_visible = not cursor_visible
                    suffix = " ▏" if cursor_visible else ""
                    await self._on_content(last_streamed_text + suffix)
                    last_push_mono = now

        return self._final_answer(
            before_ids=before_ids,
            before_assistant_count=before_assistant_count,
            before_error_count=before_error_count,
            sent_tool_ids=sent_tool_ids,
        )

    def _observe_progress(
        self,
        before_ids: set[str],
        sent_tool_ids: set[str],
        completed_tools: int,
    ) -> tuple[str | None, bool, int]:
        running_tool: str | None = None
        assistant_started = False
        for message in self._get_messages():
            if message.id not in before_ids and message.type == MessageType.TOOL:
                if message.tool_status in {ToolStatus.PENDING, ToolStatus.RUNNING, None}:
                    running_tool = message.tool_name or running_tool
                elif message.id not in sent_tool_ids:
                    sent_tool_ids.add(message.id)
                    completed_tools += 1

            if (
                message.id not in before_ids
                and message.type == MessageType.ASSISTANT
                and bool(message.content)
            ):
                assistant_started = True
        return running_tool, assistant_started, completed_tools

    def _final_answer(
        self,
        *,
        before_ids: set[str],
        before_assistant_count: int,
        before_error_count: int,
        sent_tool_ids: set[str],
    ) -> str:
        after = self._get_messages()
        for message in after:
            if (
                message.id not in before_ids
                and message.id not in sent_tool_ids
                and message.type == MessageType.TOOL
            ):
                sent_tool_ids.add(message.id)

        assistant_msgs = [m for m in after if m.type == MessageType.ASSISTANT]
        if len(assistant_msgs) > before_assistant_count:
            return assistant_msgs[-1].content.strip() or "（空回复）"

        all_errors = [m for m in after if m.type == MessageType.ERROR]
        new_errors = all_errors[before_error_count:]
        if new_errors:
            return new_errors[-1].content.strip()
        return "未获取到有效回复。"
