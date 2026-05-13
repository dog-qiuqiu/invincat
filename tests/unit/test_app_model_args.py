"""Tests for `/model` argument parsing helpers."""

from __future__ import annotations

import pytest

from invincat_cli.app_model_args import (
    extract_model_params_flag,
    parse_model_target,
    split_model_spec,
)


def test_extract_model_params_flag_with_bare_json() -> None:
    remaining, params = extract_model_params_flag(
        '2 openai:gpt-4 --model-params {"temperature": 0.2, "max_tokens": 100}'
    )

    assert remaining == "2 openai:gpt-4"
    assert params == {"temperature": 0.2, "max_tokens": 100}


def test_extract_model_params_flag_with_quoted_json_and_trailing_args() -> None:
    remaining, params = extract_model_params_flag(
        "--default --model-params '{\"temperature\": 0}' openai:gpt-4"
    )

    assert remaining == "--default openai:gpt-4"
    assert params == {"temperature": 0}


def test_extract_model_params_flag_rejects_non_object() -> None:
    with pytest.raises(TypeError):
        extract_model_params_flag("--model-params []")


def test_parse_model_target() -> None:
    assert parse_model_target("2 openai:gpt-4") == ("memory", "openai:gpt-4")
    assert parse_model_target("primary anthropic:claude") == (
        "primary",
        "anthropic:claude",
    )
    assert parse_model_target("openai:gpt-4") == ("primary", "openai:gpt-4")


def test_split_model_spec() -> None:
    assert split_model_spec("openai:gpt-4") == ("openai", "gpt-4")
    assert split_model_spec("gpt-4") == ("", "gpt-4")
    assert split_model_spec(None) == ("", "")
