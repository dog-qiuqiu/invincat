"""Memory extraction workflow used by MemoryAgentMiddleware."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from copy import deepcopy
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from invincat_cli.memory import store_ops as _ops
from invincat_cli.memory.prompts import (
    _FINAL_INSTRUCTION_TEMPLATE as _FINAL_INSTRUCTION_TEMPLATE,
)
from invincat_cli.memory.prompts import _SYSTEM_PROMPT as _SYSTEM_PROMPT

logger = logging.getLogger(__name__)


async def extract_and_write(
    middleware: Any,
    model: Any,
    messages: list[Any],
    *,
    thread_id: str,
    source_anchor: str,
    preloaded_stores: tuple[dict[str, Any] | None, dict[str, Any] | None]
    | None = None,
) -> list[str] | None:
    """Extract memory operations with the memory model and write changed stores."""
    written_store_paths: list[str] = []
    try:
        last_human = middleware._last_human_text(messages)
        explicit_memory_request = _ops._is_explicit_memory_request(last_human)
        target_language = _ops._detect_target_language(last_human)

        if preloaded_stores is not None:
            user_store, project_store = preloaded_stores
        else:
            user_store = await asyncio.to_thread(
                middleware._load_or_recover_store, "user", thread_id, source_anchor
            )
            project_store = await asyncio.to_thread(
                middleware._load_or_recover_store, "project", thread_id, source_anchor
            )

        unreadable_scopes: list[str] = []
        if isinstance(user_store, dict) and user_store.get("__read_error__"):
            unreadable_scopes.append("user")
        if isinstance(project_store, dict) and project_store.get("__read_error__"):
            unreadable_scopes.append("project")
        if unreadable_scopes:
            logger.warning(
                "Memory agent: skip write because store is unreadable (scopes=%s)",
                ",".join(unreadable_scopes),
            )
            return []
        user_before = deepcopy(user_store)
        project_before = deepcopy(project_store)

        if preloaded_stores is None:
            cleanup_operations = _ops._build_invalid_fact_cleanup_operations(
                user_store,
                project_store,
            )
            if cleanup_operations:
                (
                    user_store,
                    project_store,
                    cleanup_written,
                ) = await middleware._apply_and_write_memory_operations(
                    user_store,
                    project_store,
                    user_before,
                    project_before,
                    cleanup_operations,
                    thread_id=thread_id,
                    source_anchor=source_anchor,
                    now_iso=_ops._iso_now(),
                )
                written_store_paths.extend(cleanup_written)
                if cleanup_written:
                    user_before = deepcopy(user_store)
                    project_before = deepcopy(project_store)

        snapshot = _ops._build_memory_snapshot(user_store, project_store)

        system_content = (
            _SYSTEM_PROMPT
            + f"\ncurrent_date: {_ops._iso_now()[:10]}\n"
            + "memory_snapshot:\n"
            + json.dumps(snapshot, ensure_ascii=False, indent=2)
        )
        call_messages: list[Any] = [SystemMessage(content=system_content)]
        call_messages.append(
            HumanMessage(
                content=_ops._format_messages_for_memory_transcript(list(messages))
            )
        )
        call_messages.append(
            HumanMessage(
                content=_FINAL_INSTRUCTION_TEMPLATE.format(
                    explicit_memory_request=str(explicit_memory_request).lower(),
                    target_language=target_language,
                )
            )
        )

        logger.debug(
            "Memory agent input (%d messages):\n%s",
            len(call_messages),
            _ops._format_call_messages_for_log(call_messages),
        )

        try:
            response = await model.bind(max_tokens=_ops._MAX_OUTPUT_TOKENS).ainvoke(
                call_messages,
                config={"metadata": {"lc_source": "memory_agent"}},
            )
        except Exception:
            logger.warning("Memory agent model call failed", exc_info=True)
            if written_store_paths:
                middleware._last_run_turn = middleware._turn_index
                middleware._last_run_at = time.monotonic()
                return list(dict.fromkeys(written_store_paths))
            return None

        raw: str = response.content
        if isinstance(raw, list):
            raw = " ".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in raw
            )
        raw = raw.lstrip()
        logger.debug("Memory agent output: %s", raw)
        data: Any = {"operations": []}
        fence_match = re.search(r"```(?:json)?\s*(\{)", raw, re.DOTALL)
        start = fence_match.start(1) if fence_match else raw.find("{")
        if start == -1:
            logger.debug(
                "Memory agent: model response has no JSON object preview=%r",
                raw[:200],
            )
        else:
            try:
                data, _ = json.JSONDecoder().raw_decode(raw, start)
            except json.JSONDecodeError:
                logger.debug(
                    "Memory agent: model returned malformed JSON preview=%r",
                    raw[start : start + 200],
                    exc_info=True,
                )
        operations = _ops._normalize_and_validate_operations(data)
        if not operations:
            if written_store_paths:
                middleware._last_run_turn = middleware._turn_index
                middleware._last_run_at = time.monotonic()
            return list(dict.fromkeys(written_store_paths))

        now_iso = _ops._iso_now()
        (
            new_user,
            new_project,
            model_written,
        ) = await middleware._apply_and_write_memory_operations(
            user_store,
            project_store,
            user_before,
            project_before,
            operations,
            thread_id=thread_id,
            source_anchor=source_anchor,
            now_iso=now_iso,
        )
        del new_user, new_project
        written_store_paths.extend(model_written)

        if written_store_paths:
            middleware._last_run_turn = middleware._turn_index
            middleware._last_run_at = time.monotonic()
        return list(dict.fromkeys(written_store_paths))

    except json.JSONDecodeError:
        logger.debug("Memory agent: model returned malformed JSON", exc_info=True)
        return []
    except Exception:
        logger.warning("Memory agent extraction failed unexpectedly", exc_info=True)
        return None
