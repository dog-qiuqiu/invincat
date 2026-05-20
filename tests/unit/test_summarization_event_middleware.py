from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

from invincat_cli.middleware.summarization_event import (
    SummarizationEventNormalizerMiddleware,
)


@dataclass
class Request:
    state: dict[str, Any]

    def override(self, **overrides: Any) -> Request:
        return Request(state=overrides.get("state", self.state))


def _serialized_event() -> dict[str, Any]:
    return {
        "cutoff_index": 3,
        "summary_message": {
            "content": "You are in the middle of a summarized conversation.",
            "additional_kwargs": {"lc_source": "summarization"},
            "response_metadata": {},
            "type": "human",
            "name": None,
            "id": None,
        },
        "file_path": "/tmp/history.md",
    }


def test_summarization_event_normalizer_converts_serialized_summary_message() -> None:
    middleware = SummarizationEventNormalizerMiddleware()
    request = Request(state={"_summarization_event": _serialized_event()})
    seen: list[Request] = []

    def handler(next_request: Request) -> str:
        seen.append(next_request)
        return "ok"

    assert middleware.wrap_model_call(request, handler) == "ok"

    event = seen[0].state["_summarization_event"]
    summary = event["summary_message"]
    assert isinstance(summary, HumanMessage)
    assert summary.content == "You are in the middle of a summarized conversation."
    assert summary.additional_kwargs == {"lc_source": "summarization"}
    assert request.state["_summarization_event"]["summary_message"]["type"] == "human"


def test_summarization_event_normalizer_leaves_message_objects_unchanged() -> None:
    middleware = SummarizationEventNormalizerMiddleware()
    summary = HumanMessage(
        content="summary",
        additional_kwargs={"lc_source": "summarization"},
    )
    request = Request(
        state={
            "_summarization_event": {
                "cutoff_index": 1,
                "summary_message": summary,
                "file_path": None,
            }
        }
    )
    seen: list[Request] = []

    def handler(next_request: Request) -> str:
        seen.append(next_request)
        return "ok"

    assert middleware.wrap_model_call(request, handler) == "ok"
    assert seen[0] is request


def test_summarization_event_normalizer_async() -> None:
    middleware = SummarizationEventNormalizerMiddleware()
    request = Request(state={"_summarization_event": _serialized_event()})
    seen: list[Request] = []

    async def handler(next_request: Request) -> str:
        seen.append(next_request)
        return "ok"

    assert asyncio.run(middleware.awrap_model_call(request, handler)) == "ok"
    assert isinstance(
        seen[0].state["_summarization_event"]["summary_message"],
        HumanMessage,
    )
