"""Tests for plan-mode helpers and command registration."""

from __future__ import annotations

from invincat_cli.command_registry import (
    COMMANDS,
    SLASH_COMMANDS,
    SIDE_EFFECT_FREE,
)
from invincat_cli.plan_mode import (
    PLAN_MODE_PREAMBLE,
    PLAN_MODE_REJECTION_HINT,
    PLAN_READY_SENTINEL,
    WRITE_TOOL_NAMES,
    is_write_tool,
    split_blocked_tools,
)


class TestWriteToolDetection:
    def test_write_tools_blocked(self) -> None:
        for name in [
            "write_file",
            "edit_file",
            "execute",
            "task",
            "launch_async_subagent",
            "update_async_subagent",
            "cancel_async_subagent",
            "compact_conversation",
        ]:
            assert is_write_tool(name), f"{name} should be blocked in plan mode"

    def test_read_tools_not_blocked(self) -> None:
        for name in [
            "read_file",
            "grep",
            "glob",
            "ls",
            "web_search",
            "fetch_url",
            "ask_user",
        ]:
            assert not is_write_tool(name), (
                f"{name} should be allowed in plan mode"
            )

    def test_unknown_tool_not_blocked(self) -> None:
        # Unknown tools default to allowed so plan mode doesn't accidentally
        # break MCP / user-defined tools the framework hasn't classified.
        assert not is_write_tool("some_random_mcp_tool")
        assert not is_write_tool("")

    def test_write_tool_set_is_frozen(self) -> None:
        assert isinstance(WRITE_TOOL_NAMES, frozenset)


class TestSplitBlockedTools:
    def test_all_blocked(self) -> None:
        blocked, allowed = split_blocked_tools(["write_file", "edit_file"])
        assert blocked == ["write_file", "edit_file"]
        assert allowed == []

    def test_all_allowed(self) -> None:
        blocked, allowed = split_blocked_tools(["read_file", "grep"])
        assert blocked == []
        assert allowed == ["read_file", "grep"]

    def test_mixed_preserves_order(self) -> None:
        blocked, allowed = split_blocked_tools(
            ["read_file", "write_file", "grep", "execute"]
        )
        assert blocked == ["write_file", "execute"]
        assert allowed == ["read_file", "grep"]

    def test_empty_input(self) -> None:
        blocked, allowed = split_blocked_tools([])
        assert blocked == []
        assert allowed == []


class TestPlanPrompts:
    def test_preamble_mentions_sentinel(self) -> None:
        # The preamble must instruct the model to emit the sentinel so the
        # CLI can later prompt the user to approve and exit plan mode.
        assert PLAN_READY_SENTINEL in PLAN_MODE_PREAMBLE

    def test_preamble_lists_blocked_tools(self) -> None:
        # Spot-check that the preamble names every blocked tool so the model
        # knows exactly what is off-limits.
        for name in [
            "write_file",
            "edit_file",
            "execute",
            "task",
            "launch_async_subagent",
            "update_async_subagent",
            "cancel_async_subagent",
            "compact_conversation",
        ]:
            assert name in PLAN_MODE_PREAMBLE, (
                f"preamble should mention {name}"
            )

    def test_preamble_mentions_exit_command(self) -> None:
        assert "/exit-plan" in PLAN_MODE_PREAMBLE

    def test_rejection_hint_is_user_friendly(self) -> None:
        assert "plan mode" in PLAN_MODE_REJECTION_HINT.lower()
        assert "/exit-plan" in PLAN_MODE_REJECTION_HINT.lower() or (
            "refine" in PLAN_MODE_REJECTION_HINT.lower()
        )


class TestSlashCommandRegistration:
    def test_plan_command_registered(self) -> None:
        names = {cmd.name for cmd in COMMANDS}
        assert "/plan" in names
        assert "/exit-plan" in names

    def test_plan_command_is_side_effect_free(self) -> None:
        # /plan should bypass the normal queue so users can toggle it even
        # while waiting on a slow agent reply.
        assert "/plan" in SIDE_EFFECT_FREE
        assert "/exit-plan" in SIDE_EFFECT_FREE

    def test_plan_command_in_autocomplete(self) -> None:
        autocomplete_names = {entry[0] for entry in SLASH_COMMANDS}
        assert "/plan" in autocomplete_names
        assert "/exit-plan" in autocomplete_names

    def test_plan_command_has_description(self) -> None:
        plan = next(cmd for cmd in COMMANDS if cmd.name == "/plan")
        assert plan.description, "/plan should have a localized description"
