"""Approved-plan handoff prompt builders."""

from __future__ import annotations

from typing import Any

from invincat_cli.plan_mode.policy import (
    latest_human_text,
    normalize_plan_steps,
)


def build_plan_text(todos: list[dict[str, str]]) -> str:
    """Render approved todos as a numbered plain-text plan."""
    return "\n".join(f"{i + 1}. {todo['content']}" for i, todo in enumerate(todos))


def looks_cjk_text(text: str) -> bool:
    """Heuristic: detect whether text contains CJK characters."""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def prefer_zh_for_text(text: str) -> bool:
    """Decide whether runtime prompts should use Chinese."""
    if looks_cjk_text(text):
        return True
    try:
        from invincat_cli.i18n import Language, get_i18n

        return get_i18n().language == Language.ZH
    except Exception:
        return False


def normalize_state_messages(raw_messages: Any) -> list[Any]:  # noqa: ANN401
    """Normalize checkpoint `messages` into LangChain message objects."""
    if (
        isinstance(raw_messages, list)
        and raw_messages
        and isinstance(raw_messages[0], dict)
    ):
        from langchain_core.messages.utils import convert_to_messages

        return convert_to_messages(raw_messages)
    if isinstance(raw_messages, list):
        return raw_messages
    return []


def render_enhanced_plan(todos: list[dict[str, str]]) -> str:
    """Render plan steps with optional enhanced metadata for handoff."""
    lines: list[str] = []
    for index, step in enumerate(normalize_plan_steps(todos), start=1):
        lines.append(f"{index}. {step['content']}")
        if rationale := step.get("rationale"):
            lines.append(f"   rationale: {rationale}")
        if target := step.get("target"):
            lines.append(f"   target: {', '.join(target)}")
        if verification := step.get("verification"):
            lines.append(f"   verification: {verification}")
        if risk := step.get("risk"):
            lines.append(f"   risk: {risk}")
    return "\n".join(lines)


def build_plan_handoff_prompt(
    todos: list[dict[str, str]],
    *,
    planner_state_values: dict[str, Any] | None = None,
    refinement_notes: list[str] | None = None,
) -> str:
    """Build structured main-agent handoff prompt from approved todo items."""
    messages: list[Any] = []
    if planner_state_values:
        messages = normalize_state_messages(planner_state_values.get("messages", []))
    latest_user_text = latest_human_text(messages).strip()
    prefer_zh = prefer_zh_for_text(latest_user_text)
    plan_text = render_enhanced_plan(todos)
    notes = [note.strip() for note in (refinement_notes or []) if note.strip()]

    if prefer_zh:
        original_block = latest_user_text or "未记录"
        notes_block = "\n".join(f"- {note}" for note in notes) if notes else "无"
        return (
            "[approved_plan_handoff]\n"
            "mode: execute_approved_plan\n\n"
            "original_user_request:\n"
            "规划阶段关键上下文：\n"
            f"{original_block}\n\n"
            "refinement_notes:\n"
            f"{notes_block}\n\n"
            "approved_plan:\n"
            f"{plan_text}\n\n"
            "execution_rules:\n"
            "- 请立即执行以下已批准计划。\n"
            "- 不要重新规划同一批工作。\n"
            "- 不要重复请求审批。\n"
            "- 保持 todo 状态与执行进度一致。\n"
            "- 如果范围、风险或破坏性行为发生变化，请暂停并请求确认。\n"
            "[/approved_plan_handoff]"
        )

    original_block = latest_user_text or "Not recorded."
    notes_block = "\n".join(f"- {note}" for note in notes) if notes else "None."
    return (
        "[approved_plan_handoff]\n"
        "mode: execute_approved_plan\n\n"
        "original_user_request:\n"
        f"{original_block}\n\n"
        "refinement_notes:\n"
        f"{notes_block}\n\n"
        "approved_plan:\n"
        f"{plan_text}\n\n"
        "execution_rules:\n"
        "- Execute this approved plan now.\n"
        "- Do not re-plan the same work.\n"
        "- Do not ask for approval again.\n"
        "- Keep todo status aligned.\n"
        "- Pause if scope, risk, or destructive behavior changes.\n"
        "[/approved_plan_handoff]"
    )
