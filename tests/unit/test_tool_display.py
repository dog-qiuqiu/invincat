from __future__ import annotations

from types import SimpleNamespace

from invincat_cli.presentation import tool_display


def install_test_glyphs(monkeypatch) -> None:
    monkeypatch.setattr(
        tool_display,
        "get_glyphs",
        lambda: SimpleNamespace(tool_prefix="(*)", ellipsis="..."),
    )


def test_format_timeout_and_coerce_timeout_seconds() -> None:
    assert tool_display._format_timeout(30) == "30s"
    assert tool_display._format_timeout(300) == "5m"
    assert tool_display._format_timeout(7200) == "2h"
    assert tool_display._format_timeout(65) == "65s"

    assert tool_display._coerce_timeout_seconds(5) == 5
    assert tool_display._coerce_timeout_seconds(True) is None
    assert tool_display._coerce_timeout_seconds(" 12 ") == 12
    assert tool_display._coerce_timeout_seconds("   ") is None
    assert tool_display._coerce_timeout_seconds("bad") is None
    assert tool_display._coerce_timeout_seconds(None) is None


def test_truncate_and_sanitize_display_value(monkeypatch) -> None:
    install_test_glyphs(monkeypatch)

    assert tool_display.truncate_value("abcdef", max_length=3) == "abc..."
    assert (
        tool_display._sanitize_display_value("safe\u202ehidden", max_length=20)
        == "safehidden [hidden chars removed]"
    )


def test_format_tool_display_for_common_tools(monkeypatch, tmp_path) -> None:
    install_test_glyphs(monkeypatch)

    assert tool_display.format_tool_display("read_file", {"path": "README.md"}) == (
        "(*) read_file(README.md)"
    )
    assert tool_display.format_tool_display("web_search", {"query": "python"}) == (
        '(*) web_search("python")'
    )
    assert tool_display.format_tool_display("grep", {"pattern": "needle"}) == (
        '(*) grep("needle")'
    )
    assert tool_display.format_tool_display("ls", {}) == "(*) ls()"
    assert tool_display.format_tool_display("glob", {"pattern": "*.py"}) == (
        '(*) glob("*.py")'
    )
    assert tool_display.format_tool_display("fetch_url", {"url": "https://e.test"}) == (
        '(*) fetch_url("https://e.test")'
    )
    assert tool_display.format_tool_display("task", {"subagent_type": "worker"}) == (
        "(*) task [worker]"
    )
    assert tool_display.format_tool_display("task", {}) == "(*) task"
    assert tool_display.format_tool_display("ask_user", {"questions": [{}, {}]}) == (
        "(*) ask_user(2 questions)"
    )
    assert tool_display.format_tool_display("compact_conversation", {}) == (
        "(*) compact_conversation()"
    )
    assert tool_display.format_tool_display("write_todos", {"todos": [1, 2]}) == (
        "(*) write_todos(2 items)"
    )

    nested = tmp_path / "very" / "long" / "name.txt"
    nested.parent.mkdir(parents=True)
    assert tool_display.format_tool_display(
        "write_file",
        {"file_path": str(nested)},
    ).endswith("name.txt)")


def test_format_tool_display_for_path_edges(monkeypatch, tmp_path) -> None:
    install_test_glyphs(monkeypatch)
    nested = tmp_path / "pkg" / "file.py"
    nested.parent.mkdir()
    nested.touch()
    monkeypatch.chdir(tmp_path)

    assert (
        tool_display.format_tool_display(
            "read_file",
            {"path": str(nested)},
        )
        == "(*) read_file(pkg/file.py)"
    )
    assert "hidden chars removed" in tool_display.format_tool_display(
        "edit_file",
        {"path": "safe\u202efile.py"},
    )
    assert "hidden chars removed" in tool_display.format_tool_display(
        "ls",
        {"path": "safe\u202edir"},
    )
    assert tool_display.format_tool_display("ls", {"path": "/tmp/x"}) == (
        "(*) ls(/tmp/x)"
    )


def test_format_tool_display_falls_back_when_path_constructor_fails(
    monkeypatch,
) -> None:
    install_test_glyphs(monkeypatch)

    class FailingPath:
        def __init__(self, _path: str) -> None:
            raise RuntimeError("bad path")

    monkeypatch.setattr(tool_display, "Path", FailingPath)

    assert (
        tool_display.format_tool_display(
            "read_file",
            {"path": "a" * 70},
        )
        == f"(*) read_file({'a' * 60}...)"
    )


def test_format_tool_display_for_execute_timeout(monkeypatch) -> None:
    install_test_glyphs(monkeypatch)

    assert "timeout=1s" in tool_display.format_tool_display(
        "execute",
        {"command": "sleep 1", "timeout": "1"},
    )
    assert tool_display.format_tool_display("execute", {"command": "pwd"}) == (
        '(*) execute("pwd")'
    )


def test_format_tool_display_generic_fallback_sanitizes(monkeypatch) -> None:
    install_test_glyphs(monkeypatch)

    rendered = tool_display.format_tool_display(
        "unknown",
        {"weird\u202ekey": "value"},
    )

    assert rendered == "(*) unknown(weirdkey [hidden chars removed]=value)"


def test_format_content_block_replaces_large_payloads() -> None:
    assert (
        tool_display._format_content_block(
            {"type": "image", "base64": "A" * 4096, "mime_type": "image/png"}
        )
        == "[Image: image/png, ~3KB]"
    )
    assert (
        tool_display._format_content_block(
            {"type": "video", "base64": "A" * 4096, "mime_type": "video/mp4"}
        )
        == "[Video: video/mp4, ~3KB]"
    )
    assert (
        tool_display._format_content_block(
            {"type": "file", "base64": "A" * 4096, "mime_type": "text/plain"}
        )
        == "[File: text/plain, ~3KB]"
    )
    assert tool_display._format_content_block({"text": "你好"}) == '{"text": "你好"}'
    assert "object object" in tool_display._format_content_block({"bad": object()})


def test_format_tool_message_content() -> None:
    assert tool_display.format_tool_message_content(None) == ""
    assert tool_display.format_tool_message_content("plain") == "plain"
    assert (
        tool_display.format_tool_message_content(
            ["text", {"type": "file", "base64": "AAAA", "mime_type": "text/plain"}, 3]
        )
        == "text\n[File: text/plain, ~0KB]\n3"
    )
    assert "object object" in tool_display.format_tool_message_content([object()])
