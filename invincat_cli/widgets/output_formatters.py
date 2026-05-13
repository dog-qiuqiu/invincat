"""Formatting helpers for tool output widgets."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from textual.content import Content

from invincat_cli import theme
from invincat_cli.config import get_glyphs
from invincat_cli.i18n import t


@dataclass(frozen=True, slots=True)
class FormattedOutput:
    """Result of formatting tool output for display."""

    content: Content
    truncation: str | None = None


_MAX_TODO_CONTENT_LEN = 70
_MAX_WEB_CONTENT_LEN = 100


def format_tool_output(
    tool_name: str,
    output: str,
    *,
    is_preview: bool = False,
    preview_lines: int = 6,
    preview_chars: int = 400,
    theme_context: object | None = None,
) -> FormattedOutput:
    """Format raw tool output based on the tool type."""
    output = output.strip()
    if not output:
        return FormattedOutput(content=Content(""))

    formatters = {
        "write_todos": lambda value: _format_todos_output(
            value, is_preview=is_preview, theme_context=theme_context
        ),
        "ls": lambda value: _format_ls_output(value, is_preview=is_preview),
        "read_file": lambda value: _format_file_output(
            value,
            is_preview=is_preview,
            preview_lines=preview_lines,
            preview_chars=preview_chars,
        ),
        "write_file": lambda value: _format_file_output(
            value,
            is_preview=is_preview,
            preview_lines=preview_lines,
            preview_chars=preview_chars,
        ),
        "edit_file": lambda value: _format_file_output(
            value,
            is_preview=is_preview,
            preview_lines=preview_lines,
            preview_chars=preview_chars,
        ),
        "grep": lambda value: _format_search_output(value, is_preview=is_preview),
        "glob": lambda value: _format_search_output(value, is_preview=is_preview),
        "shell": lambda value: _format_shell_output(value, is_preview=is_preview),
        "bash": lambda value: _format_shell_output(value, is_preview=is_preview),
        "execute": lambda value: _format_shell_output(value, is_preview=is_preview),
        "web_search": lambda value: _format_web_output(value, is_preview=is_preview),
        "fetch_url": lambda value: _format_web_output(value, is_preview=is_preview),
        "task": lambda value: _format_task_output(value, is_preview=is_preview),
    }

    formatter = formatters.get(tool_name)
    if formatter is not None:
        return formatter(output)

    if is_preview:
        lines = output.split("\n")
        if len(lines) > preview_lines:
            return format_lines_output(lines, is_preview=True)
        if len(output) > preview_chars:
            truncated = output[:preview_chars]
            truncation = f"{len(output) - preview_chars} more chars"
            return FormattedOutput(content=Content(truncated), truncation=truncation)

    return FormattedOutput(content=Content(output))


def prefix_tool_output(content: Content) -> Content:
    """Prefix formatted output with the configured tool-output marker."""
    if not content.plain:
        return Content("")
    output_prefix = get_glyphs().output_prefix
    lines = content.split("\n")
    prefixed = [Content.assemble(f"{output_prefix} ", lines[0])]
    prefixed.extend(Content.assemble("  ", line) for line in lines[1:])
    return Content("\n").join(prefixed)


def _format_todos_output(
    output: str,
    *,
    is_preview: bool = False,
    theme_context: object | None = None,
) -> FormattedOutput:
    items = _parse_todo_items(output)
    if items is None:
        return FormattedOutput(content=Content(output))

    if not items:
        return FormattedOutput(content=Content.styled("    No todos", "dim"))

    lines: list[Content] = []
    max_items = 4 if is_preview else len(items)

    stats = _build_todo_stats(items, theme_context=theme_context)
    if stats:
        lines.extend([Content.assemble("    ", stats), Content("")])

    lines.extend(
        _format_single_todo(item, theme_context=theme_context)
        for item in items[:max_items]
    )

    truncation = None
    if is_preview and len(items) > max_items:
        truncation = f"{len(items) - max_items} more"

    return FormattedOutput(content=Content("\n").join(lines), truncation=truncation)


def _parse_todo_items(output: str) -> list[Any] | None:
    list_match = re.search(r"\[(\{.*\})\]", output.replace("\n", " "), re.DOTALL)
    if list_match:
        try:
            return ast.literal_eval("[" + list_match.group(1) + "]")
        except (ValueError, SyntaxError):
            return None
    try:
        items = ast.literal_eval(output)
    except (ValueError, SyntaxError):
        return None
    return items if isinstance(items, list) else None


def _build_todo_stats(
    items: list[Any],
    *,
    theme_context: object | None = None,
) -> Content:
    colors = theme.get_theme_colors(theme_context)
    completed = sum(
        1 for item in items if isinstance(item, dict) and item.get("status") == "completed"
    )
    active = sum(
        1
        for item in items
        if isinstance(item, dict) and item.get("status") == "in_progress"
    )
    pending = len(items) - completed - active

    parts: list[Content] = []
    if active:
        parts.append(Content.styled(f"{active} active", colors.warning))
    if pending:
        parts.append(Content.styled(f"{pending} pending", "dim"))
    if completed:
        parts.append(Content.styled(f"{completed} done", colors.success))
    return Content.styled(" | ", "dim").join(parts) if parts else Content("")


def _format_single_todo(
    item: dict[str, Any] | str,
    *,
    theme_context: object | None = None,
) -> Content:
    colors = theme.get_theme_colors(theme_context)
    if isinstance(item, dict):
        text = item.get("content", str(item))
        status = item.get("status", "pending")
    else:
        text = str(item)
        status = "pending"

    if len(text) > _MAX_TODO_CONTENT_LEN:
        text = text[: _MAX_TODO_CONTENT_LEN - 3] + "..."

    glyphs = get_glyphs()
    if status == "completed":
        return Content.assemble(
            Content.styled(f"    {glyphs.checkmark} done", colors.success),
            Content.styled(f"   {text}", "dim"),
        )
    if status == "in_progress":
        return Content.assemble(
            Content.styled(f"    {glyphs.circle_filled} active", colors.warning),
            f" {text}",
        )
    return Content.assemble(
        Content.styled(f"    {glyphs.circle_empty} todo", "dim"),
        f"   {text}",
    )


def _format_ls_output(output: str, *, is_preview: bool = False) -> FormattedOutput:
    try:
        items = ast.literal_eval(output)
    except (ValueError, SyntaxError):
        return FormattedOutput(content=Content(output))

    if not isinstance(items, list):
        return FormattedOutput(content=Content(output))

    lines: list[Content] = []
    max_items = 5 if is_preview else len(items)
    for item in items[:max_items]:
        path = Path(str(item))
        name = path.name
        if path.suffix in {".py", ".pyx"}:
            lines.append(Content.styled(f"    {name}", theme.FILE_PYTHON))
        elif path.suffix in {".json", ".yaml", ".yml", ".toml"}:
            lines.append(Content.styled(f"    {name}", theme.FILE_CONFIG))
        elif not path.suffix:
            lines.append(Content.styled(f"    {name}/", theme.FILE_DIR))
        else:
            lines.append(Content(f"    {name}"))

    truncation = None
    if is_preview and len(items) > max_items:
        truncation = f"{len(items) - max_items} more"

    return FormattedOutput(content=Content("\n").join(lines), truncation=truncation)


def _format_file_output(
    output: str,
    *,
    is_preview: bool = False,
    preview_lines: int = 6,
    preview_chars: int = 400,
) -> FormattedOutput:
    lines = output.split("\n")
    total_chars = len(output)

    if is_preview:
        if len(lines) > preview_lines:
            parts = [Content(line) for line in lines[:preview_lines]]
            content = Content("\n").join(parts)
            truncation = f"{len(lines) - preview_lines} more lines"
            return FormattedOutput(content=content, truncation=truncation)

        if total_chars > preview_chars:
            truncated = output[:preview_chars]
            parts = [Content(line) for line in truncated.split("\n")]
            content = Content("\n").join(parts)
            truncation = f"{total_chars - preview_chars} more chars"
            return FormattedOutput(content=content, truncation=truncation)

    parts = [Content(line) for line in lines]
    return FormattedOutput(content=Content("\n").join(parts))


def _format_search_output(
    output: str,
    *,
    is_preview: bool = False,
) -> FormattedOutput:
    try:
        items = ast.literal_eval(output.strip())
    except (ValueError, SyntaxError):
        items = None

    if isinstance(items, list):
        parts: list[Content] = []
        max_items = 5 if is_preview else len(items)
        for item in items[:max_items]:
            path = Path(str(item))
            try:
                display = str(path.relative_to(Path.cwd()))
            except ValueError:
                display = path.name
            parts.append(Content(f"    {display}"))

        truncation = None
        if is_preview and len(items) > max_items:
            truncation = f"{len(items) - max_items} more files"

        return FormattedOutput(
            content=Content("\n").join(parts),
            truncation=truncation,
        )

    lines = output.split("\n")
    max_lines = 5 if is_preview else len(lines)
    parts = [
        Content(f"    {raw_line.strip()}")
        for raw_line in lines[:max_lines]
        if raw_line.strip()
    ]

    content = Content("\n").join(parts) if parts else Content("")
    truncation = None
    if is_preview and len(lines) > max_lines:
        truncation = f"{len(lines) - max_lines} more"

    return FormattedOutput(content=content, truncation=truncation)


def _format_shell_output(
    output: str,
    *,
    is_preview: bool = False,
) -> FormattedOutput:
    lines = output.split("\n")
    max_lines = 4 if is_preview else len(lines)

    parts: list[Content] = []
    for i, line in enumerate(lines[:max_lines]):
        if i == 0 and line.startswith("$ "):
            parts.append(Content.styled(line, "dim"))
        else:
            parts.append(Content(line))

    content = Content("\n").join(parts) if parts else Content("")
    truncation = None
    if is_preview and len(lines) > max_lines:
        truncation = f"{len(lines) - max_lines} more lines"

    return FormattedOutput(content=content, truncation=truncation)


def _format_web_output(output: str, *, is_preview: bool = False) -> FormattedOutput:
    data = _try_parse_web_data(output)
    if isinstance(data, dict):
        return _format_web_dict(data, is_preview=is_preview)

    return format_lines_output(output.split("\n"), is_preview=is_preview)


def _try_parse_web_data(output: str) -> dict[str, Any] | None:
    try:
        if output.strip().startswith("{"):
            return json.loads(output)
        value = ast.literal_eval(output)
    except (ValueError, SyntaxError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _format_web_dict(data: dict[str, Any], *, is_preview: bool) -> FormattedOutput:
    if "results" in data:
        results = data.get("results", [])
        return _format_web_search_results(
            results if isinstance(results, list) else [],
            is_preview=is_preview,
        )

    if "markdown_content" in data:
        lines = str(data["markdown_content"]).split("\n")
        return format_lines_output(lines, is_preview=is_preview)

    parts: list[Content] = []
    max_keys = 3 if is_preview else len(data)
    for key, value in list(data.items())[:max_keys]:
        value_text = str(value)
        if is_preview and len(value_text) > _MAX_WEB_CONTENT_LEN:
            value_text = value_text[:_MAX_WEB_CONTENT_LEN] + "..."
        parts.append(Content(f"  {key}: {value_text}"))

    truncation = None
    if is_preview and len(data) > max_keys:
        truncation = f"{len(data) - max_keys} more"
    return FormattedOutput(
        content=Content("\n").join(parts) if parts else Content(""),
        truncation=truncation,
    )


def _format_web_search_results(
    results: list[Any],
    *,
    is_preview: bool,
) -> FormattedOutput:
    if not results:
        return FormattedOutput(content=Content.styled(t("message.no_results"), "dim"))

    parts: list[Content] = []
    max_results = 3 if is_preview else len(results)
    for result in results[:max_results]:
        if not isinstance(result, dict):
            continue
        title = result.get("title", "")
        url = result.get("url", "")
        parts.extend(
            [
                Content.styled(f"  {title}", "bold"),
                Content.styled(f"  {url}", "dim"),
            ]
        )
    truncation = None
    if is_preview and len(results) > max_results:
        truncation = t("message.more_results", count=len(results) - max_results)
    return FormattedOutput(content=Content("\n").join(parts), truncation=truncation)


def format_lines_output(lines: list[str], *, is_preview: bool) -> FormattedOutput:
    """Format a list of lines with generic preview truncation."""
    max_lines = 4 if is_preview else len(lines)
    parts = [Content(line) for line in lines[:max_lines]]
    content = Content("\n").join(parts) if parts else Content("")
    truncation = None
    if is_preview and len(lines) > max_lines:
        truncation = f"{len(lines) - max_lines} more lines"
    return FormattedOutput(content=content, truncation=truncation)


def _format_task_output(output: str, *, is_preview: bool = False) -> FormattedOutput:
    lines = output.split("\n")
    max_lines = 4 if is_preview else len(lines)

    parts = [Content(line) for line in lines[:max_lines]]
    content = Content("\n").join(parts) if parts else Content("")

    truncation = None
    if is_preview and len(lines) > max_lines:
        truncation = f"{len(lines) - max_lines} more lines"

    return FormattedOutput(content=content, truncation=truncation)
