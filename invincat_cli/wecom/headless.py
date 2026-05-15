"""Headless WeCom message handler for the background daemon.

Adapts the RemoteAgent (LangGraph server) to the WeComMessageResponder
interface, without any Textual UI dependency.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from invincat_cli.wecom.headless_stream import (
    DebouncedContentEmitter as _DebouncedContentEmitter,
)

logger = logging.getLogger(__name__)

_STREAM_CHUNK_LENGTH = 3
_MESSAGE_DATA_LENGTH = 2
_HITL_AUTO_APPROVE_CAP = 50
_MAX_SESSIONS = 256  # bound _sessions LRU; older idle entries are evicted
_STREAM_UPDATE_INTERVAL = 0.5


class HeadlessWeComHandler:
    """Run WeCom agent turns via RemoteAgent without the Textual UI.

    One instance is shared for all inbound messages; per-chat serialization
    is enforced by a per-chatid asyncio.Lock so the same user's rapid
    messages are processed in order.
    """

    def __init__(
        self,
        *,
        agent: Any,
        cwd: Path,
        send_request: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        on_schedule_run_now: Callable[[Any], Awaitable[None]] | None = None,
        max_concurrent_turns: int = 1,
    ) -> None:
        self._agent = agent
        self._cwd = cwd
        self._send_request = send_request
        self._on_schedule_run_now = on_schedule_run_now
        self._turn_semaphore = asyncio.Semaphore(max(1, max_concurrent_turns))
        # chatid → (thread_id, lock).  OrderedDict so we can evict the
        # least-recently-used entry when the cache exceeds _MAX_SESSIONS,
        # bounding memory across a long-running daemon.
        self._sessions: OrderedDict[str, tuple[str, asyncio.Lock]] = OrderedDict()
        self._messages_handled = 0

    @property
    def messages_handled(self) -> int:
        return self._messages_handled

    # ------------------------------------------------------------------
    # Public interface — compatible with WeComMessageResponder.run_turn
    # ------------------------------------------------------------------

    async def run_turn(
        self,
        text: str,
        inbound_frame: dict[str, Any],
        on_content: Callable[[str], Awaitable[None]],
        *,
        runtime_context: dict[str, Any] | None = None,
    ) -> str:
        """Run one agent turn and return the final answer."""
        chatid = self._resolve_chatid(inbound_frame)
        thread_id, lock = self._get_or_create_session(chatid)
        async with lock, self._turn_semaphore:
            try:
                answer = await self._run_agent_turn(
                    text,
                    thread_id=thread_id,
                    inbound_frame=inbound_frame,
                    on_content=on_content,
                    runtime_context=runtime_context,
                )
            except Exception as exc:
                logger.warning("Headless agent turn failed: %s", exc, exc_info=True)
                raise
            self._messages_handled += 1
            return answer

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_chatid(self, frame: dict[str, Any]) -> str:
        body = frame.get("body") or {}
        chatid = body.get("chatid")
        if isinstance(chatid, str) and chatid:
            return chatid
        from_obj = body.get("from") or {}
        userid = from_obj.get("userid") if isinstance(from_obj, dict) else None
        if isinstance(userid, str) and userid:
            return userid
        return "default"

    def _get_or_create_session(self, chatid: str) -> tuple[str, asyncio.Lock]:
        existing = self._sessions.get(chatid)
        if existing is not None:
            self._sessions.move_to_end(chatid)
            return existing
        from invincat_cli.sessions import generate_thread_id

        self._sessions[chatid] = (generate_thread_id(), asyncio.Lock())
        self._evict_idle_sessions()
        return self._sessions[chatid]

    def _evict_idle_sessions(self) -> None:
        """Drop the oldest idle (lock not held) sessions until under the cap.

        We never evict an entry whose lock is currently held: that would let
        a concurrent ``run_turn`` re-create the entry under a new lock,
        breaking per-chat serialisation.  In the worst case (every cached
        session busy) the cache is allowed to exceed ``_MAX_SESSIONS``
        temporarily — capacity is restored as turns complete.
        """
        if len(self._sessions) <= _MAX_SESSIONS:
            return
        for chatid in list(self._sessions.keys()):
            if len(self._sessions) <= _MAX_SESSIONS:
                break
            _, lock = self._sessions[chatid]
            if not lock.locked():
                self._sessions.pop(chatid, None)

    async def _run_agent_turn(
        self,
        text: str,
        *,
        thread_id: str,
        inbound_frame: dict[str, Any],
        on_content: Callable[[str], Awaitable[None]],
        runtime_context: dict[str, Any] | None = None,
    ) -> str:
        from langchain_core.messages import AIMessage, ToolMessage
        from langgraph.types import Command

        from invincat_cli.config import build_stream_config
        from invincat_cli.wecom.file import (
            WECOM_FILE_TOOL_NAME,
            parse_wecom_file_request,
        )
        from invincat_cli.wecom.media import send_wecom_file_from_tool_payload

        config = build_stream_config(thread_id, "agent")
        # WeComFileMiddleware reads context["wecom_enabled"] from the LangGraph runtime.
        # RemoteAgent passes context as a separate kwarg (not inside configurable).
        wecom_context = {**(runtime_context or {}), "wecom_enabled": True}

        from invincat_cli.wecom.session import format_wecom_progress_line

        accumulated = ""
        processed_file_tool_ids: set[str] = set()
        stream_input: Any = {"messages": [{"role": "user", "content": text}]}

        # Tool progress state
        running_tool: str | None = None
        completed_tools: int = 0
        progress_tick: int = 0
        stream_emitter = _DebouncedContentEmitter(on_content)

        async def _emit_immediate(content: str) -> None:
            await stream_emitter.flush()
            try:
                await on_content(content)
            except Exception:
                logger.debug("on_content callback failed", exc_info=True)

        try:
            for _ in range(_HITL_AUTO_APPROVE_CAP):
                pending_resumes: dict[str, Any] = {}

                async for chunk in self._agent.astream(
                    stream_input,
                    config=config,
                    context=wecom_context,
                    stream_mode=["messages", "updates"],
                    subgraphs=True,
                    durability="exit",
                ):
                    if (
                        not isinstance(chunk, tuple)
                        or len(chunk) != _STREAM_CHUNK_LENGTH
                    ):
                        continue
                    namespace, mode, data = chunk

                    # Only process main-agent output; ignore planner/subagent namespaces.
                    if namespace:
                        continue

                    if mode == "messages":
                        if (
                            not isinstance(data, tuple)
                            or len(data) != _MESSAGE_DATA_LENGTH
                        ):
                            continue
                        message_obj, _meta = data

                        if isinstance(message_obj, AIMessage):
                            # Skip internal middleware LLM runs (memory agent, summarization).
                            # These have lc_source set in metadata and must never reach the user.
                            lc_source = (
                                _meta.get("lc_source")
                                if isinstance(_meta, dict)
                                else None
                            )
                            if lc_source in {"memory_agent", "summarization"}:
                                continue

                            # Detect tool calls starting — emit progress before AI text arrives.
                            # Three formats to check (model-dependent):
                            # 1. tool_calls — complete AIMessage (any model, post-stream)
                            # 2. tool_call_chunks with name — OpenAI-style streaming delta
                            # 3. content[{"type":"tool_use","name":...}] — Anthropic streaming delta
                            detected_tool: str | None = None
                            tool_calls = list(
                                getattr(message_obj, "tool_calls", None) or []
                            )
                            if tool_calls:
                                first = tool_calls[0]
                                detected_tool = (
                                    first.get("name")
                                    if isinstance(first, dict)
                                    else getattr(first, "name", None)
                                ) or None
                            if not detected_tool:
                                raw_chunks = (
                                    getattr(message_obj, "tool_call_chunks", None) or []
                                )
                                for c in raw_chunks:
                                    name = (
                                        c.get("name")
                                        if isinstance(c, dict)
                                        else getattr(c, "name", "")
                                    )
                                    if name:
                                        detected_tool = name
                                        break
                            if not detected_tool:
                                content_blocks = getattr(message_obj, "content", None)
                                if isinstance(content_blocks, list):
                                    for block in content_blocks:
                                        if (
                                            isinstance(block, dict)
                                            and block.get("type") == "tool_use"
                                            and block.get("name")
                                        ):
                                            detected_tool = block["name"]
                                            break

                            # Only emit progress when the tool name is new (avoid duplicate flushes
                            # for each streaming delta chunk of the same tool call).
                            if detected_tool and detected_tool != running_tool:
                                running_tool = detected_tool
                                progress = format_wecom_progress_line(
                                    running_tool=running_tool,
                                    completed_tools=completed_tools,
                                    assistant_started=False,
                                    tick=progress_tick,
                                )
                                progress_tick += 1
                                await _emit_immediate(progress)
                            text_delta = self._extract_ai_text(message_obj)
                            if text_delta:
                                accumulated += text_delta
                                await stream_emitter.emit(accumulated)

                        elif isinstance(message_obj, ToolMessage):
                            tool_name = getattr(message_obj, "name", "")

                            # Persist schedule management payloads that the TUI would
                            # normally handle via on_schedule_payload / _handle_schedule_tool_payload.
                            from invincat_cli.scheduler.tool import (
                                parse_schedule_tool_result,
                            )

                            sched_payload = parse_schedule_tool_result(
                                message_obj.content
                            )
                            if sched_payload is not None:
                                try:
                                    await self._process_schedule_payload(
                                        sched_payload, inbound_frame
                                    )
                                except Exception:
                                    logger.warning(
                                        "Schedule payload processing failed",
                                        exc_info=True,
                                    )
                                    raise

                            if tool_name == WECOM_FILE_TOOL_NAME:
                                payload = parse_wecom_file_request(message_obj.content)
                                if payload is not None:
                                    dedupe_id = (
                                        payload.get("tool_call_id")
                                        or getattr(message_obj, "tool_call_id", None)
                                        or str(id(payload))
                                    )
                                    if dedupe_id not in processed_file_tool_ids:
                                        processed_file_tool_ids.add(str(dedupe_id))
                                        try:
                                            await send_wecom_file_from_tool_payload(
                                                inbound_frame,
                                                payload,
                                                cwd=self._cwd,
                                                send_request=self._send_request,
                                            )
                                        except Exception as exc:
                                            logger.warning(
                                                "WeCom file send failed: %s",
                                                exc,
                                                exc_info=True,
                                            )

                            # Tool completed — update counters and emit progress.
                            completed_tools += 1
                            running_tool = None
                            if not accumulated:
                                # Only show progress if AI text hasn't started yet.
                                progress = format_wecom_progress_line(
                                    running_tool=None,
                                    completed_tools=completed_tools,
                                    assistant_started=False,
                                    tick=progress_tick,
                                )
                                progress_tick += 1
                                await _emit_immediate(progress)

                    elif mode == "updates" and isinstance(data, dict):
                        for interrupt_obj in data.get("__interrupt__", []):
                            pending_resumes[interrupt_obj.id] = {
                                "decisions": [{"type": "approve"}]
                            }

                if not pending_resumes:
                    break
                # Resume all pending HITL interrupts with auto-approve
                stream_input = Command(resume=pending_resumes)
        finally:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                await stream_emitter.close()
            else:
                await stream_emitter.flush()

        return accumulated.strip() or "（空回复）"

    async def _process_schedule_payload(
        self,
        payload: dict[str, Any],
        inbound_frame: dict[str, Any],
    ) -> None:
        """Persist a schedule management payload from the agent to the DB."""
        from invincat_cli.wecom.headless_schedule import process_schedule_payload

        await process_schedule_payload(
            payload=payload,
            inbound_frame=inbound_frame,
            cwd=self._cwd,
            on_schedule_run_now=self._on_schedule_run_now,
        )

    @staticmethod
    def _extract_ai_text(message: Any) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            return "".join(parts)
        return ""
