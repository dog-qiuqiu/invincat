"""Tests for tool output formatting helpers."""

from __future__ import annotations

import json
from pathlib import Path

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
        json.dumps(
            {
                "results": [
                    {"title": "One", "url": "https://example.com/1"},
                    {"title": "Two", "url": "https://example.com/2"},
                    {"title": "Three", "url": "https://example.com/3"},
                    {"title": "Four", "url": "https://example.com/4"},
                ]
            }
        ),
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


def test_unknown_tool_non_preview_returns_full_output() -> None:
    result = format_tool_output("custom_tool", "abcdef", is_preview=False)

    assert result.content.plain == "abcdef"
    assert result.truncation is None


def test_prefix_tool_output_indents_continuation_lines() -> None:
    result = prefix_tool_output(format_tool_output("shell", "$ pwd\n/tmp").content)

    plain = result.plain
    assert "$ pwd" in plain
    assert "\n  /tmp" in plain


def test_empty_output_and_empty_prefix_return_empty_content() -> None:
    assert format_tool_output("shell", "   ").content.plain == ""
    assert prefix_tool_output(format_tool_output("shell", "   ").content).plain == ""


def test_unknown_tool_preview_truncates_by_line_count() -> None:
    result = format_tool_output(
        "custom_tool",
        "one\ntwo\nthree\nfour\nfive",
        is_preview=True,
        preview_lines=2,
        preview_chars=200,
    )

    assert result.content.plain == "one\ntwo\nthree\nfour"
    assert result.truncation == "1 more lines"


def test_todo_output_handles_empty_invalid_string_and_long_items() -> None:
    assert "No todos" in format_tool_output("write_todos", "[]").content.plain
    assert format_tool_output("write_todos", "not a list").content.plain == "not a list"
    assert format_tool_output("write_todos", "[{bad}]").content.plain == "[{bad}]"

    long_content = "x" * 90
    result = format_tool_output(
        "write_todos",
        str([{"content": long_content, "status": "pending"}, "plain item"]),
    )

    plain = result.content.plain
    assert "xxx..." in plain
    assert "plain item" in plain


def test_todo_preview_limits_items() -> None:
    result = format_tool_output(
        "write_todos",
        str([{"content": f"item {idx}", "status": "pending"} for idx in range(6)]),
        is_preview=True,
    )

    assert "item 0" in result.content.plain
    assert "item 4" not in result.content.plain
    assert result.truncation == "2 more"


def test_ls_output_formats_known_suffixes_and_preview_truncates() -> None:
    result = format_tool_output(
        "ls",
        str(
            ["src/app.py", "config.toml", "docs", "README.md", "data.json", "more.txt"]
        ),
        is_preview=True,
    )

    plain = result.content.plain
    assert "app.py" in plain
    assert "config.toml" in plain
    assert "docs/" in plain
    assert "more.txt" not in plain
    assert result.truncation == "1 more"


def test_ls_output_falls_back_for_invalid_or_non_list_output() -> None:
    assert format_tool_output("ls", "{bad").content.plain == "{bad"
    assert format_tool_output("ls", "{'a': 1}").content.plain == "{'a': 1}"


def test_file_output_preview_truncates_by_character_count() -> None:
    result = format_tool_output(
        "write_file",
        "abcdef",
        is_preview=True,
        preview_lines=10,
        preview_chars=3,
    )

    assert result.content.plain == "abc"
    assert result.truncation == "3 more chars"


def test_file_output_non_preview_keeps_all_lines() -> None:
    result = format_tool_output("read_file", "one\ntwo", is_preview=False)

    assert result.content.plain == "one\ntwo"
    assert result.truncation is None


def test_search_output_formats_literal_file_list(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    inside = tmp_path / "src" / "app.py"
    outside = Path("/outside/result.py")

    result = format_tool_output("glob", str([inside, outside]), is_preview=True)

    plain = result.content.plain
    assert "src/app.py" in plain
    assert "result.py" in plain


def test_search_output_uses_filename_for_paths_outside_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = format_tool_output("glob", str(["/outside/result.py"]))

    assert result.content.plain == "    result.py"


def test_search_output_list_preview_reports_more_files(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = format_tool_output(
        "glob",
        str([str(tmp_path / f"file{idx}.py") for idx in range(7)]),
        is_preview=True,
    )

    assert "file0.py" in result.content.plain
    assert "file6.py" not in result.content.plain
    assert result.truncation == "2 more files"


def test_search_output_formats_text_lines_and_preview_truncates() -> None:
    result = format_tool_output(
        "grep",
        " hit one \n\nhit two\nhit three\nhit four\nhit five\nhit six",
        is_preview=True,
    )

    plain = result.content.plain
    assert "hit one" in plain
    assert "hit six" not in plain
    assert result.truncation == "2 more"


def test_shell_output_preview_truncates_lines() -> None:
    result = format_tool_output(
        "shell", "$ cmd\none\ntwo\nthree\nfour", is_preview=True
    )

    assert "$ cmd" in result.content.plain
    assert "four" not in result.content.plain
    assert result.truncation == "1 more lines"


def test_web_output_handles_markdown_generic_dict_and_invalid_data() -> None:
    markdown = format_tool_output(
        "fetch_url",
        json.dumps({"markdown_content": "one\ntwo\nthree\nfour\nfive"}),
        is_preview=True,
    )
    assert markdown.content.plain == "one\ntwo\nthree\nfour"
    assert markdown.truncation == "1 more lines"

    generic = format_tool_output(
        "web_search",
        json.dumps({"a": "x" * 120, "b": 2, "c": 3, "d": 4}),
        is_preview=True,
    )
    assert "a:" in generic.content.plain
    assert "..." in generic.content.plain
    assert generic.truncation == "1 more"

    fallback = format_tool_output("fetch_url", "{not json}\nline2", is_preview=True)
    assert fallback.content.plain == "{not json}\nline2"

    literal_list = format_tool_output("fetch_url", "['a', 'b']", is_preview=False)
    assert literal_list.content.plain == "['a', 'b']"


def test_web_search_output_handles_empty_and_malformed_results() -> None:
    empty = format_tool_output("web_search", json.dumps({"results": []}))
    assert empty.content.plain

    mixed = format_tool_output(
        "web_search",
        json.dumps({"results": ["bad", {"title": "Good", "url": "https://x"}]}),
    )
    assert "Good" in mixed.content.plain
    assert "bad" not in mixed.content.plain


def test_task_output_preview_truncates_lines() -> None:
    result = format_tool_output("task", "one\ntwo\nthree\nfour\nfive", is_preview=True)

    assert result.content.plain == "one\ntwo\nthree\nfour"
    assert result.truncation == "1 more lines"
