"""Helpers for WeCom turn progress and user-facing messages."""

from __future__ import annotations


WECOM_IDLE_TIMEOUT = 30.0
WECOM_AGENT_TIMEOUT = 30 * 60.0
WECOM_PROGRESS_MAX_INTERVAL = 0.25
WECOM_FILE_NOTIFY_HOLD = 2.0
WECOM_STREAM_BLINK_DELAY = 1.0
WECOM_BLINK_INTERVAL = 0.6


def wecom_user_facing_error(exc: Exception) -> str:
    text = str(exc).strip()
    if text:
        return text
    return type(exc).__name__


def format_wecom_progress_line(
    *,
    running_tool: str | None,
    completed_tools: int,
    assistant_started: bool,
    tick: int = 0,
) -> str:
    """Format the one-line in-place progress update shown before final output."""
    dots = "." * (tick % 3 + 1)
    if running_tool:
        if completed_tools:
            return f"处理中：正在执行工具 `{running_tool}`，已完成 {completed_tools} 个{dots}"
        return f"处理中：正在执行工具 `{running_tool}`{dots}"
    if assistant_started:
        if completed_tools:
            return f"处理中：已完成 {completed_tools} 个工具调用，正在整理回复{dots}"
        return f"处理中：正在整理回复{dots}"
    if completed_tools:
        return f"处理中：已完成 {completed_tools} 个工具调用，正在继续分析{dots}"
    return f"处理中：正在分析问题{dots}"
