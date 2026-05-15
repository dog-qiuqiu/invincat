"""Approval runtime helpers for the Textual app."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from invincat_cli.middleware.plan_agent import PLANNER_ALLOWED_TOOLS

PLAN_MODE_ALLOWED_INTERRUPT_TOOLS: frozenset[str] = frozenset(PLANNER_ALLOWED_TOOLS)
"""Interrupt-gated tools allowed to proceed in `/plan` mode."""

TYPING_IDLE_THRESHOLD_SECONDS: float = 2.0
"""Seconds since the last keystroke after which the user is considered idle."""

DEFERRED_APPROVAL_TIMEOUT_SECONDS: float = 30.0
"""Maximum seconds to defer approval while the user is typing."""

PENDING_WIDGET_WAIT_SECONDS: float = 30.0
"""Maximum seconds to wait for a previous interaction widget to clear."""

INTERACTION_POLL_SECONDS: float = 0.1
"""Polling interval while waiting for pending interaction widgets."""

DEFERRED_APPROVAL_POLL_SECONDS: float = 0.2
"""Polling interval while waiting for typing to stop before showing approval."""

APPROVAL_PLACEHOLDER_TEXT = "Waiting for typing to finish..."
APPROVAL_PLACEHOLDER_CLASS = "approval-placeholder"

APPROVAL_PENDING_TIMEOUT_LOG = (
    "Timed out waiting for previous approval widget to clear after 30s; "
    "proceeding with new approval"
)
ASK_USER_PENDING_TIMEOUT_LOG = (
    "Timed out waiting for previous ask-user widget to clear. Forcefully cleaning up."
)


def _action_request_mappings(action_requests: object) -> list[Mapping[str, Any]]:
    if not action_requests or isinstance(action_requests, str | bytes):
        return []
    if not isinstance(action_requests, Iterable):
        return []
    return [req for req in action_requests if isinstance(req, Mapping)]


def disallowed_plan_interrupt_tools(
    action_requests: object,
    *,
    allowed_tools: frozenset[str] = PLAN_MODE_ALLOWED_INTERRUPT_TOOLS,
) -> list[str]:
    """Return interrupting tool names that are not allowed in `/plan` mode."""
    tool_names = {
        str(req.get("name", "")).strip()
        for req in _action_request_mappings(action_requests)
    }
    return sorted(
        tool_name
        for tool_name in tool_names
        if tool_name and tool_name not in allowed_tools
    )


def plan_interrupt_guard_disallowed_tools(
    action_requests: object,
    *,
    bypass_plan_guard: bool,
    plan_mode: bool,
    active_turn_is_planner: bool,
    allowed_tools: frozenset[str] = PLAN_MODE_ALLOWED_INTERRUPT_TOOLS,
) -> list[str]:
    """Return disallowed tool names when `/plan` interrupt guard applies."""
    if bypass_plan_guard or not plan_mode or not active_turn_is_planner:
        return []
    return disallowed_plan_interrupt_tools(
        action_requests,
        allowed_tools=allowed_tools,
    )


def resolve_auto_approved_shell_commands(
    action_requests: object,
    *,
    shell_allow_list: Sequence[str],
    shell_tool_names: frozenset[str],
    cwd: str,
    is_shell_command_allowed: Callable[..., bool],
) -> list[str] | None:
    """Return shell commands when every request can be auto-approved."""
    requests = _action_request_mappings(action_requests)
    if not shell_allow_list or not requests:
        return None

    approved_commands: list[str] = []
    for req in requests:
        if req.get("name") not in shell_tool_names:
            return None
        args = req.get("args", {})
        command = str(args.get("command", "")) if isinstance(args, Mapping) else ""
        if not is_shell_command_allowed(command, shell_allow_list, cwd=cwd):
            return None
        approved_commands.append(command)

    return approved_commands or None


def build_auto_approved_shell_message(command: str) -> str:
    """Build the message shown for an allow-list auto-approved command."""
    return f"\u2713 Auto-approved shell command (allow-list): {command}"


def user_is_typing(
    *,
    last_typed_at: float | None,
    now: float,
    threshold_seconds: float = TYPING_IDLE_THRESHOLD_SECONDS,
) -> bool:
    """Return whether the user typed recently enough to defer approval."""
    if last_typed_at is None:
        return False
    return (now - last_typed_at) < threshold_seconds


def build_interaction_widget_id(*, prefix: str, token: str) -> str:
    """Build a stable DOM id for a transient interaction widget."""
    return f"{prefix}-{token}"


def pending_widget_deadline(
    *,
    now: float,
    timeout_seconds: float = PENDING_WIDGET_WAIT_SECONDS,
) -> float:
    """Return the deadline for waiting on a previous interaction widget."""
    return now + timeout_seconds


def pending_interaction_timeout_log(*, kind: str) -> str:
    """Return the timeout log message for a pending interaction kind."""
    if kind == "ask_user":
        return ASK_USER_PENDING_TIMEOUT_LOG
    return APPROVAL_PENDING_TIMEOUT_LOG


def deadline_expired(*, now: float, deadline: float) -> bool:
    """Return whether a polling loop has passed its deadline."""
    return now > deadline


def should_cancel_detached_placeholder(*, placeholder_attached: bool) -> bool:
    """Return whether a detached deferred approval placeholder should cancel."""
    return not placeholder_attached


def build_approve_plan_action_request(todos: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the approval action request for generated plan todos."""
    return {
        "name": "approve_plan",
        "description": "Approve or refine this generated plan.",
        "args": {"todos": todos},
    }


def normalize_plan_todos(todos: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Normalize todo items before fingerprinting or display handoff."""
    return [
        {
            "content": str(item.get("content", "")).strip(),
            "status": str(item.get("status", "pending")).strip() or "pending",
        }
        for item in todos
        if isinstance(item, dict) and str(item.get("content", "")).strip()
    ]


def plan_todos_fingerprint(todos: list[dict[str, Any]]) -> str:
    """Return a stable fingerprint for plan approval dedupe."""
    return json.dumps(normalize_plan_todos(todos), ensure_ascii=False, sort_keys=True)


def map_raw_approval_to_plan_decision(raw: Mapping[str, Any]) -> dict[str, str]:
    """Map a standard ApprovalMenu result to a plan approval result."""
    decision = str(raw.get("type", "")).strip().lower()
    if decision == "approve":
        return {"type": "approved"}
    return {"type": "rejected"}
