"""Focused tests for schedule frontend payload helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from invincat_cli.scheduler import payloads
from invincat_cli.scheduler.models import DeliverySpec, ReportSpec, ScheduledTask
from invincat_cli.scheduler.payloads import (
    apply_schedule_update_payload,
    build_schedule_create_payload_result,
)


def _task(tmp_path: Path, *, schedule_type: str = "recurring") -> ScheduledTask:
    now = datetime.now(UTC).isoformat()
    return ScheduledTask(
        id="task-1",
        title="Old",
        enabled=True,
        prompt="old prompt",
        cron="0 8 * * *",
        timezone="Asia/Shanghai",
        cwd=str(tmp_path),
        delivery=DeliverySpec(),
        report=ReportSpec(),
        created_at=now,
        updated_at=now,
        next_run_at=now,
        last_run_at=None,
        last_status="never",
        last_error=None,
        run_count=0,
        failure_count=0,
        schedule_type=schedule_type,
        run_at="not-a-date" if schedule_type == "once" else None,
    )


def test_build_schedule_create_payload_resolves_wecom_and_invalid_type_fallback(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 5, 14, 8, 0, tzinfo=UTC)

    result = build_schedule_create_payload_result(
        {
            "task_id": "task-1",
            "title": "Report Title",
            "prompt": "do it",
            "schedule_type": "bad",
            "delivery": "wecom",
            "output_mode": "report",
            "report_format": "text",
        },
        cwd=tmp_path,
        active_wecom_frame={"chat": "frame"},
        now=now,
        resolve_active_chat_id=lambda _frame: "chat-1",
    )

    assert result.task.schedule_type == "recurring"
    assert result.task.delivery.channels == [{"type": "wecom", "chatid": "chat-1"}]
    assert result.task.report.mode == "report"
    assert result.task.report.filename_template == "report-title-{date}.text"
    assert result.report_path_display == "reports/report-title-{date}.text"


def test_build_schedule_create_payload_keeps_tui_when_wecom_resolution_fails(
    tmp_path: Path,
) -> None:
    def fail_resolve(_frame: dict) -> str:
        raise RuntimeError("missing chat")

    result = build_schedule_create_payload_result(
        {"delivery": "wecom"},
        cwd=tmp_path,
        active_wecom_frame={"chat": "frame"},
        resolve_active_chat_id=fail_resolve,
    )

    assert result.task.delivery.channels == [{"type": "tui"}]


def test_build_schedule_create_payload_rejects_unparseable_once_time(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="Could not compute"):
        build_schedule_create_payload_result(
            {"schedule_type": "once", "run_at": "not-a-date"},
            cwd=tmp_path,
        )


def test_apply_schedule_update_payload_updates_prompt_and_enabled(
    tmp_path: Path,
) -> None:
    task = _task(tmp_path)
    now = datetime(2026, 5, 14, 8, 0, tzinfo=UTC)

    updated = apply_schedule_update_payload(
        task,
        {"title": "New", "prompt": "new prompt", "enabled": False},
        now=now,
    )

    assert updated.title == "New"
    assert updated.prompt == "new prompt"
    assert updated.enabled is False
    assert updated.updated_at == now.isoformat()


def test_apply_schedule_update_payload_rejects_uncomputable_next_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = _task(tmp_path)
    monkeypatch.setattr(payloads, "compute_next_run", lambda *_args: None)

    with pytest.raises(ValueError, match="Could not compute"):
        apply_schedule_update_payload(task, {"cron": "0 9 * * *"})

    once = _task(tmp_path, schedule_type="once")
    with pytest.raises(ValueError, match="Could not compute"):
        apply_schedule_update_payload(once, {"timezone": "UTC"})
