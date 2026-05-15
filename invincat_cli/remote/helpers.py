"""Small helpers shared by the remote agent client."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def require_thread_id(config: dict[str, Any] | None) -> str:
    """Extract and validate that `thread_id` is present in config."""
    thread_id = (config or {}).get("configurable", {}).get("thread_id")
    if not thread_id:
        msg = "thread_id is required in config.configurable"
        raise ValueError(msg)
    return thread_id


def prepare_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow-copy config so callers' dicts are not mutated."""
    config = dict(config or {})
    configurable = dict(config.get("configurable", {}))
    config["configurable"] = configurable
    return config


def convert_interrupts(raw: Any) -> list[Any]:  # noqa: ANN401
    """Convert interrupt dicts from the server into Interrupt objects."""
    from langgraph.types import Interrupt

    if not isinstance(raw, list):
        logger.warning(
            "Expected list for __interrupt__ data, got %s",
            type(raw).__name__,
        )
        return [raw] if raw is not None else []
    results = []
    for item in raw:
        if isinstance(item, Interrupt):
            results.append(item)
        elif isinstance(item, dict) and "value" in item:
            results.append(Interrupt(value=item["value"], id=item.get("id", "")))
        else:
            results.append(item)
    return results
