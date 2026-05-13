"""Tests for `/model` command parsing."""

from __future__ import annotations

from invincat_cli.app_runtime.model_command import (
    MODEL_DEFAULT_USAGE,
    ModelCommandAction,
    parse_model_command,
)


def test_parse_model_command_opens_primary_selector_by_default() -> None:
    assert parse_model_command("/model") == ModelCommandAction(kind="selector")


def test_parse_model_command_opens_memory_selector() -> None:
    assert parse_model_command("/model memory") == ModelCommandAction(
        kind="selector",
        target="memory",
    )


def test_parse_model_command_direct_switch_with_params() -> None:
    assert parse_model_command(
        "/model 2 openai:gpt-test --model-params '{\"temperature\": 0.2}'"
    ) == ModelCommandAction(
        kind="switch",
        target="memory",
        model_arg="openai:gpt-test",
        extra_kwargs={"temperature": 0.2},
    )


def test_parse_model_command_default_actions() -> None:
    assert parse_model_command("/model --default openai:gpt-test") == (
        ModelCommandAction(
            kind="set_default",
            model_arg="openai:gpt-test",
        )
    )
    assert parse_model_command("/model memory --default --clear") == (
        ModelCommandAction(kind="clear_default", target="memory")
    )
    assert parse_model_command("/model --default") == ModelCommandAction(kind="usage")
    assert "provider:model" in MODEL_DEFAULT_USAGE


def test_parse_model_command_rejects_params_with_default() -> None:
    action = parse_model_command(
        "/model --default openai:gpt-test --model-params '{\"temperature\": 0.2}'"
    )

    assert action.kind == "error"
    assert "--model-params cannot be used with --default" in (action.error or "")


def test_parse_model_command_reports_invalid_params() -> None:
    action = parse_model_command("/model --model-params '{bad'")

    assert action.kind == "error"
    assert "Invalid JSON" in (action.error or "")
