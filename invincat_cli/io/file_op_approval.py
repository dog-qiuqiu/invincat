"""Approval preview construction for file operation tools."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from invincat_cli.io.file_op_diff import count_diff_changes, count_lines
from invincat_cli.io.file_op_models import ApprovalPreview


def build_approval_preview(
    tool_name: str,
    args: dict[str, Any],
    assistant_id: str | None,
    *,
    safe_read: Callable[[Path], str | None],
    resolve_path: Callable[[str | None, str | None], Path | None],
    format_path: Callable[[str | None], str],
    compute_diff: Callable[..., str | None],
) -> ApprovalPreview | None:
    """Collect summary info and diff for HITL approvals."""
    path_str = str(args.get("file_path") or args.get("path") or "")
    display_path = format_path(path_str)
    physical_path = resolve_path(path_str, assistant_id)

    if tool_name == "write_file":
        return _build_write_preview(
            args, path_str, display_path, physical_path, safe_read, compute_diff
        )
    if tool_name == "edit_file":
        return _build_edit_preview(
            args, path_str, display_path, physical_path, safe_read, compute_diff
        )
    return None


def _build_write_preview(
    args: dict[str, Any],
    path_str: str,
    display_path: str,
    physical_path: Path | None,
    safe_read: Callable[[Path], str | None],
    compute_diff: Callable[..., str | None],
) -> ApprovalPreview:
    content = str(args.get("content", ""))
    before = safe_read(physical_path) if physical_path and physical_path.exists() else ""
    diff = compute_diff(before or "", content, display_path, max_lines=100)
    additions, _ = count_diff_changes(diff)
    details = [
        f"File: {path_str}",
        "Action: Create new file"
        + (" (overwrites existing content)" if before else ""),
        f"Lines to write: {additions or count_lines(content)}",
    ]
    return ApprovalPreview(
        title=f"Write {display_path}",
        details=details,
        diff=diff,
        diff_title=f"Diff {display_path}",
    )


def _build_edit_preview(
    args: dict[str, Any],
    path_str: str,
    display_path: str,
    physical_path: Path | None,
    safe_read: Callable[[Path], str | None],
    compute_diff: Callable[..., str | None],
) -> ApprovalPreview:
    details = [f"File: {path_str}", "Action: Replace text"]
    if physical_path is None:
        return ApprovalPreview(
            title=f"Update {display_path}",
            details=details,
            error="Unable to resolve file path.",
        )

    before = safe_read(physical_path)
    if before is None:
        return ApprovalPreview(
            title=f"Update {display_path}",
            details=details,
            error="Unable to read current file contents.",
        )

    old_string = str(args.get("old_string", ""))
    new_string = str(args.get("new_string", ""))
    replace_all = bool(args.get("replace_all"))
    from deepagents.backends.utils import perform_string_replacement

    replacement = perform_string_replacement(
        before, old_string, new_string, replace_all
    )
    if isinstance(replacement, str):
        return ApprovalPreview(
            title=f"Update {display_path}",
            details=details,
            error=replacement,
        )

    after, occurrences = replacement
    diff = compute_diff(before, after, display_path, max_lines=None)
    additions, deletions = count_diff_changes(diff)
    action = "all occurrences" if replace_all else "single occurrence"
    return ApprovalPreview(
        title=f"Update {display_path}",
        details=[
            f"File: {path_str}",
            f"Action: Replace text ({action})",
            f"Occurrences matched: {occurrences}",
            f"Lines changed: +{additions} / -{deletions}",
        ],
        diff=diff,
        diff_title=f"Diff {display_path}",
    )
