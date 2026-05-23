"""Normalize summarized conversation state before model calls."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import convert_to_messages

logger = logging.getLogger(__name__)


class SummarizationEventNormalizerMiddleware(AgentMiddleware):
    """Convert checkpoint-serialized summary messages before model calls.

    LangGraph checkpointers can deserialize custom state fields as plain dicts.
    DeepAgents' summarization middleware expects
    ``_summarization_event["summary_message"]`` to be a LangChain message object,
    so normalize that event before it rebuilds the effective message list.

    Subagents also receive a shallow copy of parent runtime state from
    DeepAgents' task tool. If a parent summarization event leaks into a child
    state, its cutoff index is usually beyond the child's short message list.
    Clear that stale event so the child starts from its own task prompt.
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

    @staticmethod
    def _is_stale_event(event: object, state: dict[str, Any]) -> bool:
        if not isinstance(event, dict):
            return False

        cutoff = event.get("cutoff_index")
        messages = state.get("messages")
        if not isinstance(cutoff, int) or not isinstance(messages, list):
            return False

        return cutoff < 0 or cutoff > len(messages)

    def _state_update(self, state: dict[str, Any]) -> dict[str, Any] | None:
        event = state.get("_summarization_event")
        if self._is_stale_event(event, state):
            return {"_summarization_event": None}

        normalized = self._normalize_event(event)
        if normalized is None:
            return None

        return {"_summarization_event": normalized}

    def before_model(
        self,
        state: dict[str, Any],
        runtime: Any,  # noqa: ANN401, ARG002
    ) -> dict[str, Any] | None:
        return self._state_update(state)

    async def abefore_model(
        self,
        state: dict[str, Any],
        runtime: Any,  # noqa: ANN401, ARG002
    ) -> dict[str, Any] | None:
        return self._state_update(state)

    def _apply(self, request: Any) -> Any | None:
        update = self._state_update(request.state)
        if update is None:
            return None

        state = dict(request.state)
        state.update(update)
        return request.override(state=state)

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        modified = self._apply(request)
        return handler(modified if modified is not None else request)

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        modified = self._apply(request)
        return await handler(modified if modified is not None else request)
