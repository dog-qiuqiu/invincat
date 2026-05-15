"""Unit tests for file operations module."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import invincat_cli.io.file_ops as file_ops_module
from invincat_cli.io.file_ops import (
    ApprovalPreview,
    FileOperationRecord,
    FileOpTracker,
    _count_lines,
    _safe_read,
    build_approval_preview,
    compute_unified_diff,
    format_display_path,
    resolve_physical_path,
)


class TestComputeUnifiedDiff:
    """Tests for compute_unified_diff function."""

    def test_no_changes(self):
        """Test diff with identical content returns None."""
        before = "line1\nline2\nline3"
        after = "line1\nline2\nline3"
        result = compute_unified_diff(before, after, "test.txt")
        assert result is None

    def test_single_line_change(self):
        """Test diff with single line change."""
        before = "line1\nline2\nline3"
        after = "line1\nline2_changed\nline3"
        result = compute_unified_diff(before, after, "test.txt")
        assert result is not None
        assert "line2" in result
        assert "line2_changed" in result

    def test_truncation(self):
        """Test diff truncation with max_lines."""
        before = "\n".join([f"line{i}" for i in range(1000)])
        after = "\n".join([f"line{i}_changed" for i in range(1000)])
        result = compute_unified_diff(before, after, "test.txt", max_lines=100)
        assert result is not None
        assert "..." in result  # Should be truncated
        lines = result.splitlines()
        assert len(lines) <= 100


def test_safe_read_and_count_lines(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("one\ntwo")

    assert _safe_read(path) == "one\ntwo"
    assert _safe_read(tmp_path / "missing.txt") is None
    assert _count_lines("") == 0
    assert _count_lines("one\ntwo\n") == 2


def test_build_approval_preview_for_write_file_create_and_overwrite(tmp_path):
    new_path = tmp_path / "new.txt"

    preview = build_approval_preview(
        "write_file",
        {"file_path": str(new_path), "content": "hello\nworld"},
        assistant_id=None,
    )

    assert isinstance(preview, ApprovalPreview)
    assert preview.title == "Write new.txt"
    assert "Lines to write: 2" in preview.details
    assert preview.diff is not None

    new_path.write_text("old\n")
    preview = build_approval_preview(
        "write_file",
        {"file_path": str(new_path), "content": "new\n"},
        assistant_id=None,
    )

    assert preview is not None
    assert "overwrites existing content" in preview.details[1]
    assert "-old" in (preview.diff or "")
    assert "+new" in (preview.diff or "")


def test_build_approval_preview_for_edit_file_success_and_errors(tmp_path):
    path = tmp_path / "edit.txt"
    path.write_text("hello\nhello\n")

    preview = build_approval_preview(
        "edit_file",
        {
            "file_path": str(path),
            "old_string": "hello",
            "new_string": "hi",
            "replace_all": True,
        },
        assistant_id=None,
    )

    assert preview is not None
    assert preview.error is None
    assert "Occurrences matched: 2" in preview.details
    assert "+hi" in (preview.diff or "")

    missing = build_approval_preview(
        "edit_file",
        {
            "file_path": str(tmp_path / "missing.txt"),
            "old_string": "x",
            "new_string": "y",
        },
        assistant_id=None,
    )
    assert missing is not None
    assert missing.error == "Unable to read current file contents."

    no_match = build_approval_preview(
        "edit_file",
        {
            "file_path": str(path),
            "old_string": "missing",
            "new_string": "new",
        },
        assistant_id=None,
    )
    assert no_match is not None
    assert no_match.error

    unresolved = build_approval_preview(
        "edit_file",
        {"file_path": ""},
        assistant_id=None,
    )
    assert unresolved is not None
    assert unresolved.error == "Unable to resolve file path."

    unchanged = build_approval_preview(
        "unknown_tool",
        {"file_path": str(path)},
        assistant_id=None,
    )
    assert unchanged is None


def test_resolve_physical_path_maps_memory_paths(monkeypatch, tmp_path):
    agent_dir = tmp_path / "agent"
    monkeypatch.setattr(
        "invincat_cli.config.settings.get_agent_dir",
        lambda assistant_id: agent_dir,
    )

    assert (
        resolve_physical_path("/memories/facts.md", "assistant")
        == (agent_dir / "facts.md").resolve()
    )


def test_resolve_and_format_path_error_fallbacks(monkeypatch):
    monkeypatch.setattr(
        file_ops_module.Path,
        "cwd",
        lambda: (_ for _ in ()).throw(OSError("cwd missing")),
    )
    assert resolve_physical_path("relative.txt", "assistant") is None

    class RaisingPath:
        def __init__(self, _value):
            raise ValueError("bad path")

    monkeypatch.setattr(file_ops_module, "Path", RaisingPath)
    assert format_display_path("bad") == "bad"


class TestPathFunctions:
    """Tests for path-related functions."""

    def test_format_display_path(self):
        """Test formatting paths for display."""
        # Absolute path shows only filename
        assert format_display_path("/home/user/file.txt") == "file.txt"

        # Relative path shows as-is
        assert format_display_path("relative/file.txt") == "relative/file.txt"

        # None/empty returns placeholder
        assert format_display_path(None) == "(unknown)"
        assert format_display_path("") == "(unknown)"

        # Invalid path returns string representation (may be truncated)
        result = format_display_path("/invalid/\x00path")
        assert "\x00path" in result  # May be truncated but should contain part of path

    def test_resolve_physical_path(self):
        """Test path resolution."""
        # Absolute path returns as-is
        abs_path = "/absolute/path"
        result = resolve_physical_path(abs_path, "assistant1")
        assert str(result) == abs_path

        # Relative path resolves from cwd (using os.chdir instead of Path.chdir)
        with tempfile.TemporaryDirectory() as tmpdir:
            import os

            cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                result = resolve_physical_path("relative.txt", "assistant1")
                assert result == (Path(tmpdir) / "relative.txt").resolve()
            finally:
                os.chdir(cwd)

        # None/empty returns None
        assert resolve_physical_path(None, "assistant1") is None
        assert resolve_physical_path("", "assistant1") is None


class TestFileOpTracker:
    """Tests for FileOpTracker class."""

    def test_init(self):
        """Test tracker initialization."""
        tracker = FileOpTracker(assistant_id="test", backend=None)
        assert tracker.assistant_id == "test"
        assert tracker.backend is None
        assert tracker.active == {}
        assert tracker.completed == []

    def test_start_operation(self):
        """Test starting file operation tracking."""
        tracker = FileOpTracker(assistant_id="test", backend=None)
        args = {"file_path": "/test/file.txt", "content": "test content"}

        tracker.start_operation("write_file", args, "call1")

        assert len(tracker.active) == 1
        record = list(tracker.active.values())[0]
        assert record.tool_name == "write_file"
        assert record.display_path == "file.txt"
        assert record.status == "pending"

    def test_start_operation_captures_backend_before_content_and_errors(self):
        backend = Mock()
        backend.download_files.return_value = [Mock(content=b"before\n", error=None)]
        tracker = FileOpTracker(assistant_id="test", backend=backend)
        tracker.start_operation("write_file", {"file_path": "/remote.txt"}, "call1")
        assert tracker.active["call1"].before_content == "before\n"

        backend.download_files.return_value = [Mock(content=None, error="missing")]
        tracker.start_operation("write_file", {"file_path": "/missing.txt"}, "call2")
        assert tracker.active["call2"].before_content == ""

        backend.download_files.side_effect = OSError("backend down")
        tracker.start_operation("write_file", {"file_path": "/error.txt"}, "call3")
        assert tracker.active["call3"].before_content == ""

    def test_unsupported_tool_ignored(self):
        """Test that unsupported tools are ignored."""
        tracker = FileOpTracker(assistant_id="test", backend=None)
        args = {"file_path": "/test/file.txt"}

        tracker.start_operation("unsupported_tool", args, "call1")

        assert len(tracker.active) == 0

    @patch("invincat_cli.io.file_ops._safe_read")
    def test_complete_read_operation(self, mock_safe_read):
        """Test completing a read file operation."""
        tracker = FileOpTracker(assistant_id="test", backend=None)
        args = {"file_path": "/test/file.txt", "offset": 0, "limit": 100}

        tracker.start_operation("read_file", args, "call1")

        # Mock tool message
        tool_message = Mock()
        tool_message.tool_call_id = "call1"
        tool_message.name = "read_file"
        tool_message.content = "line1\nline2\nline3"
        tool_message.status = "success"

        record = tracker.complete_with_message(tool_message)

        assert record is not None
        assert record.status == "success"
        assert record.read_output == "line1\nline2\nline3"
        assert record.metrics.lines_read == 3
        assert len(tracker.completed) == 1
        assert len(tracker.active) == 0

    def test_complete_error_operation(self):
        """Test completing an operation with error."""
        tracker = FileOpTracker(assistant_id="test", backend=None)
        args = {"file_path": "/test/file.txt", "content": "test"}

        tracker.start_operation("write_file", args, "call1")

        # Mock error tool message
        tool_message = Mock()
        tool_message.tool_call_id = "call1"
        tool_message.name = "write_file"
        tool_message.content = "Error: Permission denied"
        tool_message.status = "error"

        record = tracker.complete_with_message(tool_message)

        assert record is not None
        assert record.status == "error"
        assert record.error == "Error: Permission denied"
        assert len(tracker.completed) == 1

    def test_complete_read_operation_with_list_content_and_offset_overflow(self):
        tracker = FileOpTracker(assistant_id="test", backend=None)
        args = {"file_path": "/test/file.txt", "offset": 99, "limit": 1}
        tracker.start_operation("read_file", args, "call1")

        tool_message = Mock()
        tool_message.tool_call_id = "call1"
        tool_message.name = "read_file"
        tool_message.content = ["line1", {"text": "line2"}]
        tool_message.status = "success"

        record = tracker.complete_with_message(tool_message)

        assert record is not None
        assert record.read_output == "line1\n{'text': 'line2'}"
        assert record.metrics.start_line == 1
        assert record.metrics.end_line == 1

    def test_complete_write_operation_reads_after_content_from_filesystem(
        self, tmp_path
    ):
        path = tmp_path / "file.txt"
        path.write_text("before\n")
        tracker = FileOpTracker(assistant_id="test", backend=None)
        tracker.start_operation(
            "write_file",
            {"file_path": str(path), "content": "after\n"},
            "call1",
        )
        path.write_text("after\n")

        tool_message = Mock()
        tool_message.tool_call_id = "call1"
        tool_message.name = "write_file"
        tool_message.content = "ok"
        tool_message.status = "success"

        record = tracker.complete_with_message(tool_message)

        assert record is not None
        assert record.status == "success"
        assert record.after_content == "after\n"
        assert record.metrics.lines_written == 1
        assert record.metrics.lines_added == 1
        assert record.metrics.lines_removed == 1
        assert record.metrics.bytes_written == len("after\n".encode("utf-8"))
        assert record.diff is not None

    def test_complete_write_operation_reports_after_content_failure(self, tmp_path):
        path = tmp_path / "file.txt"
        tracker = FileOpTracker(assistant_id="test", backend=None)
        tracker.start_operation(
            "write_file",
            {"file_path": str(path), "content": "after\n"},
            "call1",
        )

        tool_message = Mock()
        tool_message.tool_call_id = "call1"
        tool_message.name = "write_file"
        tool_message.content = "ok"
        tool_message.status = "success"

        record = tracker.complete_with_message(tool_message)

        assert record is not None
        assert record.status == "error"
        assert record.error == "Could not read updated file content."

    def test_complete_with_message_matches_by_tool_name_and_path(self):
        tracker = FileOpTracker(assistant_id="test", backend=None)
        tracker.start_operation("write_file", {"file_path": "/tmp/a.txt"}, None)
        tracker.start_operation("write_file", {"file_path": "/tmp/b.txt"}, None)

        tool_message = Mock()
        tool_message.tool_call_id = "real-call"
        tool_message.name = "write_file"
        tool_message.content = "Error: failed"
        tool_message.status = "error"

        record = tracker.complete_with_message(
            tool_message,
            tool_args={"file_path": "/tmp/b.txt"},
        )

        assert record is not None
        assert record.tool_call_id == "real-call"
        assert record.args["file_path"] == "/tmp/b.txt"
        assert len(tracker.active) == 1

    def test_complete_with_message_single_name_match_updates_tool_call_id(self):
        tracker = FileOpTracker(assistant_id="test", backend=None)
        tracker.start_operation("read_file", {"file_path": "/tmp/a.txt"}, None)

        tool_message = Mock()
        tool_message.tool_call_id = "actual"
        tool_message.name = "read_file"
        tool_message.content = "one\n"
        tool_message.status = "success"

        record = tracker.complete_with_message(tool_message)

        assert record is not None
        assert record.tool_call_id == "actual"
        assert not tracker.active

    def test_complete_with_message_ambiguous_matches_stay_active(self):
        tracker = FileOpTracker(assistant_id="test", backend=None)
        tracker.start_operation("write_file", {"file_path": "/tmp/a.txt"}, None)
        tracker.start_operation("write_file", {"file_path": "/tmp/b.txt"}, None)

        tool_message = Mock()
        tool_message.tool_call_id = "actual"
        tool_message.name = "write_file"
        tool_message.content = "ok"
        tool_message.status = "success"

        assert tracker.complete_with_message(tool_message) is None
        assert (
            tracker.complete_with_message(
                tool_message, tool_args={"file_path": "/tmp/missing.txt"}
            )
            is None
        )
        assert len(tracker.active) == 2

    def test_mark_hitl_approved_marks_matching_active_record(self):
        tracker = FileOpTracker(assistant_id="test", backend=None)
        tracker.start_operation("write_file", {"file_path": "/tmp/a.txt"}, "a")
        tracker.start_operation("write_file", {"file_path": "/tmp/b.txt"}, "b")

        tracker.mark_hitl_approved("write_file", {"file_path": "/tmp/b.txt"})

        assert not tracker.active["a"].hitl_approved
        assert tracker.active["b"].hitl_approved
        tracker.mark_hitl_approved("write_file", {})

    def test_populate_after_content_uses_backend_then_filesystem(self, tmp_path):
        backend = Mock()
        backend.download_files.return_value = [Mock(content=b"backend\n", error=None)]
        tracker = FileOpTracker(assistant_id="test", backend=backend)
        record = FileOperationRecord(
            tool_name="write_file",
            display_path="file.txt",
            physical_path=None,
            tool_call_id="call",
            args={"file_path": "/remote/file.txt"},
        )

        tracker._populate_after_content(record)

        assert record.after_content == "backend\n"

        local = tmp_path / "local.txt"
        local.write_text("local\n")
        backend.download_files.return_value = [Mock(content=None, error="missing")]
        record = FileOperationRecord(
            tool_name="write_file",
            display_path="local.txt",
            physical_path=local,
            tool_call_id="call",
            args={"file_path": str(local)},
        )

        tracker._populate_after_content(record)

        assert record.after_content == "local\n"

    def test_populate_after_content_backend_exception_and_missing_path(self):
        backend = Mock()
        backend.download_files.side_effect = OSError("backend down")
        tracker = FileOpTracker(assistant_id="test", backend=backend)
        record = FileOperationRecord(
            tool_name="write_file",
            display_path="remote.txt",
            physical_path=None,
            tool_call_id="call",
            args={"file_path": "/remote.txt"},
        )

        tracker._populate_after_content(record)

        assert record.after_content is None

    def test_complete_read_without_offset_sets_full_range(self):
        tracker = FileOpTracker(assistant_id="test", backend=None)
        tracker.start_operation("read_file", {"file_path": "/tmp/a.txt"}, "call")

        tool_message = Mock()
        tool_message.tool_call_id = "call"
        tool_message.name = "read_file"
        tool_message.content = "one\ntwo"
        tool_message.status = "success"

        record = tracker.complete_with_message(tool_message)

        assert record is not None
        assert record.metrics.start_line == 1
        assert record.metrics.end_line == 2

    def test_complete_write_handles_missing_before_content_and_empty_noop(
        self, tmp_path
    ):
        path = tmp_path / "file.txt"
        path.write_text("after\n")
        tracker = FileOpTracker(assistant_id="test", backend=None)
        record = FileOperationRecord(
            tool_name="write_file",
            display_path="file.txt",
            physical_path=path,
            tool_call_id="manual",
            args={"file_path": str(path)},
            before_content=None,
        )
        tracker.active["manual"] = record

        tool_message = Mock()
        tool_message.tool_call_id = "manual"
        tool_message.name = "write_file"
        tool_message.content = "ok"
        tool_message.status = "success"

        completed = tracker.complete_with_message(tool_message)
        assert completed is not None
        assert completed.metrics.lines_added == 1

        empty = tmp_path / "empty.txt"
        empty.write_text("")
        tracker.start_operation("write_file", {"file_path": str(empty)}, "empty")
        tool_message.tool_call_id = "empty"
        completed = tracker.complete_with_message(tool_message)
        assert completed is not None
        assert completed.metrics.lines_added == 0

    def test_complete_with_message_returns_none_without_matching_record(self):
        tracker = FileOpTracker(assistant_id="test", backend=None)
        tool_message = Mock()
        tool_message.tool_call_id = "missing"
        tool_message.name = "write_file"
        tool_message.content = "ok"
        tool_message.status = "success"

        assert tracker.complete_with_message(tool_message) is None


if __name__ == "__main__":
    pytest.main([__file__])
