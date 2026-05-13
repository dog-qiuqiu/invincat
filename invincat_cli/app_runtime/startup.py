"""Startup runtime helpers for the Textual app."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from invincat_cli.app_runtime.state import TextualSessionState
from invincat_cli.skills.load import ExtendedSkillMetadata

StartupFollowupKind = Literal["submit_prompt", "load_history"]


@dataclass(frozen=True, slots=True)
class StartupModelOverrides:
    """Model override values resolved during TUI mount."""

    memory_model: str | None
    primary_params: dict[str, Any] | None
    memory_params: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class StartupMemoryStatusModel:
    """Status-bar memory model display data."""

    provider: str
    model: str
    follow_primary: bool


@dataclass(frozen=True, slots=True)
class StartupFollowup:
    """Action to schedule after post-paint startup work."""

    kind: StartupFollowupKind
    prompt: str | None = None


def resolve_startup_model_overrides(
    *,
    memory_model_override: str | None,
    memory_model_params_override: dict[str, Any] | None,
    model_params_override: dict[str, Any] | None,
    model_provider: str | None,
    model_name: str | None,
    get_default_memory_model_spec: Callable[[], str | None],
    get_target_model_params: Callable[[str, str], dict[str, Any]],
) -> StartupModelOverrides:
    """Resolve model/profile overrides used by the TUI at mount time."""
    memory_model = memory_model_override
    if memory_model is None:
        default_memory_model = get_default_memory_model_spec()
        if default_memory_model:
            memory_model = default_memory_model

    primary_params = model_params_override
    if primary_params is None and model_provider and model_name:
        resolved = get_target_model_params("primary", f"{model_provider}:{model_name}")
        if resolved:
            primary_params = resolved

    memory_params = memory_model_params_override
    if memory_model is not None and memory_params is None:
        resolved = get_target_model_params("memory", memory_model)
        if resolved:
            memory_params = resolved

    return StartupModelOverrides(
        memory_model=memory_model,
        primary_params=primary_params,
        memory_params=memory_params,
    )


def resolve_memory_status_model(
    *,
    memory_model_override: str | None,
    model_provider: str | None,
    model_name: str | None,
    split_model_spec: Callable[[str], tuple[str, str]],
) -> StartupMemoryStatusModel:
    """Resolve memory model display state for the status bar."""
    if memory_model_override:
        provider, model = split_model_spec(memory_model_override)
        return StartupMemoryStatusModel(
            provider=provider,
            model=model,
            follow_primary=False,
        )
    return StartupMemoryStatusModel(
        provider=model_provider or "",
        model=model_name or "",
        follow_primary=True,
    )


def build_startup_slash_commands(
    *,
    commands: Sequence[Any],
    discovered_skills: list[ExtendedSkillMetadata],
    build_skill_commands: Callable[
        [list[ExtendedSkillMetadata]], list[tuple[str, str, str]]
    ],
) -> list[tuple[str, str, str]]:
    """Build slash-command autocomplete entries for initial mount."""
    slash_commands = [
        (cmd.name, cmd.description, cmd.hidden_keywords) for cmd in commands
    ]
    if discovered_skills:
        slash_commands.extend(build_skill_commands(discovered_skills))
    return slash_commands


def create_startup_session_state(
    *,
    auto_approve: bool,
    thread_id: str,
) -> TextualSessionState:
    """Create the initial Textual session state."""
    return TextualSessionState(auto_approve=auto_approve, thread_id=thread_id)


def resolve_startup_followup(
    *,
    connecting: bool,
    initial_prompt: str | None,
    thread_id: str | None,
    agent: object | None,
) -> StartupFollowup | None:
    """Return the post-paint followup action to schedule, if any."""
    if connecting:
        return None
    if initial_prompt and initial_prompt.strip():
        return StartupFollowup(kind="submit_prompt", prompt=initial_prompt)
    if thread_id and agent is not None:
        return StartupFollowup(kind="load_history")
    return None
