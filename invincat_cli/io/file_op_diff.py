"""Diff and line-count helpers for file operation summaries."""

from __future__ import annotations

import difflib


def count_lines(text: str) -> int:
    """Count lines in text, treating empty strings as zero lines."""
    if not text:
        return 0
    return len(text.splitlines())


def compute_unified_diff(
    before: str,
    after: str,
    display_path: str,
    *,
    max_lines: int | None = 800,
    context_lines: int = 3,
) -> str | None:
    """Compute a unified diff between before and after content."""
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    diff_lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"{display_path} (before)",
            tofile=f"{display_path} (after)",
            lineterm="",
            n=context_lines,
        )
    )
    if not diff_lines:
        return None
    if max_lines is not None and len(diff_lines) > max_lines:
        truncated = diff_lines[: max_lines - 1]
        truncated.append("...")
        return "\n".join(truncated)
    return "\n".join(diff_lines)


def count_diff_changes(diff: str | None) -> tuple[int, int]:
    """Return added and removed line counts from a unified diff."""
    if not diff:
        return 0, 0
    additions = sum(
        1
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    deletions = sum(
        1
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    return additions, deletions
