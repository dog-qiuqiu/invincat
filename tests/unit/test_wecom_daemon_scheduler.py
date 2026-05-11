"""Tests for WeCom daemon scheduler task filtering."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from invincat_cli.wecom.daemon import (
    _scheduled_task_wecom_chatid,
    _task_visible_to_wecom_daemon,
)


def _task(cwd: Path, channels: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        cwd=str(cwd),
        delivery=SimpleNamespace(channels=channels),
    )


def test_wecom_daemon_ignores_tui_only_scheduled_tasks(tmp_path: Path) -> None:
    task = _task(tmp_path, [{"type": "tui"}])

    assert _scheduled_task_wecom_chatid(task) == ""
    assert _task_visible_to_wecom_daemon(task, tmp_path) is False


def test_wecom_daemon_ignores_empty_wecom_targets(tmp_path: Path) -> None:
    task = _task(tmp_path, [{"type": "wecom", "chatid": "  "}])

    assert _scheduled_task_wecom_chatid(task) == ""
    assert _task_visible_to_wecom_daemon(task, tmp_path) is False


def test_wecom_daemon_accepts_wecom_deliverable_tasks(tmp_path: Path) -> None:
    task = _task(
        tmp_path,
        [
            {"type": "tui"},
            {"type": "wecom", "chatid": " chat-1 "},
        ],
    )

    assert _scheduled_task_wecom_chatid(task) == "chat-1"
    assert _task_visible_to_wecom_daemon(task, tmp_path) is True


def test_wecom_daemon_rejects_tasks_from_other_cwd(tmp_path: Path) -> None:
    other_cwd = tmp_path / "other"
    task = _task(other_cwd, [{"type": "wecom", "chatid": "chat-1"}])

    assert _scheduled_task_wecom_chatid(task) == "chat-1"
    assert _task_visible_to_wecom_daemon(task, tmp_path) is False
