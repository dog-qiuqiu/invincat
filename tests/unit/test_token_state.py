"""Tests for token state middleware schema registration."""

from invincat_cli.token_state import TokenStateMiddleware, TokenTrackingState


def test_token_state_middleware_registers_private_state_schema() -> None:
    assert TokenStateMiddleware.state_schema is TokenTrackingState
    assert "_context_tokens" in TokenTrackingState.__annotations__
