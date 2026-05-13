"""Pure helpers for scheduled task WeCom delivery."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def scheduled_wecom_delivery_target(task: Any) -> tuple[bool, str | None]:  # noqa: ANN401
    """Return whether a WeCom channel exists and its first non-empty chat id."""
    channels = getattr(getattr(task, "delivery", None), "channels", []) or []
    has_wecom = False
    for channel in channels:
        if not isinstance(channel, dict) or channel.get("type") != "wecom":
            continue
        has_wecom = True
        chatid = str(channel.get("chatid") or "").strip()
        if chatid:
            return True, chatid
    return has_wecom, None


def scheduled_wecom_chat_id(task: Any) -> str | None:  # noqa: ANN401
    """Return the first configured WeCom chat id for a scheduled task."""
    _has_wecom, chatid = scheduled_wecom_delivery_target(task)
    return chatid


def latest_assistant_summary(messages: list[Any], *, max_chars: int = 1200) -> str:
    """Return the latest non-empty assistant message content from message data."""
    from invincat_cli.widgets.message_store import MessageType

    assistant_messages = [
        m.content.strip()
        for m in messages
        if getattr(m, "type", None) == MessageType.ASSISTANT
        and getattr(m, "content", "").strip()
    ]
    summary = assistant_messages[-1] if assistant_messages else ""
    if len(summary) > max_chars:
        return summary[:max_chars].rstrip() + "\n\n(摘要过长，已截断)"
    return summary


def scheduled_report_path_for_wecom(
    task: Any,  # noqa: ANN401
    run: Any,  # noqa: ANN401
    *,
    check_report_exists: Callable[[Any, str], str | None] | None = None,
) -> str | None:
    """Resolve the report path attached to a completed scheduled run."""
    if getattr(getattr(task, "report", None), "mode", None) != "report":
        return None

    report_exists = check_report_exists
    if report_exists is None:
        from invincat_cli.scheduler.delivery import (
            check_report_exists as default_check_report_exists,
        )

        report_exists = default_check_report_exists

    try:
        scheduled_for = datetime.fromisoformat(run.scheduled_for)
        if scheduled_for.tzinfo is None:
            scheduled_for = scheduled_for.replace(tzinfo=UTC)
        date_str = scheduled_for.astimezone(ZoneInfo(task.timezone)).strftime(
            "%Y-%m-%d"
        )
        return report_exists(task, date_str)
    except Exception:
        logger.warning(
            "Failed to resolve scheduled report path for WeCom delivery",
            exc_info=True,
        )
        return None


def should_send_scheduled_report_file(
    *,
    status: str,
    report_path: str | None,
) -> bool:
    """Return whether a report file should be sent after the text update."""
    return status == "success" and bool(report_path)


def build_scheduled_wecom_text(
    *,
    title: str,
    status: str,
    summary: str = "",
    report_path: str | None = None,
    error: str | None = None,
) -> str:
    """Build localized WeCom text for a completed scheduled run."""
    if status == "success":
        content = f"定时任务已完成：{title}"
        if summary:
            content += f"\n\n{summary}"
        if report_path:
            content += f"\n\n报告文件：{report_path}"
        return content

    content = f"定时任务执行失败：{title}"
    if error:
        content += f"\n\n{error}"
    return content
