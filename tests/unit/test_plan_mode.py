"""Tests for plan-related commands."""

from __future__ import annotations

from invincat_cli.command_registry import (
    COMMANDS,
    SLASH_COMMANDS,
    SIDE_EFFECT_FREE,
)
from invincat_cli.plan_agent import PLANNER_ALLOWED_TOOLS
from invincat_cli.app import _PLAN_MODE_ALLOWED_INTERRUPT_TOOLS


class TestSlashCommandRegistration:
    def test_plan_command_registered(self) -> None:
        names = {cmd.name for cmd in COMMANDS}
        assert "/plan" in names

    def test_plan_command_is_side_effect_free(self) -> None:
        # /plan should bypass the normal queue so users can toggle it even
        # while waiting on a slow agent reply.
        assert "/plan" in SIDE_EFFECT_FREE

    def test_plan_command_in_autocomplete(self) -> None:
        autocomplete_names = {entry[0] for entry in SLASH_COMMANDS}
        assert "/plan" in autocomplete_names

    def test_plan_command_has_description(self) -> None:
        plan = next(cmd for cmd in COMMANDS if cmd.name == "/plan")
        assert plan.description, "/plan should have a localized description"

    def test_exit_plan_registered(self) -> None:
        names = {cmd.name for cmd in COMMANDS}
        assert "/exit-plan" in names

    def test_exit_plan_is_side_effect_free(self) -> None:
        assert "/exit-plan" in SIDE_EFFECT_FREE

    def test_plan_interrupt_allowlist_tracks_planner_allowed_tools(self) -> None:
        assert _PLAN_MODE_ALLOWED_INTERRUPT_TOOLS == frozenset(PLANNER_ALLOWED_TOOLS)
