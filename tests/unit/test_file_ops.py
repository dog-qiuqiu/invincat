"""Unit tests for file operations module."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from invincat_cli.file_ops import (
    FileOpTracker,
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

    def test_unsupported_tool_ignored(self):
        """Test that unsupported tools are ignored."""
        tracker = FileOpTracker(assistant_id="test", backend=None)
        args = {"file_path": "/test/file.txt"}
        
        tracker.start_operation("unsupported_tool", args, "call1")
        
        assert len(tracker.active) == 0

    @patch("invincat_cli.file_ops._safe_read")
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


if __name__ == "__main__":
    pytest.main([__file__])