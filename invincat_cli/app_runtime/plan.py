"""Pure helpers for Textual app plan-mode state handling."""

from __future__ import annotations

from typing import Any


def build_planner_turn_input(*, task: str, cwd: str) -> str:
    """Build the planner-agent input for one user planning task."""
    return (
        "[planner_runtime_context]\n"
        f"cwd: `{cwd}`\n"
        "response_language: same as user task\n\n"
        "[user_task]\n"
        f"{task.strip()}"
    )


def build_planner_runtime_context(*, cwd: str) -> str:
    """Build planner system-prompt runtime context."""
    return (
        "## Planner Runtime Context\n\n"
        f"- root_context_dir: `{cwd}`\n"
        "- response_language: same as user task\n"
    )


def build_planner_system_prompt(*, base_prompt: str, cwd: str) -> str:
    """Attach runtime context to the planner base system prompt."""
    return f"{base_prompt}\n\n{build_planner_runtime_context(cwd=cwd)}"


def build_plan_text(todos: list[dict[str, str]]) -> str:
    """Render approved todos as a numbered plain-text plan."""
    return "\n".join(f"{i + 1}. {todo['content']}" for i, todo in enumerate(todos))


def extract_latest_ai_text(messages: list[Any]) -> str:
    """Extract latest assistant text from checkpoint messages."""
    from langchain_core.messages import AIMessage

    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        content = msg.content
        if isinstance(content, str):
            text = content.strip()
            if text:
                return text
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    parts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    parts.append(block)
            text = "\n".join(parts).strip()
            if text:
                return text
    return ""


def extract_latest_human_text(messages: list[Any]) -> str:
    """Extract latest human text from checkpoint messages."""
    from langchain_core.messages import HumanMessage

    for msg in reversed(messages):
        if not isinstance(msg, HumanMessage):
            continue
        content = msg.content
        if isinstance(content, str):
            text = content.strip()
            if text:
                return text
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    parts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    parts.append(block)
            text = "\n".join(parts).strip()
            if text:
                return text
    return ""


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


def build_plan_handoff_prompt(
    todos: list[dict[str, str]],
    *,
    planner_state_values: dict[str, Any] | None = None,
) -> str:
    """Build structured main-agent handoff prompt from approved todo items."""
    plan_text = "\n".join(f"{i + 1}. {todo['content']}" for i, todo in enumerate(todos))
    latest_user_text = ""

    if planner_state_values:
        messages = normalize_state_messages(planner_state_values.get("messages", []))
        latest_user_text = extract_latest_human_text(messages)

    prefer_zh = prefer_zh_for_text(latest_user_text)

    context_lines: list[str] = []
    latest_user_text = latest_user_text.strip()
    if latest_user_text:
        context_lines.append(latest_user_text)

    context_block = ""
    if context_lines:
        rendered_context = "\n".join(
            f"{i + 1}. {line}" for i, line in enumerate(context_lines)
        )
        if prefer_zh:
            context_block = (
                f"\noriginal_user_request:\n规划阶段关键上下文：\n{rendered_context}\n"
            )
        else:
            context_block = (
                "\noriginal_user_request:\n"
                f"Key context from planning phase:\n{rendered_context}\n"
            )

    if prefer_zh:
        return (
            "[approved_plan_handoff]\n"
            "mode: execute_approved_plan\n"
            "instructions:\n"
            "- 请立即执行以下已批准计划。\n"
            "- 这是用户已经批准的计划交接，不要重新规划同一批工作，不要重复请求审批。\n"
            "- 按 approved_todos 顺序执行；只有实现证据表明必须调整时才改变顺序。\n"
            "- 持续更新 todo 状态，并汇报进度、结果和验证情况。\n"
            "- 如果发现超出已批准范围、破坏性、高风险或实质不同的工作，暂停并请求确认。\n"
            f"{context_block}"
            "approved_todos:\n"
            f"{plan_text}"
            "\n[/approved_plan_handoff]"
        )

    return (
        "[approved_plan_handoff]\n"
        "mode: execute_approved_plan\n"
        "instructions:\n"
        "- Execute the following approved plan now.\n"
        "- This is an already approved plan handoff. Do not re-plan the same work or ask for approval again.\n"
        "- Execute approved_todos in order; only change order when implementation evidence requires it.\n"
        "- Keep todo status updated and report progress, results, and verification.\n"
        "- If you discover out-of-scope, destructive, high-risk, or materially different work, pause and ask for confirmation.\n"
        f"{context_block}"
        "approved_todos:\n"
        f"{plan_text}"
        "\n[/approved_plan_handoff]"
    )


def planner_turn_has_write_todos(messages: list[Any]) -> bool:
    """Return whether the latest planner turn invoked `write_todos`."""
    from langchain_core.messages import HumanMessage, ToolMessage

    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            break
        if isinstance(msg, ToolMessage) and getattr(msg, "name", "") == "write_todos":
            return True
    return False


def planner_turn_has_approve_plan(messages: list[Any]) -> bool:
    """Return whether the latest planner turn invoked `approve_plan`."""
    from langchain_core.messages import HumanMessage, ToolMessage

    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            break
        if isinstance(msg, ToolMessage) and getattr(msg, "name", "") == "approve_plan":
            return True
    return False


def planner_turn_approve_plan_decision(messages: list[Any]) -> str | None:
    """Return latest-turn approve_plan decision: approved/rejected/None."""
    from langchain_core.messages import HumanMessage, ToolMessage

    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            break
        if not isinstance(msg, ToolMessage):
            continue
        if getattr(msg, "name", "") != "approve_plan":
            continue

        content = msg.content
        if isinstance(content, str):
            normalized = content.strip().lower()
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    parts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    parts.append(block)
            normalized = "\n".join(parts).strip().lower()
        else:
            normalized = str(content).strip().lower()

        if normalized == "approved":
            return "approved"
        return "rejected"
    return None


def latest_ai_text_after_latest_tool(
    messages: list[Any],
    tool_name: str,
) -> str:
    """Return assistant text emitted after the latest named tool result."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    parts: list[str] = []
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            break
        if isinstance(msg, ToolMessage) and getattr(msg, "name", "") == tool_name:
            break
        if not isinstance(msg, AIMessage):
            continue
        content = msg.content
        if isinstance(content, str):
            text = content.strip()
            if text:
                parts.append(text)
        elif isinstance(content, list):
            block_parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    block_parts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    block_parts.append(block)
            text = "\n".join(block_parts).strip()
            if text:
                parts.append(text)
    return "\n".join(reversed(parts)).strip()


def extract_todos_from_state(state_values: dict[str, Any]) -> list[dict[str, str]]:
    """Read todo list from planner state values."""
    raw_todos = state_values.get("todos")
    if not isinstance(raw_todos, list):
        return []
    todos: list[dict[str, str]] = []
    for raw in raw_todos:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content", "")).strip()
        if not content:
            continue
        status = str(raw.get("status", "pending")).strip() or "pending"
        todos.append({"content": content, "status": status})
    return todos
