"""Tests for `/tokens` response formatting."""

from __future__ import annotations

from invincat_cli.app_tokens import build_tokens_message
from invincat_cli.i18n import Language, set_language


def test_build_tokens_message_without_usage() -> None:
    set_language(Language.EN)

    assert build_tokens_message(
        context_tokens=0,
        model_name="gpt-test",
        context_limit=200_000,
    ) == "No token usage yet · 200.0K token context window · gpt-test"


def test_build_tokens_message_with_limit_and_breakdown() -> None:
    set_language(Language.EN)

    assert build_tokens_message(
        context_tokens=50_000,
        model_name="gpt-test",
        context_limit=200_000,
        conversation_tokens=30_000,
    ) == (
        "50.0K / 200.0K tokens (25%) · gpt-test\n"
        "├ System prompt + tools: ~20.0K (fixed)\n"
        "└ Conversation: ~30.0K"
    )


def test_build_tokens_message_without_context_limit() -> None:
    set_language(Language.EN)

    assert build_tokens_message(
        context_tokens=300,
        model_name="",
        context_limit=None,
        conversation_tokens=100,
    ) == (
        "300 tokens used\n"
        "├ System prompt + tools: ~200 tokens (fixed)\n"
        "└ Conversation: ~100 tokens"
    )
