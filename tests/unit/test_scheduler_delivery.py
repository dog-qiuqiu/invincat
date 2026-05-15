"""Unit tests for scheduled delivery helpers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from invincat_cli.scheduler.delivery import (
    check_report_exists,
    deliver_webhook,
    is_wecom_deliverable_task,
    report_display_path,
    resolve_report_path,
    save_fallback_report,
    scheduled_task_wecom_chatid,
)
from invincat_cli.scheduler.models import DeliverySpec, ReportSpec, ScheduledTask


def _task(
    tmp_path: Path,
    *,
    title: str = "Daily / Report",
    report: ReportSpec | None = None,
) -> ScheduledTask:
    now = datetime.now(UTC).isoformat()
    return ScheduledTask(
        id="task-1",
        title=title,
        enabled=True,
        prompt="Summarize",
        cron="0 8 * * *",
        timezone="Asia/Shanghai",
        cwd=str(tmp_path),
        delivery=DeliverySpec(channels=[]),
        report=report or ReportSpec(mode="report"),
        created_at=now,
        updated_at=now,
        next_run_at=None,
        last_run_at=None,
        last_status="never",
        last_error=None,
        run_count=0,
        failure_count=0,
    )


def test_scheduled_task_wecom_chatid_filters_and_trims_channels() -> None:
    task = SimpleNamespace(
        delivery=SimpleNamespace(
            channels=[
                "bad",
                {"type": "tui"},
                {"type": "wecom", "chatid": "  "},
                {"type": "wecom", "chatid": " chat-1 "},
            ]
        )
    )

    assert scheduled_task_wecom_chatid(task) == "chat-1"
    assert is_wecom_deliverable_task(task) is True
    assert scheduled_task_wecom_chatid(SimpleNamespace(delivery=None)) == ""


def test_report_path_is_slugged_relative_and_confined(tmp_path: Path) -> None:
    task = _task(tmp_path)

    path = resolve_report_path(task, "2026-05-14")

    assert path == tmp_path / "reports" / "daily---report-2026-05-14.md"
    assert report_display_path(task, "2026-05-14") == (
        "reports/daily---report-2026-05-14.md"
    )

    absolute_output = _task(
        tmp_path,
        report=ReportSpec(output_dir=str(tmp_path / "outside")),
    )
    with pytest.raises(ValueError, match="relative"):
        resolve_report_path(absolute_output, "2026-05-14")

    escaping = _task(tmp_path, report=ReportSpec(output_dir=".."))
    with pytest.raises(ValueError, match="escapes"):
        resolve_report_path(escaping, "2026-05-14")

    absolute_filename = _task(
        tmp_path,
        report=ReportSpec(filename_template=str(tmp_path / "x.md")),
    )
    with pytest.raises(ValueError, match="relative"):
        resolve_report_path(absolute_filename, "2026-05-14")


def test_check_report_exists_requires_non_empty_file(tmp_path: Path) -> None:
    task = _task(tmp_path)

    assert check_report_exists(task, "2026-05-14") is None

    report = resolve_report_path(task, "2026-05-14")
    report.parent.mkdir(parents=True)
    report.write_text("", encoding="utf-8")
    assert check_report_exists(task, "2026-05-14") is None

    report.write_text("done", encoding="utf-8")
    assert check_report_exists(task, "2026-05-14") == str(report)

    invalid = _task(tmp_path, report=ReportSpec(output_dir="/absolute"))
    assert check_report_exists(invalid, "2026-05-14") is None


def test_save_fallback_report_writes_content_and_handles_failures(
    tmp_path: Path,
) -> None:
    task = _task(tmp_path)

    assert save_fallback_report(task, "   ", "2026-05-14") is None

    path = save_fallback_report(task, "fallback content", "2026-05-14")

    assert path == str(resolve_report_path(task, "2026-05-14"))
    assert Path(path).read_text(encoding="utf-8") == "fallback content"

    invalid = _task(tmp_path, report=ReportSpec(output_dir="/absolute"))
    assert save_fallback_report(invalid, "fallback", "2026-05-14") is None


def test_deliver_webhook_posts_payload_and_swallows_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    class Response:
        def __init__(self, *, fail: bool = False) -> None:
            self.fail = fail

        def raise_for_status(self) -> None:
            if self.fail:
                raise RuntimeError("bad status")

    class Client:
        def __init__(self, *, timeout: int) -> None:
            assert timeout == 15

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, *, json: dict) -> Response:
            calls.append((url, json))
            return Response(fail=url.endswith("/fail"))

    monkeypatch.setattr("httpx.AsyncClient", Client)

    asyncio.run(deliver_webhook("https://example.com/ok", {"status": "ok"}))
    asyncio.run(deliver_webhook("https://example.com/fail", {"status": "bad"}))

    assert calls == [
        ("https://example.com/ok", {"status": "ok"}),
        ("https://example.com/fail", {"status": "bad"}),
    ]
