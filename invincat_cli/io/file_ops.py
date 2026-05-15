"""Compatibility facade for file operation tracking and diff helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from invincat_cli.io.file_op_approval import (
    build_approval_preview as _build_approval_preview_impl,
)
from invincat_cli.io.file_op_diff import (
    compute_unified_diff,
)
from invincat_cli.io.file_op_diff import (
    count_lines as _count_lines_impl,
)
from invincat_cli.io.file_op_models import (
    ApprovalPreview,
    FileOperationRecord,
    FileOpMetrics,
    FileOpStatus,
)
from invincat_cli.io.file_op_paths import (
    format_display_path as _format_display_path_impl,
)
from invincat_cli.io.file_op_paths import (
    resolve_physical_path as _resolve_physical_path_impl,
)
from invincat_cli.io.file_op_paths import (
    safe_read as _safe_read_impl,
)
from invincat_cli.io.file_op_tracker import FileOpTracker


def _safe_read(path: Path) -> str | None:
    """Read file content, returning None on failure."""
    return _safe_read_impl(path)


def _count_lines(text: str) -> int:
    """Count lines in text, treating empty strings as zero lines."""
    return _count_lines_impl(text)


def resolve_physical_path(
    path_str: str | None, assistant_id: str | None
) -> Path | None:
    """Convert a virtual/relative path to a physical filesystem path."""
    return _resolve_physical_path_impl(path_str, assistant_id, path_cls=Path)


def format_display_path(path_str: str | None) -> str:
    """Format a path for display."""
    return _format_display_path_impl(path_str, path_cls=Path)


def build_approval_preview(
    tool_name: str,
    args: dict[str, Any],
    assistant_id: str | None,
) -> ApprovalPreview | None:
    """Collect summary info and diff for HITL approvals."""
    return _build_approval_preview_impl(
        tool_name,
        args,
        assistant_id,
        safe_read=_safe_read,
        resolve_path=resolve_physical_path,
        format_path=format_display_path,
        compute_diff=compute_unified_diff,
    )


__all__ = [
    "ApprovalPreview",
    "FileOperationRecord",
    "FileOpMetrics",
    "FileOpStatus",
    "FileOpTracker",
    "_count_lines",
    "_safe_read",
    "build_approval_preview",
    "compute_unified_diff",
    "format_display_path",
    "resolve_physical_path",
]
