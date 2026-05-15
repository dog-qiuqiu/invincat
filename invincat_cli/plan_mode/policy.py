"""Plan-mode policy, normalization, and drift detection."""

from __future__ import annotations

import json
import re
from typing import Any

from invincat_cli.plan_mode.models import PlanDrift, PlanStep

PLANNER_ALLOWED_TOOLS: tuple[str, ...] = (
    "read_file",
    "ls",
    "glob",
    "grep",
    "web_search",
    "fetch_url",
    "write_todos",
    "ask_user",
    "approve_plan",
)
"""Planner-visible read/planning tool contract."""

_FINAL_ANSWER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"```",
        r"^diff --git ",
        r"^\+\+\+ ",
        r"^--- ",
        r"^@@ ",
        r"\bhere is the (implementation|fix|patch|code|documentation)\b",
        r"\bimplemented\b",
        r"已完成",
        r"修复如下",
        r"下面是代码",
        r"以下是代码",
        r"实现如下",
    )
)
_TODO_PATTERN = re.compile(r"^\s*(\d+)\.\s+(.+)$")


def normalize_plan_todos(todos: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Normalize todo items before fingerprinting, display, or handoff."""
    return [
        {
            "content": str(item.get("content", "")).strip(),
            "status": str(item.get("status", "pending")).strip() or "pending",
        }
        for item in todos
        if isinstance(item, dict) and str(item.get("content", "")).strip()
    ]


def normalize_plan_steps(todos: list[dict[str, Any]]) -> list[PlanStep]:
    """Normalize arbitrary todo dictionaries into enhanced plan steps."""
    steps: list[PlanStep] = []
    for index, item in enumerate(todos, start=1):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        raw_status = str(item.get("status", "pending")).strip() or "pending"
        status = raw_status if raw_status in {"pending", "in_progress", "completed"} else "pending"
        step: PlanStep = {
            "id": str(item.get("id") or f"step-{index}"),
            "content": content,
            "status": status,  # type: ignore[typeddict-item]
        }
        for key in ("rationale", "verification"):
            value = str(item.get(key, "")).strip()
            if value:
                step[key] = value  # type: ignore[literal-required]
        raw_target = item.get("target")
        if isinstance(raw_target, list):
            target = [str(value).strip() for value in raw_target if str(value).strip()]
            if target:
                step["target"] = target
        raw_risk = str(item.get("risk", "")).strip().lower()
        if raw_risk in {"low", "medium", "high"}:
            step["risk"] = raw_risk  # type: ignore[typeddict-item]
        steps.append(step)
    return steps


def plan_todos_fingerprint(todos: list[dict[str, Any]]) -> str:
    """Return a stable fingerprint for plan approval dedupe."""
    return json.dumps(normalize_plan_todos(todos), ensure_ascii=False, sort_keys=True)


def extract_todos_from_state(state_values: dict[str, Any]) -> list[dict[str, str]]:
    """Read todo list from planner state values."""
    raw_todos = state_values.get("todos")
    if not isinstance(raw_todos, list):
        return []
    return normalize_plan_todos(raw_todos)


def extract_todos_from_message(message: str) -> list[dict[str, str]] | None:
    """Extract numbered todos from a fallback assistant message."""
    todos: list[dict[str, str]] = []
    for line in message.splitlines():
        match = _TODO_PATTERN.match(line)
        if not match:
            continue
        content = match.group(2).strip()
        if content:
            todos.append(
                {
                    "content": content,
                    "status": "in_progress" if not todos else "pending",
                }
            )
    return todos or None


def message_text(message: Any) -> str:  # noqa: ANN401
    """Extract text from a LangChain-style message content field."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and "text" in block:
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts).strip()
    return str(content).strip()


def latest_turn_messages(messages: list[Any]) -> list[Any]:
    """Return messages after the latest human message, inclusive of that turn only."""
    from langchain_core.messages import HumanMessage

    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return messages[index + 1 :]
    return messages


def turn_has_tool(messages: list[Any], tool_name: str) -> bool:
    """Return whether the latest turn has a tool result with the given name."""
    from langchain_core.messages import ToolMessage

    return any(
        isinstance(msg, ToolMessage) and getattr(msg, "name", "") == tool_name
        for msg in latest_turn_messages(messages)
    )


def _tool_call_todos(messages: list[Any], tool_name: str) -> list[dict[str, Any]] | None:
    """Return todos from the latest AI tool call args, when available."""
    for msg in reversed(latest_turn_messages(messages)):
        tool_calls = getattr(msg, "tool_calls", None)
        if not isinstance(tool_calls, list):
            continue
        for call in reversed(tool_calls):
            if not isinstance(call, dict) or call.get("name") != tool_name:
                continue
            args = call.get("args", {})
            if not isinstance(args, dict):
                continue
            todos = args.get("todos")
            if isinstance(todos, list):
                return [todo for todo in todos if isinstance(todo, dict)]
    return None


def latest_ai_text(messages: list[Any]) -> str:
    """Extract latest assistant text from checkpoint messages."""
    from langchain_core.messages import AIMessage

    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            text = message_text(msg)
            if text:
                return text
    return ""


def latest_human_text(messages: list[Any]) -> str:
    """Extract latest human text from checkpoint messages."""
    from langchain_core.messages import HumanMessage

    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            text = message_text(msg)
            if text:
                return text
    return ""


def ai_text_after_latest_tool(messages: list[Any], tool_name: str) -> str:
    """Return assistant text emitted after the latest named tool result."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    parts: list[str] = []
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            break
        if isinstance(msg, ToolMessage) and getattr(msg, "name", "") == tool_name:
            break
        if isinstance(msg, AIMessage):
            text = message_text(msg)
            if text:
                parts.append(text)
    return "\n".join(reversed(parts)).strip()


def approval_decision(messages: list[Any]) -> str | None:
    """Return latest-turn approve_plan decision: approved/rejected/None."""
    from langchain_core.messages import ToolMessage

    for msg in reversed(latest_turn_messages(messages)):
        if not isinstance(msg, ToolMessage):
            continue
        if getattr(msg, "name", "") != "approve_plan":
            continue
        return "approved" if message_text(msg).strip().lower() == "approved" else "rejected"
    return None


def text_looks_like_final_answer(text: str) -> bool:
    """Return whether planner text looks like an attempted deliverable."""
    if not text.strip():
        return False
    return any(pattern.search(text) for pattern in _FINAL_ANSWER_PATTERNS)


def detect_planner_drift(messages: list[Any]) -> PlanDrift | None:
    """Detect planner turns that drift from `write_todos` + `approve_plan`."""
    from langchain_core.messages import ToolMessage

    turn = latest_turn_messages(messages)
    disallowed = sorted(
        {
            str(getattr(msg, "name", "")).strip()
            for msg in turn
            if isinstance(msg, ToolMessage)
            and str(getattr(msg, "name", "")).strip()
            and str(getattr(msg, "name", "")).strip() not in PLANNER_ALLOWED_TOOLS
        }
    )
    if disallowed:
        return {
            "reason": "disallowed_tool",
            "message": f"Planner called disallowed tools: {', '.join(disallowed)}.",
        }

    text = latest_ai_text(turn)
    has_write = turn_has_tool(messages, "write_todos")
    has_approve = turn_has_tool(messages, "approve_plan")
    if text_looks_like_final_answer(text):
        return {
            "reason": "final_answer",
            "message": "Planner produced final-answer content instead of an approval checklist.",
        }
    if text and not has_write:
        return {
            "reason": "missing_todos",
            "message": "Planner returned text without calling write_todos.",
        }
    if has_write and not has_approve:
        return {
            "reason": "missing_approval",
            "message": "Planner called write_todos without approve_plan.",
        }
    write_todos = _tool_call_todos(messages, "write_todos")
    approve_todos = _tool_call_todos(messages, "approve_plan")
    if (
        write_todos is not None
        and approve_todos is not None
        and plan_todos_fingerprint(write_todos) != plan_todos_fingerprint(approve_todos)
    ):
        return {
            "reason": "todo_mismatch",
            "message": "Planner used different todos for write_todos and approve_plan.",
        }
    return None
