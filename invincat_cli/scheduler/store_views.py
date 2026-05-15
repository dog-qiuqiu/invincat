"""Scoped and filtered scheduler store views."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from invincat_cli.scheduler.store import SchedulerStore

if TYPE_CHECKING:
    from invincat_cli.scheduler.models import ScheduledTask

logger = logging.getLogger(__name__)


class CwdScopedSchedulerStore(SchedulerStore):
    """SchedulerStore view that hides tasks outside one working directory."""

    def __init__(self, cwd: str | Path, db_path: Path | None = None) -> None:
        self._scope_cwd = str(cwd)
        super().__init__(db_path=db_path)

    def list_tasks(
        self,
        *,
        enabled_only: bool = False,
        cwd: str | None = None,
    ) -> list[ScheduledTask]:
        if cwd is not None and cwd != self._scope_cwd:
            return []
        return super().list_tasks(enabled_only=enabled_only, cwd=self._scope_cwd)

    def load_task(self, task_id: str) -> ScheduledTask | None:
        task = super().load_task(task_id)
        if task is None or task.cwd != self._scope_cwd:
            return None
        return task


class FilteredSchedulerStore(SchedulerStore):
    """SchedulerStore view that excludes matching tasks from runner claims."""

    def __init__(
        self,
        *,
        exclude_task: Callable[[Any], bool],
        db_path: Path | None = None,
    ) -> None:
        self._exclude_task = exclude_task
        super().__init__(db_path=db_path)

    def _is_excluded(self, task: Any) -> bool:
        try:
            return bool(self._exclude_task(task))
        except Exception:
            logger.warning("Scheduler task filter failed", exc_info=True)
            return False

    def list_tasks(
        self,
        *,
        enabled_only: bool = False,
        cwd: str | None = None,
    ) -> list[ScheduledTask]:
        return [
            task
            for task in super().list_tasks(enabled_only=enabled_only, cwd=cwd)
            if not self._is_excluded(task)
        ]

    def try_start_run(self, task_id: str, run: Any, **kwargs: Any) -> bool:
        task = super().load_task(task_id)
        if task is not None and self._is_excluded(task):
            return False
        return super().try_start_run(task_id, run, **kwargs)
