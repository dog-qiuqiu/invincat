"""Data models for the scheduler subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


MisfirePolicy = Literal["skip", "run_once"]
TaskStatus = Literal["never", "success", "failed", "running", "missed", "timeout"]


@dataclass
class DeliverySpec:
    channels: list[dict] = field(default_factory=lambda: [{"type": "tui"}])


@dataclass
class ReportSpec:
    output_dir: str = "reports"
    filename_template: str = "{task_slug}-{date}.md"
    format: str = "markdown"


@dataclass
class ScheduledTask:
    id: str
    title: str
    enabled: bool
    prompt: str
    cron: str
    """Normalised cron expression (5-field)."""
    timezone: str
    cwd: str
    delivery: DeliverySpec
    report: ReportSpec
    created_at: str
    updated_at: str
    next_run_at: str | None
    last_run_at: str | None
    last_status: TaskStatus
    last_error: str | None
    run_count: int
    failure_count: int
    misfire_policy: MisfirePolicy = "run_once"
    timeout_seconds: int = 600
    """Maximum seconds a scheduled run may take before being marked timeout."""


@dataclass
class TaskRun:
    id: str
    task_id: str
    scheduled_for: str
    started_at: str | None
    finished_at: str | None
    status: TaskStatus
    report_path: str | None
    error: str | None
    thread_id: str | None
    cwd: str
