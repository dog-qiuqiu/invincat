"""Turn cleanup, token persistence, and assistant text flushing helpers."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from invincat_cli.i18n import t
from invincat_cli.textual_adapter.ui_adapter import TextualUIAdapter
from invincat_cli.widgets.messages import AppMessage, AssistantMessage

logger = logging.getLogger(__name__)


async def handle_interrupt_cleanup(
    *,
    adapter: TextualUIAdapter,
    agent: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    pending_text_by_namespace: dict[tuple, str],
    captured_input_tokens: int,
    captured_output_tokens: int,
    turn_stats: Any,  # noqa: ANN401
    start_time: float,
    build_interrupted_ai_message_func: Any,  # noqa: ANN401
    report_and_persist_tokens_func: Any,  # noqa: ANN401
) -> None:
    """Shared cleanup for CancelledError and KeyboardInterrupt."""
    from langchain_core.messages import HumanMessage

    if adapter._set_active_message:
        adapter._set_active_message(None)

    interrupted_msg = build_interrupted_ai_message_func(
        pending_text_by_namespace,
        adapter._current_tool_messages,
    )

    for tool_msg in list(adapter._current_tool_messages.values()):
        tool_msg.set_rejected()
    adapter._current_tool_messages.clear()

    try:
        if adapter._set_spinner:
            await adapter._set_spinner(None)
        await adapter._mount_message(AppMessage(t("tool.interrupted_by_user")))
    except (asyncio.CancelledError, Exception):
        logger.debug("UI cleanup partially failed during interrupt", exc_info=True)

    async def _aupdate_state_with_retry(update_kwargs: dict) -> None:
        delay = 1.0
        for attempt in range(3):
            try:
                await agent.aupdate_state(config, update_kwargs)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt == 2:
                    logger.warning(
                        "Failed to save interrupted state after 3 attempts",
                        exc_info=True,
                    )
                    return
                logger.debug(
                    "aupdate_state attempt %d failed; retrying in %.1fs",
                    attempt + 1,
                    delay,
                    exc_info=True,
                )
                await asyncio.sleep(delay)
                delay *= 2

    try:
        if interrupted_msg:
            await _aupdate_state_with_retry({"messages": [interrupted_msg]})

        cancellation_msg = HumanMessage(
            content="[SYSTEM] Task interrupted by user. "
            "Previous operation was cancelled."
        )
        await _aupdate_state_with_retry({"messages": [cancellation_msg]})
    except asyncio.CancelledError:
        logger.debug("aupdate_state retry cancelled - skipping state save")
    except Exception:
        logger.warning("Failed to save interrupted state", exc_info=True)

    approximate = interrupted_msg is not None
    turn_stats.wall_time_seconds = time.monotonic() - start_time
    try:
        await report_and_persist_tokens_func(
            adapter,
            agent,
            config,
            captured_input_tokens,
            captured_output_tokens,
            shield=True,
            approximate=approximate,
        )
    except (asyncio.CancelledError, Exception):
        logger.debug("Token reporting failed during interrupt cleanup", exc_info=True)


async def persist_context_tokens(
    agent: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    tokens: int,
) -> None:
    """Best-effort persist of the context token count into graph state."""

    def _is_connectivity_error(exc: Exception) -> bool:
        names = {cls.__name__ for cls in type(exc).__mro__}
        if names & {
            "ConnectError",
            "ConnectTimeout",
            "ReadTimeout",
            "WriteTimeout",
            "PoolTimeout",
            "NetworkError",
            "RemoteProtocolError",
            "TransportError",
        }:
            return True
        text = str(exc).lower()
        return "connection attempts failed" in text or "connection refused" in text

    max_attempts = 3
    delay = 0.2
    ensured_thread = False
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            await agent.aupdate_state(config, {"_context_tokens": tokens})
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_exc = exc

            ensure_thread = getattr(agent, "aensure_thread", None)
            if not ensured_thread and callable(ensure_thread):
                try:
                    await ensure_thread(config)
                    ensured_thread = True
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug(
                        "aensure_thread failed while persisting _context_tokens",
                        exc_info=True,
                    )

            if attempt < max_attempts and _is_connectivity_error(exc):
                await asyncio.sleep(delay)
                delay *= 2
                continue
            break

    if last_exc is None:
        return

    if _is_connectivity_error(last_exc):
        logger.warning(
            "Failed to persist _context_tokens=%d due to temporary connection issue; "
            "token count may be stale on resume (%s)",
            tokens,
            last_exc,
        )
    else:
        logger.warning(
            "Failed to persist _context_tokens=%d; token count may be stale on resume",
            tokens,
            exc_info=True,
        )


async def report_and_persist_tokens(
    adapter: TextualUIAdapter,
    agent: Any,  # noqa: ANN401
    config: Any,  # noqa: ANN401
    captured_input_tokens: int,
    captured_output_tokens: int,
    *,
    shield: bool = False,
    approximate: bool = False,
    persist_context_tokens_func: Any,  # noqa: ANN401
) -> None:
    """Update the token display and best-effort persist to graph state."""
    if captured_input_tokens or captured_output_tokens:
        if adapter._on_tokens_update:
            adapter._on_tokens_update(captured_input_tokens, approximate=approximate)
        if shield:
            try:
                await persist_context_tokens_func(agent, config, captured_input_tokens)
            except (Exception, asyncio.CancelledError):
                logger.debug(
                    "Token persist suppressed during interrupt cleanup",
                    exc_info=True,
                )
        else:
            await persist_context_tokens_func(agent, config, captured_input_tokens)
    elif adapter._on_tokens_show:
        adapter._on_tokens_show(approximate=approximate)


async def flush_assistant_text_ns(
    adapter: TextualUIAdapter,
    text: str,
    ns_key: tuple,
    assistant_message_by_namespace: dict[tuple, Any],
    *,
    assistant_message_cls: type = AssistantMessage,
) -> None:
    """Flush accumulated assistant text for a specific namespace."""
    if not text.strip():
        return

    current_msg = assistant_message_by_namespace.get(ns_key)
    if current_msg is None:
        msg_id = f"asst-{uuid.uuid4().hex[:8]}"
        current_msg = assistant_message_cls(text, id=msg_id)
        await adapter._mount_message(current_msg)
        await current_msg.write_initial_content()
        assistant_message_by_namespace[ns_key] = current_msg
    else:
        await current_msg.stop_stream()

    if adapter._sync_message_content and current_msg.id:
        adapter._sync_message_content(current_msg.id, current_msg._content)

    if adapter._set_active_message:
        adapter._set_active_message(None)
