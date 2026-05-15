"""Runtime tracking for file operation tool calls."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from invincat_cli.io.file_op_content import (
    populate_after_content,
    read_before_content,
)
from invincat_cli.io.file_op_diff import count_diff_changes
from invincat_cli.io.file_op_models import FileOperationRecord

if TYPE_CHECKING:
    from deepagents.backends.protocol import BackendProtocol

logger = logging.getLogger(__name__)


def _file_ops_facade():
    from invincat_cli.io import file_ops

    return file_ops


def _normalize_tool_call_id(tool_call_id: str | None) -> str:
    if tool_call_id is not None:
        return str(tool_call_id)
    return f"_unknown_{uuid.uuid4().hex[:8]}"


def _tool_content_to_text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(item if isinstance(item, str) else str(item) for item in content)
    return str(content) if content is not None else ""


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
        """Begin tracking a file operation."""
        if tool_name not in {"read_file", "write_file", "edit_file"}:
            return

        file_ops = _file_ops_facade()
        normalized_id = _normalize_tool_call_id(tool_call_id)
        path_str = str(args.get("file_path") or args.get("path") or "")
        record = FileOperationRecord(
            tool_name=tool_name,
            display_path=file_ops.format_display_path(path_str),
            physical_path=file_ops.resolve_physical_path(path_str, self.assistant_id),
            tool_call_id=normalized_id,
            args=args,
        )
        if tool_name in {"write_file", "edit_file"}:
            record.before_content = read_before_content(
                self.backend, record, path_str, file_ops._safe_read
            )
        self.active[normalized_id] = record

    def complete_with_message(
        self, tool_message: Any, tool_args: dict[str, Any] | None = None
    ) -> FileOperationRecord | None:  # noqa: ANN401  # Tool message type is dynamic
        """Complete a file operation with the tool message result."""
        raw_tool_call_id = getattr(tool_message, "tool_call_id", None)
        tool_name = getattr(tool_message, "name", "") or ""
        tool_call_id = str(raw_tool_call_id) if raw_tool_call_id is not None else None

        record = self.active.get(tool_call_id) if tool_call_id else None
        already_removed = False
        if record is None and tool_name:
            record, already_removed = self._match_fallback(
                tool_name, tool_call_id, tool_args
            )
        if record is None:
            return None

        content_text = _tool_content_to_text(tool_message.content)
        if (
            getattr(tool_message, "status", "success") != "success"
            or content_text.lower().startswith("error")
        ):
            record.status = "error"
            record.error = content_text
            self._finalize(record, already_removed=already_removed)
            return record

        record.status = "success"
        if record.tool_name == "read_file":
            self._complete_read(record, content_text)
        else:
            self._complete_write_or_edit(record)
            if record.status == "error":
                self._finalize(record, already_removed=already_removed)
                return record

        self._finalize(record, already_removed=already_removed)
        return record

    def mark_hitl_approved(self, tool_name: str, args: dict[str, Any]) -> None:
        """Mark operations matching tool_name and file_path as HIL-approved."""
        file_path = args.get("file_path") or args.get("path")
        if not file_path:
            return
        for record in self.active.values():
            record_path = record.args.get("file_path") or record.args.get("path")
            if record.tool_name == tool_name and record_path == file_path:
                record.hitl_approved = True

    def _match_fallback(
        self,
        tool_name: str,
        tool_call_id: str | None,
        tool_args: dict[str, Any] | None,
    ) -> tuple[FileOperationRecord | None, bool]:
        path_from_args = (
            tool_args.get("file_path") or tool_args.get("path") if tool_args else None
        )
        matching_records = [
            (key, record)
            for key, record in self.active.items()
            if record.tool_name == tool_name
        ]

        if len(matching_records) == 1:
            key, record = matching_records[0]
            self.active.pop(key, None)
            self._sync_tool_call_id(record, tool_call_id)
            logger.debug(
                "complete_with_message: matched record by tool_name=%s (key=%s)",
                tool_name,
                key,
            )
            return record, True

        if len(matching_records) > 1 and path_from_args:
            for key, record in matching_records:
                record_path = record.args.get("file_path") or record.args.get("path")
                if record_path == path_from_args:
                    self.active.pop(key, None)
                    self._sync_tool_call_id(record, tool_call_id)
                    logger.debug(
                        "complete_with_message: matched record by tool_name=%s path=%s (key=%s)",
                        tool_name,
                        path_from_args,
                        key,
                    )
                    return record, True
            logger.debug(
                "complete_with_message: multiple matches for tool_name=%s, "
                "path=%s not found in any record (keys: %s)",
                tool_name,
                path_from_args,
                [key for key, _ in matching_records],
            )
        elif len(matching_records) > 1:
            logger.debug(
                "complete_with_message: multiple matches for tool_name=%s, "
                "no path info available to distinguish (keys: %s)",
                tool_name,
                [key for key, _ in matching_records],
            )
        return None, False

    def _sync_tool_call_id(
        self, record: FileOperationRecord, tool_call_id: str | None
    ) -> None:
        if tool_call_id is not None and record.tool_call_id != tool_call_id:
            logger.debug(
                "Updating record tool_call_id from %s to %s",
                record.tool_call_id,
                tool_call_id,
            )
            record.tool_call_id = tool_call_id

    def _complete_read(self, record: FileOperationRecord, content_text: str) -> None:
        record.read_output = content_text
        lines = _file_ops_facade()._count_lines(content_text)
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

    def _complete_write_or_edit(self, record: FileOperationRecord) -> None:
        populate_after_content(self.backend, record, _file_ops_facade()._safe_read)
        if record.after_content is None:
            record.status = "error"
            record.error = "Could not read updated file content."
            return

        file_ops = _file_ops_facade()
        record.metrics.lines_written = file_ops._count_lines(record.after_content)
        if record.before_content is None:
            logger.warning(
                "before_content is None for tool=%s path=%s tool_call_id=%s; "
                "start_operation() may have been skipped — "
                "diff and line metrics will be inaccurate",
                record.tool_name,
                record.display_path,
                record.tool_call_id,
            )

        before = record.before_content or ""
        logger.debug(
            "Generating diff: tool=%s, before_lines=%s, after_lines=%s, "
            "before_len=%s, after_len=%s",
            record.tool_name,
            file_ops._count_lines(before),
            record.metrics.lines_written,
            len(before),
            len(record.after_content),
        )
        record.diff = file_ops.compute_unified_diff(
            before, record.after_content, record.display_path, max_lines=100
        )
        logger.debug(
            "Diff generated: length=%s, empty=%s",
            len(record.diff) if record.diff else 0,
            not record.diff or record.diff.strip() == "",
        )

        additions, deletions = count_diff_changes(record.diff)
        record.metrics.lines_added = additions
        record.metrics.lines_removed = deletions
        if (
            not record.diff
            and record.tool_name == "write_file"
            and not record.before_content
        ):
            record.metrics.lines_added = record.metrics.lines_written
        record.metrics.bytes_written = len(record.after_content.encode("utf-8"))

    def _populate_after_content(self, record: FileOperationRecord) -> None:
        """Compatibility wrapper for tests and older internal callers."""
        populate_after_content(self.backend, record, _file_ops_facade()._safe_read)

    def _finalize(
        self, record: FileOperationRecord, *, already_removed: bool = False
    ) -> None:
        """Append record to completed and remove it from active."""
        self.completed.append(record)
        if not already_removed:
            self.active.pop(record.tool_call_id, None)
