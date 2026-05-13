"""Tests for model switching helpers."""

from __future__ import annotations

from invincat_cli.app_runtime.model_runtime import (
    already_using_model_display,
    can_start_deferred_server_for_model_switch,
    choose_default_model_clear_fn,
    choose_default_model_save_fn,
    current_model_display,
    is_target_already_using,
    missing_credentials_detail,
    model_status_fields,
    model_switch_requires_server_error,
    model_switch_target_kwargs,
    model_target_translation_key,
    normalize_default_model_spec,
    resolve_model_spec,
    should_primary_switch_update_memory_status,
    should_start_server_after_primary_model_switch,
)


def test_resolve_model_spec_keeps_explicit_provider() -> None:
    resolved = resolve_model_spec(
        "anthropic:claude-test",
        detect_provider=lambda _spec: "openai",
    )

    assert resolved.raw == "anthropic:claude-test"
    assert resolved.provider == "anthropic"
    assert resolved.model_name == "claude-test"
    assert resolved.display == "anthropic:claude-test"
    assert resolved.parsed is True


def test_resolve_model_spec_uses_detected_provider_for_bare_model() -> None:
    resolved = resolve_model_spec(
        ":gpt-test",
        detect_provider=lambda _spec: "openai",
    )

    assert resolved.raw == "gpt-test"
    assert resolved.provider == "openai"
    assert resolved.model_name == "gpt-test"
    assert resolved.display == "openai:gpt-test"
    assert resolved.parsed is False


def test_normalize_default_model_spec() -> None:
    assert normalize_default_model_spec(
        ":anthropic:claude-test",
        detect_provider=lambda _spec: "openai",
    ) == "anthropic:claude-test"
    assert normalize_default_model_spec(
        "gpt-test",
        detect_provider=lambda _spec: "openai",
    ) == "openai:gpt-test"
    assert normalize_default_model_spec(
        "custom-model",
        detect_provider=lambda _spec: None,
    ) == "custom-model"


def test_missing_credentials_detail() -> None:
    assert missing_credentials_detail(
        "openai",
        get_credential_env_var=lambda _provider: "OPENAI_API_KEY",
    ) == "OPENAI_API_KEY is not set or is empty"
    assert "provider 'unknown' is not recognized" in missing_credentials_detail(
        "unknown",
        get_credential_env_var=lambda _provider: None,
    )


def test_current_model_display() -> None:
    assert current_model_display("openai", "gpt-test") == "openai:gpt-test"
    assert current_model_display(None, "gpt-test") is None
    assert current_model_display("openai", None) is None


def test_is_target_already_using_primary() -> None:
    resolved = resolve_model_spec(
        "gpt-test",
        detect_provider=lambda _spec: "openai",
    )

    assert is_target_already_using(
        target="primary",
        resolved=resolved,
        current_provider="openai",
        current_model_name="gpt-test",
        memory_model_override=None,
    )
    assert not is_target_already_using(
        target="primary",
        resolved=resolved,
        current_provider="anthropic",
        current_model_name="claude-test",
        memory_model_override=None,
    )


def test_is_target_already_using_memory() -> None:
    resolved = resolve_model_spec(
        "openai:gpt-test",
        detect_provider=lambda _spec: "openai",
    )

    assert is_target_already_using(
        target="memory",
        resolved=resolved,
        current_provider="anthropic",
        current_model_name="claude-test",
        memory_model_override="openai:gpt-test",
    )
    assert not is_target_already_using(
        target="memory",
        resolved=resolved,
        current_provider="anthropic",
        current_model_name="claude-test",
        memory_model_override=None,
    )


def test_model_switch_target_kwargs_prefers_explicit_values() -> None:
    assert model_switch_target_kwargs(
        extra_kwargs={"temperature": 0.2},
        saved_kwargs={"temperature": 0.8},
    ) == {"temperature": 0.2}
    assert model_switch_target_kwargs(
        extra_kwargs=None,
        saved_kwargs={"temperature": 0.8},
    ) == {"temperature": 0.8}
    assert model_switch_target_kwargs(extra_kwargs=None, saved_kwargs={}) is None
    assert model_switch_target_kwargs(extra_kwargs=None, saved_kwargs=None) is None


def test_can_start_deferred_server_for_model_switch() -> None:
    assert can_start_deferred_server_for_model_switch(
        target="primary",
        has_server_kwargs=True,
        connecting=False,
    )
    assert not can_start_deferred_server_for_model_switch(
        target="memory",
        has_server_kwargs=True,
        connecting=False,
    )
    assert not can_start_deferred_server_for_model_switch(
        target="primary",
        has_server_kwargs=False,
        connecting=False,
    )
    assert not can_start_deferred_server_for_model_switch(
        target="primary",
        has_server_kwargs=True,
        connecting=True,
    )


def test_model_switch_requires_server_error() -> None:
    assert model_switch_requires_server_error(
        has_remote_agent=False,
        can_start_deferred_server=False,
    )
    assert not model_switch_requires_server_error(
        has_remote_agent=True,
        can_start_deferred_server=False,
    )
    assert not model_switch_requires_server_error(
        has_remote_agent=False,
        can_start_deferred_server=True,
    )


def test_already_using_model_display() -> None:
    resolved = resolve_model_spec(
        "gpt-test",
        detect_provider=lambda _spec: None,
    )

    assert (
        already_using_model_display(
            target="primary",
            resolved=resolved,
            current_provider="openai",
            current_model_name="gpt-test",
        )
        == "openai:gpt-test"
    )
    assert (
        already_using_model_display(
            target="primary",
            resolved=resolved,
            current_provider=None,
            current_model_name="gpt-test",
        )
        == "gpt-test"
    )
    assert (
        already_using_model_display(
            target="memory",
            resolved=resolved,
            current_provider="openai",
            current_model_name="gpt-test",
        )
        == "gpt-test"
    )


def test_model_status_fields() -> None:
    fields = model_status_fields(provider="openai", model_name="gpt-test")
    assert fields.provider == "openai"
    assert fields.model == "gpt-test"

    missing = model_status_fields(provider=None, model_name=None)
    assert missing.provider == ""
    assert missing.model == ""


def test_should_primary_switch_update_memory_status() -> None:
    assert should_primary_switch_update_memory_status(memory_model_override=None)
    assert not should_primary_switch_update_memory_status(
        memory_model_override="anthropic:claude",
    )


def test_should_start_server_after_primary_model_switch() -> None:
    assert should_start_server_after_primary_model_switch(
        has_remote_agent=False,
        has_server_kwargs=True,
    )
    assert not should_start_server_after_primary_model_switch(
        has_remote_agent=True,
        has_server_kwargs=True,
    )
    assert not should_start_server_after_primary_model_switch(
        has_remote_agent=False,
        has_server_kwargs=False,
    )


def test_model_target_translation_key() -> None:
    assert model_target_translation_key("primary") == "model.target_primary"
    assert model_target_translation_key("memory") == "model.target_memory"


def test_choose_default_model_save_fn() -> None:
    def save_primary(_spec: str) -> bool:
        return True

    def save_memory(_spec: str) -> bool:
        return False

    assert (
        choose_default_model_save_fn(
            "primary",
            save_default_model=save_primary,
            save_memory_default_model=save_memory,
        )
        is save_primary
    )
    assert (
        choose_default_model_save_fn(
            "memory",
            save_default_model=save_primary,
            save_memory_default_model=save_memory,
        )
        is save_memory
    )


def test_choose_default_model_clear_fn() -> None:
    def clear_primary() -> bool:
        return True

    def clear_memory() -> bool:
        return False

    assert (
        choose_default_model_clear_fn(
            "primary",
            clear_default_model=clear_primary,
            clear_memory_default_model=clear_memory,
        )
        is clear_primary
    )
    assert (
        choose_default_model_clear_fn(
            "memory",
            clear_default_model=clear_primary,
            clear_memory_default_model=clear_memory,
        )
        is clear_memory
    )
