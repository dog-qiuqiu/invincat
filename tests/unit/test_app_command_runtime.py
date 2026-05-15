"""Tests for slash-command routing helpers."""

from __future__ import annotations

from invincat_cli.app_runtime.command import (
    CommandRoute,
    rewrite_skill_creator_command,
    route_slash_command,
)


def test_route_simple_commands() -> None:
    assert route_slash_command("/q") == CommandRoute(kind="quit", normalized="/q")
    assert route_slash_command("/help") == CommandRoute(
        kind="help",
        normalized="/help",
    )
    assert route_slash_command("/docs") == CommandRoute(
        kind="url",
        normalized="/docs",
    )
    assert route_slash_command("/compact") == CommandRoute(
        kind="offload",
        normalized="/compact",
    )


def test_route_prefix_commands() -> None:
    route = route_slash_command("/plan Refactor scheduler")
    assert route.kind == "plan"
    assert route.normalized == "/plan"
    assert route.plan_task == "Refactor scheduler"
    assert route_slash_command("/schedule list").kind == "schedule"
    assert route_slash_command("/model memory").kind == "model"
    assert route_slash_command("/skill:demo run").kind == "skill"


def test_route_wecom_commands() -> None:
    assert route_slash_command("/wecombot-start") == CommandRoute(
        kind="wecom",
        normalized="/wecombot-start",
        wecom_action="start",
    )
    assert route_slash_command("/wecombot-status").wecom_action == "status"
    assert route_slash_command("/wecombot-stop").wecom_action == "stop"


def test_rewrite_skill_creator_command() -> None:
    assert rewrite_skill_creator_command("/skill-creator") == "/skill:skill-creator"
    assert rewrite_skill_creator_command("/skill-creator new skill") == (
        "/skill:skill-creator new skill"
    )
    route = route_slash_command("/skill-creator new skill")
    assert route.kind == "skill_creator"
    assert route.rewritten_command == "/skill:skill-creator new skill"


def test_route_unknown_command() -> None:
    assert route_slash_command(" /NOPE ") == CommandRoute(
        kind="unknown",
        normalized="/nope",
    )
