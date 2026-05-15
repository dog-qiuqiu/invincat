"""Runtime decision helpers for MemoryAgentMiddleware."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from invincat_cli.memory import store_ops as _ops

logger = logging.getLogger(__name__)


def should_run_for_turn(middleware: Any, messages: list[Any]) -> bool:
    """Return whether the memory extraction pass should run for this turn."""
    middleware._turn_index += 1
    turns_since_last = middleware._turn_index - middleware._last_run_turn
    interval_due = turns_since_last >= middleware._min_turn_interval
    human_text = middleware._last_human_text(messages)
    signal_match = bool(_ops._MEMORY_SIGNAL_RE.search(human_text))

    if middleware._min_seconds_between_runs > 0:
        elapsed = time.monotonic() - middleware._last_run_at
        if elapsed < middleware._min_seconds_between_runs and not signal_match:
            logger.debug(
                "Memory agent: throttled by wall-clock cooldown (%.2fs < %.2fs)",
                elapsed,
                middleware._min_seconds_between_runs,
            )
            return False

    if middleware._memory_files_recently_updated() and not signal_match:
        logger.debug("Memory agent: throttled by file-update cooldown")
        return False

    if interval_due or signal_match:
        return True

    logger.debug(
        "Memory agent: throttled by turn interval (%d < %d)",
        turns_since_last,
        middleware._min_turn_interval,
    )
    return False


def resolve_memory_model(middleware: Any, runtime: Any, fallback_model: Any) -> Any:
    """Resolve dedicated memory model override from runtime context."""
    ctx = getattr(runtime, "context", None)
    if not isinstance(ctx, dict):
        return fallback_model

    raw_spec = ctx.get("memory_model")
    if not isinstance(raw_spec, str) or not raw_spec.strip():
        return fallback_model
    memory_spec = raw_spec.strip()

    raw_params = ctx.get("memory_model_params", {})
    memory_params = raw_params if isinstance(raw_params, dict) else {}
    try:
        params_key = json.dumps(memory_params, sort_keys=True, ensure_ascii=True)
    except (TypeError, ValueError):
        params_key = "{}"
    cache_key = (memory_spec, params_key)

    if (
        cache_key == middleware._memory_model_cache_key
        and middleware._memory_model_cache_obj is not None
    ):
        return middleware._memory_model_cache_obj

    try:
        from invincat_cli.config import create_model

        model_result = create_model(memory_spec, extra_kwargs=memory_params)
        middleware._memory_model_cache_key = cache_key
        middleware._memory_model_cache_obj = model_result.model
        return model_result.model
    except Exception:
        logger.warning(
            "Memory agent: failed to resolve dedicated memory model '%s'; "
            "falling back to primary model",
            memory_spec,
            exc_info=True,
        )
        return fallback_model
