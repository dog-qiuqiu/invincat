from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from langchain_core.messages import HumanMessage

from invincat_cli.models.testing import DeterministicIntegrationChatModel


def _content_for(messages: list[HumanMessage]) -> str:
    result = DeterministicIntegrationChatModel()._generate(messages)
    return str(result.generations[0].message.content)


def test_deterministic_integration_chat_model_metadata_and_bind_tools() -> None:
    model = DeterministicIntegrationChatModel()

    assert model.model == "fake"
    assert model.profile == {"tool_calling": True, "max_input_tokens": 8000}
    assert model._llm_type == "deterministic-integration"
    assert model.bind_tools([]) is model


def test_deterministic_integration_chat_model_generates_prompt_reply() -> None:
    content = _content_for(
        [
            HumanMessage(content="first"),
            HumanMessage(content=[{"type": "text", "text": "second"}, "third"]),
            HumanMessage(content=[{"type": "image_url", "image_url": "ignored"}]),
        ]
    )

    assert content == "integration reply: first second third"
    assert _content_for([]) == "integration reply"


def test_deterministic_integration_chat_model_detects_summary_requests() -> None:
    assert (
        _content_for([HumanMessage(content="Messages to summarize:\nhello")])
        == "integration summary"
    )
    assert (
        _content_for([HumanMessage(content="Condense the following conversation")])
        == "integration summary"
    )
    assert _content_for([HumanMessage(content="<summary>old</summary>")]) == (
        "integration summary"
    )


def test_deterministic_integration_chat_model_stringifies_nonstandard_content() -> None:
    message: Any = SimpleNamespace(content=123)

    assert DeterministicIntegrationChatModel._stringify_message(message) == "123"
