"""Tests for DeepSeek OpenAI-compatible model behavior."""

from __future__ import annotations

from unittest.mock import patch

from invincat_cli.models.deepseek_chat_openai import DeepSeekChatOpenAICompat


def test_deepseek_payload_strips_reasoning_effort_when_thinking_disabled() -> None:
    with patch(
        "langchain_openai.ChatOpenAI._get_request_payload",
        return_value={
            "messages": [],
            "reasoning_effort": "medium",
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    ):
        model = DeepSeekChatOpenAICompat(model="deepseek-chat", api_key="test")

        payload = model._get_request_payload([])

    assert payload["extra_body"]["thinking"]["type"] == "disabled"
    assert "reasoning_effort" not in payload


def test_deepseek_payload_keeps_reasoning_effort_when_thinking_enabled() -> None:
    with patch(
        "langchain_openai.ChatOpenAI._get_request_payload",
        return_value={
            "messages": [],
            "reasoning_effort": "high",
            "extra_body": {"thinking": {"type": "enabled"}},
        },
    ):
        model = DeepSeekChatOpenAICompat(model="deepseek-chat", api_key="test")

        payload = model._get_request_payload([])

    assert payload["reasoning_effort"] == "high"
