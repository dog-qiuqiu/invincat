"""Model switching helpers for the Textual app."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from invincat_cli.model_config import ModelSpec, ModelTarget


@dataclass(frozen=True, slots=True)
class ResolvedModelSpec:
    """Normalized model spec details used by app model switching."""

    raw: str
    provider: str | None
    model_name: str
    display: str
    parsed: bool


def resolve_model_spec(
    model_spec: str,
    *,
    detect_provider: Callable[[str], str | None],
) -> ResolvedModelSpec:
    """Resolve user input into provider/model/display fields."""
    raw = model_spec.removeprefix(":")
    parsed = ModelSpec.try_parse(raw)
    if parsed:
        return ResolvedModelSpec(
            raw=raw,
            provider=parsed.provider,
            model_name=parsed.model,
            display=raw,
            parsed=True,
        )

    provider = detect_provider(raw)
    display = f"{provider}:{raw}" if provider else raw
    return ResolvedModelSpec(
        raw=raw,
        provider=provider,
        model_name=raw,
        display=display,
        parsed=False,
    )


def normalize_default_model_spec(
    model_spec: str,
    *,
    detect_provider: Callable[[str], str | None],
) -> str:
    """Normalize a model spec before persisting as default."""
    raw = model_spec.removeprefix(":")
    if ModelSpec.try_parse(raw):
        return raw
    provider = detect_provider(raw)
    return f"{provider}:{raw}" if provider else raw


def missing_credentials_detail(
    provider: str,
    *,
    get_credential_env_var: Callable[[str], str | None],
) -> str:
    """Build the user-facing detail for missing provider credentials."""
    env_var = get_credential_env_var(provider)
    if env_var:
        return f"{env_var} is not set or is empty"
    return (
        f"provider '{provider}' is not recognized. "
        "Add it to ~/.invincat/config.toml with an api_key_env field"
    )


def current_model_display(provider: str | None, model_name: str | None) -> str | None:
    """Return provider:model display for current settings when complete."""
    if provider and model_name:
        return f"{provider}:{model_name}"
    return None


def is_target_already_using(
    *,
    target: ModelTarget,
    resolved: ResolvedModelSpec,
    current_provider: str | None,
    current_model_name: str | None,
    memory_model_override: str | None,
) -> bool:
    """Return whether the target is already using the resolved model."""
    if target == "primary":
        return bool(
            current_model_name
            and resolved.model_name == current_model_name
            and (not resolved.provider or resolved.provider == current_provider)
        )

    current_memory = memory_model_override or current_model_display(
        current_provider,
        current_model_name,
    )
    return resolved.display == current_memory


def model_switch_target_kwargs(
    *,
    extra_kwargs: dict[str, Any] | None,
    saved_kwargs: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Choose explicit model kwargs, falling back to saved target defaults."""
    if extra_kwargs is not None:
        return extra_kwargs
    return saved_kwargs or None


def can_start_deferred_server_for_model_switch(
    *,
    target: ModelTarget,
    has_server_kwargs: bool,
    connecting: bool,
) -> bool:
    """Return whether a primary model switch may start a deferred server."""
    return target == "primary" and has_server_kwargs and not connecting


def model_switch_requires_server_error(
    *,
    has_remote_agent: bool,
    can_start_deferred_server: bool,
) -> bool:
    """Return whether model switching should fail due to missing server state."""
    return not has_remote_agent and not can_start_deferred_server


def already_using_model_display(
    *,
    target: ModelTarget,
    resolved: ResolvedModelSpec,
    current_provider: str | None,
    current_model_name: str | None,
) -> str:
    """Return display text for an already-selected model."""
    if target == "primary":
        return (
            current_model_display(current_provider, current_model_name)
            or current_model_name
            or resolved.display
        )
    return resolved.display


def model_target_translation_key(target: ModelTarget) -> str:
    """Return i18n key for a model target label."""
    return "model.target_memory" if target == "memory" else "model.target_primary"


def choose_default_model_save_fn(
    target: ModelTarget,
    *,
    save_default_model: Callable[[str], bool],
    save_memory_default_model: Callable[[str], bool],
) -> Callable[[str], bool]:
    """Choose the config writer for a default model target."""
    return save_memory_default_model if target == "memory" else save_default_model


def choose_default_model_clear_fn(
    target: ModelTarget,
    *,
    clear_default_model: Callable[[], bool],
    clear_memory_default_model: Callable[[], bool],
) -> Callable[[], bool]:
    """Choose the config clearer for a default model target."""
    return clear_memory_default_model if target == "memory" else clear_default_model
