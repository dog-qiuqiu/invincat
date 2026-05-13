"""Tests for startup runtime helpers used by the Textual app."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from invincat_cli.app_runtime.startup import (
    build_startup_slash_commands,
    create_startup_session_state,
    resolve_memory_status_model,
    resolve_startup_followup,
    resolve_startup_model_overrides,
)
from invincat_cli.skills.load import ExtendedSkillMetadata


@dataclass(frozen=True)
class _Command:
    name: str
    description: str
    hidden_keywords: str


def test_resolve_startup_model_overrides_uses_defaults_and_profiles() -> None:
    def _params(target: str, spec: str) -> dict[str, object]:
        return {"target": target, "spec": spec}

    result = resolve_startup_model_overrides(
        memory_model_override=None,
        memory_model_params_override=None,
        model_params_override=None,
        model_provider="openai",
        model_name="gpt-test",
        get_default_memory_model_spec=lambda: "openai:gpt-memory",
        get_target_model_params=_params,
    )

    assert result.memory_model == "openai:gpt-memory"
    assert result.primary_params == {
        "target": "primary",
        "spec": "openai:gpt-test",
    }
    assert result.memory_params == {
        "target": "memory",
        "spec": "openai:gpt-memory",
    }


def test_resolve_startup_model_overrides_preserves_existing_values() -> None:
    result = resolve_startup_model_overrides(
        memory_model_override="anthropic:mem",
        memory_model_params_override={"existing": "memory"},
        model_params_override={"existing": "primary"},
        model_provider="openai",
        model_name="gpt-test",
        get_default_memory_model_spec=lambda: "openai:gpt-memory",
        get_target_model_params=lambda _target, _spec: {"unexpected": True},
    )

    assert result.memory_model == "anthropic:mem"
    assert result.primary_params == {"existing": "primary"}
    assert result.memory_params == {"existing": "memory"}


def test_resolve_memory_status_model() -> None:
    assert resolve_memory_status_model(
        memory_model_override="openai:gpt-memory",
        model_provider="anthropic",
        model_name="claude",
        split_model_spec=lambda spec: tuple(spec.split(":", 1)),  # type: ignore[return-value]
    ).follow_primary is False

    primary = resolve_memory_status_model(
        memory_model_override=None,
        model_provider="anthropic",
        model_name="claude",
        split_model_spec=lambda spec: tuple(spec.split(":", 1)),  # type: ignore[return-value]
    )
    assert primary.provider == "anthropic"
    assert primary.model == "claude"
    assert primary.follow_primary is True


def test_build_startup_slash_commands_merges_skills() -> None:
    skill = cast(
        ExtendedSkillMetadata,
        {"name": "skill-a", "description": "Skill A", "source": "project"},
    )

    assert build_startup_slash_commands(
        commands=[_Command("/help", "Help", "")],
        discovered_skills=[skill],
        build_skill_commands=lambda _skills: [("/skill:skill-a", "Skill A", "")],
    ) == [("/help", "Help", ""), ("/skill:skill-a", "Skill A", "")]


def test_create_startup_session_state() -> None:
    state = create_startup_session_state(auto_approve=True, thread_id="thread-1")

    assert state.auto_approve is True
    assert state.thread_id == "thread-1"


def test_resolve_startup_followup() -> None:
    assert resolve_startup_followup(
        connecting=True,
        initial_prompt="hello",
        thread_id="thread",
        agent=object(),
    ) is None

    submit = resolve_startup_followup(
        connecting=False,
        initial_prompt=" hello ",
        thread_id="thread",
        agent=object(),
    )
    assert submit is not None
    assert submit.kind == "submit_prompt"
    assert submit.prompt == " hello "

    history = resolve_startup_followup(
        connecting=False,
        initial_prompt=None,
        thread_id="thread",
        agent=object(),
    )
    assert history is not None
    assert history.kind == "load_history"
