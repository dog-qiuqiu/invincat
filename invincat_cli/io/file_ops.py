"""Helpers for tracking file operations and computing diffs for CLI display."""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from deepagents.backends.protocol import BackendProtocol

FileOpStatus = Literal["pending", "success", "error"]


@dataclass
class ApprovalPreview:
    """Data used to render HITL previews."""

    title: str
    details: list[str]
    diff: str | None = None
    diff_title: str | None = None
    error: str | None = None


def _safe_read(path: Path) -> str | None:
    """Read file content, returning None on failure.

    Returns:
        File content as string, or None if reading fails.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.debug("Failed to read file %s: %s", path, e)
        return None


def _count_lines(text: str) -> int:
    """Count lines in text, treating empty strings as zero lines.

    Returns:
        Number of lines in the text.
    """
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
    """Compute a unified diff between before and after content.

    Args:
        before: Original content
        after: New content
        display_path: Path for display in diff headers
        max_lines: Maximum number of diff lines (None for unlimited)
        context_lines: Number of context lines around changes (default 3)

    Returns:
        Unified diff string or None if no changes
    """
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


def resolve_physical_path(
    path_str: str | None, assistant_id: str | None
) -> Path | None:
    """Convert a virtual/relative path to a physical filesystem path.

    Returns:
        Resolved physical Path, or None if path is empty or resolution fails.
    """
    if not path_str:
        return None
    try:
        if assistant_id and path_str.startswith("/memories/"):
            from invincat_cli.config import settings

            agent_dir = settings.get_agent_dir(assistant_id)
            suffix = path_str.removeprefix("/memories/").lstrip("/")
            return (agent_dir / suffix).resolve()
        path = Path(path_str)
        if path.is_absolute():
            return path
        return (Path.cwd() / path).resolve()
    except (OSError, ValueError):
        return None


def format_display_path(path_str: str | None) -> str:
    """Format a path for display.

    Returns:
        Formatted path string suitable for display.
    """
    if not path_str:
        return "(unknown)"
    try:
        path = Path(path_str)
        if path.is_absolute():
            return path.name or str(path)
        return str(path)
    except (OSError, ValueError):
        return str(path_str)


def build_approval_preview(
    tool_name: str,
    args: dict[str, Any],
    assistant_id: str | None,
) -> ApprovalPreview | None:
    """Collect summary info and diff for HITL approvals.

    Returns:
        ApprovalPreview with diff and details, or None if tool not supported.
    """
    path_str = str(args.get("file_path") or args.get("path") or "")
    display_path = format_display_path(path_str)
    physical_path = resolve_physical_path(path_str, assistant_id)

    if tool_name == "write_file":
        content = str(args.get("content", ""))
        before = (
            _safe_read(physical_path)
            if physical_path and physical_path.exists()
            else ""
        )
        after = content
        diff = compute_unified_diff(before or "", after, display_path, max_lines=100)
        additions = 0
        if diff:
            additions = sum(
                1
                for line in diff.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
        total_lines = _count_lines(after)
        details = [
            f"File: {path_str}",
            "Action: Create new file"
            + (" (overwrites existing content)" if before else ""),
            f"Lines to write: {additions or total_lines}",
        ]
        return ApprovalPreview(
            title=f"Write {display_path}",
            details=details,
            diff=diff,
            diff_title=f"Diff {display_path}",
        )

    if tool_name == "edit_file":
        if physical_path is None:
            return ApprovalPreview(
                title=f"Update {display_path}",
                details=[f"File: {path_str}", "Action: Replace text"],
                error="Unable to resolve file path.",
            )
        before = _safe_read(physical_path)
        if before is None:
            return ApprovalPreview(
                title=f"Update {display_path}",
                details=[f"File: {path_str}", "Action: Replace text"],
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
                details=[f"File: {path_str}", "Action: Replace text"],
                error=replacement,
            )
        after, occurrences = replacement
        diff = compute_unified_diff(before, after, display_path, max_lines=None)
        additions = 0
        deletions = 0
        if diff:
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
        action = "all occurrences" if replace_all else "single occurrence"
        details = [
            f"File: {path_str}",
            f"Action: Replace text ({action})",
            f"Occurrences matched: {occurrences}",
            f"Lines changed: +{additions} / -{deletions}",
        ]
        return ApprovalPreview(
            title=f"Update {display_path}",
            details=details,
            diff=diff,
            diff_title=f"Diff {display_path}",
        )

    return None


class FileOpTracker:
    """Collect file operation metrics during a CLI interaction."""

    def __init__(
        self, *, assistant_id: str | None, backend: BackendProtocol | None = None
    ) -> None:
        """Initialize the tracker."""
        self.assistant_id = assistant_id
        self.backend = backend
        self.active: dict[str, FileOperationRecord] = {}
        self.completed: list[FileOperationRecord] = []

    def start_operation(
        self, tool_name: str, args: dict[str, Any], tool_call_id: str | None
    ) -> None:
        """Begin tracking a file operation.

        Creates a record for the operation and, for write/edit operations,
        captures the file's content before modification.
        """
        if tool_name not in {"read_file", "write_file", "edit_file"}:
            return
        # Normalize the key to str so that active dict lookups are type-safe.
        # If the caller passes None (buffer ID not yet received), use a unique
        # sentinel so we don't overwrite a previous None-keyed record.
        import uuid as _uuid

        normalized_id: str = (
            str(tool_call_id)
            if tool_call_id is not None
            else f"_unknown_{_uuid.uuid4().hex[:8]}"
        )
        path_str = str(args.get("file_path") or args.get("path") or "")
        display_path = format_display_path(path_str)
        record = FileOperationRecord(
            tool_name=tool_name,
            display_path=display_path,
            physical_path=resolve_physical_path(path_str, self.assistant_id),
            tool_call_id=normalized_id,
            args=args,
        )
        if tool_name in {"write_file", "edit_file"}:
            if self.backend and path_str:
                try:
                    responses = self.backend.download_files([path_str])
                    if (
                        responses
                        and responses[0].content is not None
                        and responses[0].error is None
                    ):
                        record.before_content = responses[0].content.decode("utf-8")
                    else:
                        record.before_content = ""
                except (OSError, UnicodeDecodeError, AttributeError) as e:
                    logger.debug(
                        "Failed to read before_content for %s: %s", path_str, e
                    )
                    record.before_content = ""
            elif record.physical_path:
                record.before_content = _safe_read(record.physical_path) or ""
        self.active[normalized_id] = record

    def complete_with_message(
        self, tool_message: Any, tool_args: dict[str, Any] | None = None
    ) -> FileOperationRecord | None:  # noqa: ANN401  # Tool message type is dynamic
        """Complete a file operation with the tool message result.

        Args:
            tool_message: The tool message result from the agent.
            tool_args: Optional tool arguments from the widget, used to match
                records when tool_call_id is not available. This helps
                distinguish between parallel calls to the same tool.

        Returns:
            The completed FileOperationRecord, or None if no matching operation.
        """
        raw_tool_call_id = getattr(tool_message, "tool_call_id", None)
        tool_name_from_msg = getattr(tool_message, "name", "") or ""

        # Normalize to str for consistent lookup — active keys are always str
        # since start_operation normalizes at write time.
        tool_call_id: str | None = (
            str(raw_tool_call_id) if raw_tool_call_id is not None else None
        )

        # Strategy 1: exact normalized match (covers the normal case).
        # Does NOT pop from self.active; _finalize() handles removal so that
        # the record stays visible for parallel callers until fully complete.
        record = self.active.get(tool_call_id) if tool_call_id else None
        # Track whether Strategy 2 already removed the record from self.active
        # so _finalize() knows to skip its own pop (see _finalize docstring).
        _removed_from_active = False

        # Strategy 2: tool-name + path fallback — triggered when the tracker was keyed
        # by an index string (e.g. "0") because buffer_id had not arrived yet by
        # the time args were finalized. Uses file path to distinguish between
        # parallel calls to the same tool.
        if record is None and tool_name_from_msg:
            # Extract file path from tool_args if available
            path_from_args = None
            if tool_args:
                path_from_args = tool_args.get("file_path") or tool_args.get("path")

            # Find all matching records
            matching_records: list[tuple[str, FileOperationRecord]] = []
            for key, r in self.active.items():
                if r.tool_name == tool_name_from_msg:
                    matching_records.append((key, r))

            if len(matching_records) == 1:
                # Single match: use it directly
                matched_key, record = matching_records[0]
                self.active.pop(matched_key, None)
                _removed_from_active = True
                if tool_call_id is not None and record.tool_call_id != tool_call_id:
                    logger.debug(
                        "Updating record tool_call_id from %s to %s",
                        record.tool_call_id,
                        tool_call_id,
                    )
                    record.tool_call_id = tool_call_id
                logger.debug(
                    "complete_with_message: matched record by tool_name=%s (key=%s)",
                    tool_name_from_msg,
                    matched_key,
                )
            elif len(matching_records) > 1 and path_from_args:
                # Multiple matches: try to distinguish by file path
                for key, r in matching_records:
                    record_path = r.args.get("file_path") or r.args.get("path")
                    if record_path == path_from_args:
                        record = r
                        self.active.pop(key, None)
                        _removed_from_active = True
                        if tool_call_id is not None and record.tool_call_id != tool_call_id:
                            record.tool_call_id = tool_call_id
                        logger.debug(
                            "complete_with_message: matched record by tool_name=%s path=%s (key=%s)",
                            tool_name_from_msg,
                            path_from_args,
                            key,
                        )
                        break

                if record is None:
                    logger.debug(
                        "complete_with_message: multiple matches for tool_name=%s, "
                        "path=%s not found in any record (keys: %s)",
                        tool_name_from_msg,
                        path_from_args,
                        [k for k, _ in matching_records],
                    )
            elif len(matching_records) > 1:
                # Multiple matches without path info: cannot distinguish
                logger.debug(
                    "complete_with_message: multiple matches for tool_name=%s, "
                    "no path info available to distinguish (keys: %s)",
                    tool_name_from_msg,
                    [k for k, _ in matching_records],
                )

        if record is None:
            return None

        content = tool_message.content
        if isinstance(content, list):
            # Some tool messages may return list segments; join them for analysis.
            joined = []
            for item in content:
                if isinstance(item, str):
                    joined.append(item)
                else:
                    joined.append(str(item))
            content_text = "\n".join(joined)
        else:
            content_text = str(content) if content is not None else ""

        if getattr(
            tool_message, "status", "success"
        ) != "success" or content_text.lower().startswith("error"):
            record.status = "error"
            record.error = content_text
            self._finalize(record, already_removed=_removed_from_active)
            return record

        record.status = "success"

        if record.tool_name == "read_file":
            record.read_output = content_text
            lines = _count_lines(content_text)
            record.metrics.lines_read = lines
            offset = record.args.get("offset")
            limit = record.args.get("limit")
            if isinstance(offset, int):
                if offset > lines:
                    offset = 0
                record.metrics.start_line = offset + 1
                if lines:
                    record.metrics.end_line = offset + lines
            elif lines:
                record.metrics.start_line = 1
                record.metrics.end_line = lines
            if isinstance(limit, int) and lines > limit:
                record.metrics.end_line = (record.metrics.start_line or 1) + limit - 1
        else:
            # For write/edit operations, read back from backend (or local filesystem)
            self._populate_after_content(record)
            if record.after_content is None:
                record.status = "error"
                record.error = "Could not read updated file content."
                self._finalize(record, already_removed=_removed_from_active)
                return record
            record.metrics.lines_written = _count_lines(record.after_content)

            # before_content should always be populated by start_operation() for
            # write/edit tools (set to content or "" on read failure).  If it is
            # still None here, start_operation() was either skipped or the record
            # was constructed manually — treat as empty but warn so the gap is
            # visible in logs.  An empty before_content for edit_file will cause
            # the diff to look like the entire file was newly added, which is
            # misleading.
            if record.before_content is None:
                logger.warning(
                    "before_content is None for tool=%s path=%s tool_call_id=%s; "
                    "start_operation() may have been skipped — "
                    "diff and line metrics will be inaccurate",
                    record.tool_name,
                    record.display_path,
                    record.tool_call_id,
                )
            before_lines = _count_lines(record.before_content or "")
            
            logger.debug(
                "Generating diff: tool=%s, before_lines=%s, after_lines=%s, "
                "before_len=%s, after_len=%s",
                record.tool_name,
                before_lines,
                record.metrics.lines_written,
                len(record.before_content or ""),
                len(record.after_content),
            )
            
            diff = compute_unified_diff(
                record.before_content or "",
                record.after_content,
                record.display_path,
                max_lines=100,
            )
            record.diff = diff
            
            logger.debug(
                "Diff generated: length=%s, empty=%s",
                len(diff) if diff else 0,
                not diff or diff.strip() == "",
            )
            if diff:
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
                record.metrics.lines_added = additions
                record.metrics.lines_removed = deletions
            elif record.tool_name == "write_file" and not (record.before_content or ""):
                record.metrics.lines_added = record.metrics.lines_written
            record.metrics.bytes_written = len(record.after_content.encode("utf-8"))
            # Note: a second `compute_unified_diff` attempt here would be
            # dead code — `compute_unified_diff` returns None only when
            # before == after (unified_diff produces no lines), so if
            # before != after the first call always returns a non-None diff.
            # The lines_added fallback below is therefore also unreachable,
            # but kept as a defensive safety-net comment for clarity.

        self._finalize(record, already_removed=_removed_from_active)
        return record

    def mark_hitl_approved(self, tool_name: str, args: dict[str, Any]) -> None:
        """Mark operations matching tool_name and file_path as HIL-approved."""
        file_path = args.get("file_path") or args.get("path")
        if not file_path:
            return

        # Mark all active records that match
        for record in self.active.values():
            if record.tool_name == tool_name:
                record_path = record.args.get("file_path") or record.args.get("path")
                if record_path == file_path:
                    record.hitl_approved = True

    def _populate_after_content(self, record: FileOperationRecord) -> None:
        logger.debug(
            "_populate_after_content: tool=%s, path=%s, physical_path=%s, backend=%s",
            record.tool_name,
            record.args.get("file_path") or record.args.get("path"),
            record.physical_path,
            "available" if self.backend else "not available",
        )
        
        # Try backend first if available (works for any BackendProtocol implementation)
        if self.backend:
            try:
                file_path = record.args.get("file_path") or record.args.get("path")
                if file_path:
                    logger.debug("Attempting backend download for: %s", file_path)
                    responses = self.backend.download_files([file_path])
                    if (
                        responses
                        and responses[0].content is not None
                        and responses[0].error is None
                    ):
                        record.after_content = responses[0].content.decode("utf-8")
                        logger.debug(
                            "Backend download successful, content length: %s",
                            len(record.after_content),
                        )
                        return  # Success via backend
                    else:
                        logger.debug(
                            "Backend download failed for %s (responses: %s), trying local filesystem",
                            file_path,
                            responses,
                        )
            except (OSError, UnicodeDecodeError, AttributeError) as e:
                logger.debug(
                    "Backend read failed for %s: %s, trying local filesystem",
                    record.args.get("file_path") or record.args.get("path"),
                    e,
                    exc_info=True,
                )

        # Fallback: direct filesystem read (when no backend or backend failed)
        if record.physical_path is None:
            logger.debug(
                "No physical_path for %s, cannot read from local filesystem",
                record.args.get("file_path") or record.args.get("path"),
            )
            record.after_content = None
            return

        record.after_content = _safe_read(record.physical_path)
        if record.after_content is not None:
            logger.debug(
                "Successfully read after_content from local filesystem: %s",
                record.physical_path,
            )

    def _finalize(
        self, record: FileOperationRecord, *, already_removed: bool = False
    ) -> None:
        """Append record to completed and remove it from active.

        Args:
            record: The completed operation record.
            already_removed: When True, the record was already popped from
                ``self.active`` by the caller (e.g. Strategy 2 in
                ``complete_with_message``).  Skip the redundant pop so we
                don't silently discard a different record that happens to
                share the same ``tool_call_id`` value.
        """
        self.completed.append(record)
        if not already_removed:
            self.active.pop(record.tool_call_id, None)
