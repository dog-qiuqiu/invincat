"""Normalize serialized summarization events before model calls."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import convert_to_messages

logger = logging.getLogger(__name__)


class SummarizationEventNormalizerMiddleware(AgentMiddleware):
    """Convert checkpoint-serialized summary messages back to message objects.

    LangGraph checkpointers can deserialize custom state fields as plain dicts.
    DeepAgents' summarization middleware expects
    ``_summarization_event["summary_message"]`` to be a LangChain message object,
    so normalize that event before the summarization middleware rebuilds the
    effective message list.
    """

    @staticmethod
    def _normalize_event(event: object) -> dict[str, Any] | None:
        if not isinstance(event, dict):
            return None

        summary_msg = event.get("summary_message")
        if not isinstance(summary_msg, dict):
            return None

        try:
            converted = convert_to_messages([summary_msg])
        except Exception:
            logger.warning(
                "Failed to normalize serialized summarization event",
                exc_info=True,
            )
            return None

        if not converted or not isinstance(converted[0], BaseMessage):
            logger.warning("Serialized summarization event did not convert to a message")
            return None

        return {**event, "summary_message": converted[0]}

    def _apply(self, request: Any) -> Any | None:
        event = request.state.get("_summarization_event")
        normalized = self._normalize_event(event)
        if normalized is None:
            return None

        state = dict(request.state)
        state["_summarization_event"] = normalized
        return request.override(state=state)

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        modified = self._apply(request)
        return handler(modified if modified is not None else request)

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        modified = self._apply(request)
        return await handler(modified if modified is not None else request)
