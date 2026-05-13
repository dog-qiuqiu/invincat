"""Tests for scheduler runtime helpers used by the Textual app."""

from __future__ import annotations

from pathlib import Path

import pytest

from invincat_cli.app_runtime.scheduler import (
    active_scheduled_task_id,
    remove_scheduled_messages,
    resolve_scheduled_wecom_file_path,
    scheduled_run_matches,
    should_deliver_scheduled_result,
)
from invincat_cli.app_runtime.state import QueuedMessage


def test_remove_scheduled_messages_filters_only_matching_run() -> None:
    messages = [
        QueuedMessage(
            text="remove",
            mode="normal",
            scheduled_run_id="run-1",
            scheduled_task_id="task-1",
        ),
        QueuedMessage(
            text="keep other run",
            mode="normal",
            scheduled_run_id="run-2",
            scheduled_task_id="task-1",
        ),
        QueuedMessage(text="keep normal", mode="normal"),
    ]

    remaining = remove_scheduled_messages(
        messages,
        run_id="run-1",
        task_id="task-1",
    )

    assert [msg.text for msg in remaining] == ["keep other run", "keep normal"]


def test_scheduled_run_helpers() -> None:
    assert scheduled_run_matches(
        ("run-1", "task-1"),
        run_id="run-1",
        task_id="task-1",
    )
    assert not scheduled_run_matches(
        None,
        run_id="run-1",
        task_id="task-1",
    )
    assert active_scheduled_task_id(("run-1", "task-1")) == "task-1"
    assert active_scheduled_task_id(None) is None


def test_should_deliver_scheduled_result() -> None:
    class Run:
        def __init__(self, finished_at: str | None) -> None:
            self.finished_at = finished_at

    assert should_deliver_scheduled_result(None)
    assert should_deliver_scheduled_result(Run(None))
    assert not should_deliver_scheduled_result(Run("2026-05-13T00:00:00+00:00"))


def test_resolve_scheduled_wecom_file_path_accepts_project_file(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("hello", encoding="utf-8")

    assert resolve_scheduled_wecom_file_path(report, cwd=tmp_path) == report.resolve()


def test_resolve_scheduled_wecom_file_path_rejects_empty_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing path"):
        resolve_scheduled_wecom_file_path("", cwd=tmp_path)


def test_resolve_scheduled_wecom_file_path_rejects_outside_project(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="limited to the current project"):
        resolve_scheduled_wecom_file_path(outside, cwd=root)


def test_resolve_scheduled_wecom_file_path_rejects_missing_file(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        resolve_scheduled_wecom_file_path(tmp_path / "missing.txt", cwd=tmp_path)
