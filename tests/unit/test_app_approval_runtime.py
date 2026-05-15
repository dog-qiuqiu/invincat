"""Tests for approval runtime helpers used by the Textual app."""

from __future__ import annotations

from invincat_cli.app_runtime.approval import (
    APPROVAL_PLACEHOLDER_CLASS,
    APPROVAL_PLACEHOLDER_TEXT,
    build_approve_plan_action_request,
    build_auto_approved_shell_message,
    build_interaction_widget_id,
    deadline_expired,
    disallowed_plan_interrupt_tools,
    map_raw_approval_to_plan_decision,
    normalize_plan_todos,
    pending_interaction_timeout_log,
    pending_widget_deadline,
    plan_interrupt_guard_disallowed_tools,
    plan_todos_fingerprint,
    resolve_auto_approved_shell_commands,
    should_cancel_detached_placeholder,
    user_is_typing,
)


def test_disallowed_plan_interrupt_tools_filters_allowed_tools() -> None:
    assert disallowed_plan_interrupt_tools(
        [
            {"name": "web_search"},
            {"name": "write_file"},
            {"name": "edit_file"},
            {"name": ""},
        ],
        allowed_tools=frozenset({"web_search"}),
    ) == ["edit_file", "write_file"]


def test_disallowed_plan_interrupt_tools_ignores_invalid_requests() -> None:
    assert disallowed_plan_interrupt_tools(None) == []
    assert disallowed_plan_interrupt_tools("write_file") == []
    assert disallowed_plan_interrupt_tools(42) == []


def test_plan_interrupt_guard_disallowed_tools_requires_active_plan_guard() -> None:
    requests = [{"name": "web_search"}, {"name": "write_file"}]

    assert plan_interrupt_guard_disallowed_tools(
        requests,
        bypass_plan_guard=False,
        plan_mode=True,
        active_turn_is_planner=True,
        allowed_tools=frozenset({"web_search"}),
    ) == ["write_file"]
    assert (
        plan_interrupt_guard_disallowed_tools(
            requests,
            bypass_plan_guard=True,
            plan_mode=True,
            active_turn_is_planner=True,
            allowed_tools=frozenset({"web_search"}),
        )
        == []
    )
    assert (
        plan_interrupt_guard_disallowed_tools(
            requests,
            bypass_plan_guard=False,
            plan_mode=False,
            active_turn_is_planner=True,
            allowed_tools=frozenset({"web_search"}),
        )
        == []
    )
    assert (
        plan_interrupt_guard_disallowed_tools(
            requests,
            bypass_plan_guard=False,
            plan_mode=True,
            active_turn_is_planner=False,
            allowed_tools=frozenset({"web_search"}),
        )
        == []
    )


def test_resolve_auto_approved_shell_commands_requires_all_requests_allowed() -> None:
    def _allowed(command: str, _allow_list: list[str], *, cwd: str) -> bool:
        return cwd == "/repo" and command.startswith("pytest")

    assert resolve_auto_approved_shell_commands(
        [{"name": "shell", "args": {"command": "pytest -q"}}],
        shell_allow_list=["pytest"],
        shell_tool_names=frozenset({"shell"}),
        cwd="/repo",
        is_shell_command_allowed=_allowed,
    ) == ["pytest -q"]

    assert (
        resolve_auto_approved_shell_commands(
            [
                {"name": "shell", "args": {"command": "pytest -q"}},
                {"name": "write_file", "args": {}},
            ],
            shell_allow_list=["pytest"],
            shell_tool_names=frozenset({"shell"}),
            cwd="/repo",
            is_shell_command_allowed=_allowed,
        )
        is None
    )
    assert (
        resolve_auto_approved_shell_commands(
            [{"name": "shell", "args": {"command": "pytest -q"}}],
            shell_allow_list=[],
            shell_tool_names=frozenset({"shell"}),
            cwd="/repo",
            is_shell_command_allowed=_allowed,
        )
        is None
    )
    assert (
        resolve_auto_approved_shell_commands(
            [{"name": "shell", "args": {"command": "rm -rf build"}}],
            shell_allow_list=["pytest"],
            shell_tool_names=frozenset({"shell"}),
            cwd="/repo",
            is_shell_command_allowed=_allowed,
        )
        is None
    )


def test_build_auto_approved_shell_message() -> None:
    assert build_auto_approved_shell_message("pytest -q").endswith(": pytest -q")


def test_user_is_typing() -> None:
    assert user_is_typing(last_typed_at=None, now=10.0) is False
    assert user_is_typing(last_typed_at=9.0, now=10.0, threshold_seconds=2.0) is True
    assert user_is_typing(last_typed_at=7.0, now=10.0, threshold_seconds=2.0) is False


def test_interaction_widget_helpers() -> None:
    assert build_interaction_widget_id(prefix="approval-menu", token="abc") == (
        "approval-menu-abc"
    )
    assert pending_widget_deadline(now=10.0, timeout_seconds=5.0) == 15.0
    assert deadline_expired(now=15.1, deadline=15.0) is True
    assert deadline_expired(now=15.0, deadline=15.0) is False
    assert should_cancel_detached_placeholder(placeholder_attached=False) is True
    assert should_cancel_detached_placeholder(placeholder_attached=True) is False
    assert APPROVAL_PLACEHOLDER_TEXT
    assert APPROVAL_PLACEHOLDER_CLASS == "approval-placeholder"


def test_pending_interaction_timeout_log() -> None:
    assert pending_interaction_timeout_log(kind="approval") == (
        "Timed out waiting for previous approval widget to clear after 30s; "
        "proceeding with new approval"
    )
    assert pending_interaction_timeout_log(kind="ask_user") == (
        "Timed out waiting for previous ask-user widget to clear. "
        "Forcefully cleaning up."
    )


def test_build_approve_plan_action_request() -> None:
    todos = [{"content": "Implement", "status": "pending"}]

    assert build_approve_plan_action_request(todos) == {
        "name": "approve_plan",
        "description": "Approve or refine this generated plan.",
        "args": {"todos": todos},
    }


def test_normalize_plan_todos_and_fingerprint() -> None:
    todos = [
        {"content": "  Implement  ", "status": "  in_progress  "},
        {"content": "   ", "status": "pending"},
        {"content": "Test"},
    ]

    assert normalize_plan_todos(todos) == [
        {"content": "Implement", "status": "in_progress"},
        {"content": "Test", "status": "pending"},
    ]
    assert plan_todos_fingerprint(todos) == (
        '[{"content": "Implement", "status": "in_progress"}, '
        '{"content": "Test", "status": "pending"}]'
    )


def test_map_raw_approval_to_plan_decision() -> None:
    assert map_raw_approval_to_plan_decision({"type": "approve"}) == {
        "type": "approved"
    }
    assert map_raw_approval_to_plan_decision({"type": "reject"}) == {"type": "rejected"}
