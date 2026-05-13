"""Tests for scheduled WeCom delivery helpers."""

from __future__ import annotations

from types import SimpleNamespace

from invincat_cli.scheduler.wecom_delivery import (
    build_scheduled_wecom_text,
    latest_assistant_summary,
    scheduled_report_path_for_wecom,
    scheduled_wecom_delivery_target,
    should_send_scheduled_report_file,
)
from invincat_cli.widgets.message_store import MessageData, MessageType


def test_scheduled_wecom_delivery_target_distinguishes_missing_chatid() -> None:
    no_channel = SimpleNamespace(delivery=SimpleNamespace(channels=[]))
    missing_chat = SimpleNamespace(
        delivery=SimpleNamespace(channels=[{"type": "wecom", "chatid": ""}])
    )
    valid = SimpleNamespace(
        delivery=SimpleNamespace(channels=[{"type": "wecom", "chatid": "chat-1"}])
    )

    assert scheduled_wecom_delivery_target(no_channel) == (False, None)
    assert scheduled_wecom_delivery_target(missing_chat) == (True, None)
    assert scheduled_wecom_delivery_target(valid) == (True, "chat-1")


def test_latest_assistant_summary_uses_latest_non_empty_assistant_message() -> None:
    messages = [
        MessageData(type=MessageType.USER, content="user"),
        MessageData(type=MessageType.ASSISTANT, content="first"),
        MessageData(type=MessageType.ASSISTANT, content=""),
        MessageData(type=MessageType.ASSISTANT, content="second"),
    ]

    assert latest_assistant_summary(messages) == "second"


def test_build_scheduled_wecom_text() -> None:
    success = build_scheduled_wecom_text(
        title="Daily",
        status="success",
        summary="done",
        report_path="reports/daily.md",
    )
    failure = build_scheduled_wecom_text(
        title="Daily",
        status="failed",
        error="boom",
    )

    assert "定时任务已完成：Daily" in success
    assert "done" in success
    assert "reports/daily.md" in success
    assert "定时任务执行失败：Daily" in failure
    assert "boom" in failure


def test_scheduled_report_path_for_wecom_resolves_report_date() -> None:
    task = SimpleNamespace(
        timezone="Asia/Shanghai",
        report=SimpleNamespace(mode="report"),
    )
    run = SimpleNamespace(scheduled_for="2026-05-12T17:30:00+00:00")

    assert scheduled_report_path_for_wecom(
        task,
        run,
        check_report_exists=lambda _task, date_str: f"reports/{date_str}.md",
    ) == "reports/2026-05-13.md"


def test_scheduled_report_path_for_wecom_skips_message_only_task() -> None:
    task = SimpleNamespace(report=SimpleNamespace(mode="message"))
    run = SimpleNamespace(scheduled_for="2026-05-12T17:30:00+00:00")

    assert (
        scheduled_report_path_for_wecom(
            task,
            run,
            check_report_exists=lambda _task, _date_str: "unused",
        )
        is None
    )


def test_should_send_scheduled_report_file() -> None:
    assert should_send_scheduled_report_file(
        status="success",
        report_path="reports/daily.md",
    )
    assert not should_send_scheduled_report_file(
        status="failed",
        report_path="reports/daily.md",
    )
    assert not should_send_scheduled_report_file(status="success", report_path=None)
