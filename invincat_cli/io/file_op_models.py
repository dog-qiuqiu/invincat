"""Data models for tracked file operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

FileOpStatus = Literal["pending", "success", "error"]


@dataclass
class ApprovalPreview:
    """Data used to render HITL previews."""

    title: str
    details: list[str]
    diff: str | None = None
    diff_title: str | None = None
    error: str | None = None


@dataclass
class FileOpMetrics:
    """Line and byte level metrics for a file operation."""

    lines_read: int = 0
    start_line: int | None = None
    end_line: int | None = None
    lines_written: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    bytes_written: int = 0


@dataclass
class FileOperationRecord:
    """Track a single filesystem tool call."""

    tool_name: str
    display_path: str
    physical_path: Path | None
    tool_call_id: str | None
    args: dict[str, Any] = field(default_factory=dict)
    status: FileOpStatus = "pending"
    error: str | None = None
    metrics: FileOpMetrics = field(default_factory=FileOpMetrics)
    diff: str | None = None
    before_content: str | None = None
    after_content: str | None = None
    read_output: str | None = None
    hitl_approved: bool = False
