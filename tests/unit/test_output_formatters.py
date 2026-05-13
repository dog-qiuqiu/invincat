"""Tests for tool output formatting helpers."""

from __future__ import annotations

import json

from invincat_cli.widgets.output_formatters import (
    format_tool_output,
    prefix_tool_output,
)


def test_format_todo_output_includes_status_summary() -> None:
    result = format_tool_output(
        "write_todos",
        str(
            [
                {"content": "Implement parser", "status": "completed"},
                {"content": "Add tests", "status": "in_progress"},
                {"content": "Update docs", "status": "pending"},
            ]
        ),
    )

    plain = result.content.plain
    assert "1 active" in plain
    assert "1 pending" in plain
    assert "1 done" in plain
    assert "Implement parser" in plain
    assert "Add tests" in plain


def test_format_file_output_preview_truncates_by_line_count() -> None:
    result = format_tool_output(
        "read_file",
        "one\ntwo\nthree\nfour",
        is_preview=True,
        preview_lines=2,
        preview_chars=200,
    )

    assert result.content.plain == "one\ntwo"
    assert result.truncation == "2 more lines"


def test_format_web_search_output_preview_truncates_results() -> None:
    result = format_tool_output(
        "web_search",
        json.dumps({
            "results": [
                {"title": "One", "url": "https://example.com/1"},
                {"title": "Two", "url": "https://example.com/2"},
                {"title": "Three", "url": "https://example.com/3"},
                {"title": "Four", "url": "https://example.com/4"},
            ]
        }),
        is_preview=True,
    )

    plain = result.content.plain
    assert "One" in plain
    assert "Three" in plain
    assert "Four" not in plain
    assert result.truncation


def test_unknown_tool_preview_truncates_by_character_count() -> None:
    result = format_tool_output(
        "custom_tool",
        "abcdef",
        is_preview=True,
        preview_lines=10,
        preview_chars=3,
    )

    assert result.content.plain == "abc"
    assert result.truncation == "3 more chars"


def test_prefix_tool_output_indents_continuation_lines() -> None:
    result = prefix_tool_output(format_tool_output("shell", "$ pwd\n/tmp").content)

    plain = result.plain
    assert "$ pwd" in plain
    assert "\n  /tmp" in plain
