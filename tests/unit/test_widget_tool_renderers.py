from __future__ import annotations

from typing import Any

import pytest
from textual.widgets import Markdown, Static

import invincat_cli.widgets.tool_widgets as tool_widgets
from invincat_cli.widgets.tool_renderers import (
    ApprovePlanRenderer,
    EditFileRenderer,
    GenericApprovalWidget,
    PlanApprovalWidget,
    TaskRenderer,
    WriteFileApprovalWidget,
    WriteFileRenderer,
    get_renderer,
)
from invincat_cli.widgets.tool_widgets import (
    EditFileApprovalWidget,
    ToolApprovalWidget,
    _count_diff_stats,
    _file_header,
    _format_stats,
)


@pytest.fixture(autouse=True)
def stable_tool_widget_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_t(key: str, **kwargs: Any) -> str:
        if kwargs:
            suffix = ",".join(f"{name}={value}" for name, value in kwargs.items())
            return f"{key}({suffix})"
        return key

    monkeypatch.setattr(tool_widgets, "t", fake_t)


def _static_text(widget: Static) -> str:
    return str(widget._Static__content)  # noqa: SLF001


def test_renderer_registry_selects_specialized_and_default_widgets() -> None:
    assert type(get_renderer("write_file")) is WriteFileRenderer
    assert type(get_renderer("edit_file")) is EditFileRenderer
    assert type(get_renderer("approve_plan")) is ApprovePlanRenderer
    assert type(get_renderer("task")) is TaskRenderer

    widget_cls, data = get_renderer("unknown").get_approval_widget({"x": 1})
    assert widget_cls is GenericApprovalWidget
    assert data == {"x": 1}


def test_write_file_renderer_extracts_extension_and_defaults_to_text() -> None:
    widget_cls, data = WriteFileRenderer.get_approval_widget(
        {"file_path": "src/app.py", "content": "print('x')"}
    )
    assert widget_cls is WriteFileApprovalWidget
    assert data == {
        "file_path": "src/app.py",
        "content": "print('x')",
        "file_extension": "py",
    }

    _, data = WriteFileRenderer.get_approval_widget({"file_path": "README"})
    assert data["file_extension"] == "text"


def test_task_and_plan_renderers_normalize_input() -> None:
    assert TaskRenderer.get_approval_widget({"ignored": "value"}) == (
        GenericApprovalWidget,
        {},
    )

    widget_cls, data = ApprovePlanRenderer.get_approval_widget(
        {
            "todos": [
                {"content": "ship", "status": "in_progress"},
                {"content": 123},
                "bad",
            ]
        }
    )

    assert widget_cls is PlanApprovalWidget
    assert data == {
        "todos": [
            {"content": "ship", "status": "in_progress"},
            {"content": "123", "status": "pending"},
        ]
    }


def test_edit_file_renderer_generates_headerless_unified_diff() -> None:
    widget_cls, data = EditFileRenderer.get_approval_widget(
        {
            "file_path": "a.txt",
            "old_string": "one\ntwo",
            "new_string": "one\nthree",
        }
    )

    assert widget_cls is EditFileApprovalWidget
    assert data["file_path"] == "a.txt"
    assert data["diff_lines"][0].startswith("@@")
    assert "--- before" not in data["diff_lines"]
    assert "+++ after" not in data["diff_lines"]
    assert EditFileRenderer._generate_diff("", "") == []


def test_widget_helpers_format_stats_header_and_diff_counts() -> None:
    assert str(_format_stats(0, 0)) == ""
    assert str(_format_stats(2, 1)) == "+2 -1"

    header, spacer = list(_file_header("src/app.py", additions=2, deletions=1))
    assert isinstance(header, Static)
    assert _static_text(header) == "File: src/app.py  +2 -1"
    assert _static_text(spacer) == ""

    assert _count_diff_stats(["+new", "-old", " ctx"], "", "") == (1, 1)
    assert _count_diff_stats([], "old\nlines", "new") == (1, 2)


def test_generic_approval_widget_skips_none_and_truncates_values() -> None:
    [base] = list(ToolApprovalWidget({}).compose())
    assert _static_text(base) == "tool.details_not_available"

    widgets = list(
        GenericApprovalWidget(
            {"none": None, "long": "x" * 205, "short": "ok"}
        ).compose()
    )

    assert len(widgets) == 2
    assert _static_text(widgets[0]).endswith("tool.more_chars(count=5)")
    assert _static_text(widgets[1]) == "short: ok"


def test_plan_approval_widget_handles_empty_and_long_todos() -> None:
    [empty] = list(PlanApprovalWidget({"todos": []}).compose())
    assert _static_text(empty) == "tool.details_not_available"

    long_content = "x" * 205
    widgets = list(
        PlanApprovalWidget(
            {
                "todos": [
                    {"content": "", "status": "pending"},
                    {"content": long_content, "status": "completed"},
                    {"content": "unknown status", "status": "blocked"},
                    "bad",
                ]
            }
        ).compose()
    )

    assert _static_text(widgets[0]) == "tool.plan_preview"
    assert _static_text(widgets[1]).startswith("● 2. ")
    assert _static_text(widgets[1]).endswith("tool.more_chars(count=5)")
    assert _static_text(widgets[2]) == "○ 3. unknown status"


def test_write_file_widget_renders_full_and_truncated_markdown() -> None:
    full_widgets = list(
        tool_widgets.WriteFileApprovalWidget(
            {
                "file_path": "src/app.py",
                "content": "print('x')",
                "file_extension": "py",
            }
        ).compose()
    )
    assert [type(widget) for widget in full_widgets] == [Static, Static, Markdown]
    assert "```py\nprint('x')\n```" in full_widgets[-1]._initial_markdown  # noqa: SLF001

    long_content = "\n".join(str(i) for i in range(31))
    truncated = list(
        tool_widgets.WriteFileApprovalWidget(
            {
                "file_path": "long.txt",
                "content": long_content,
                "file_extension": "text",
            }
        ).compose()
    )

    assert "... (1 more lines)" in truncated[-1]._initial_markdown  # noqa: SLF001


def test_edit_file_widget_renders_no_changes_diff_and_truncation() -> None:
    no_change = list(EditFileApprovalWidget({"file_path": "a.txt"}).compose())
    assert _static_text(no_change[-1]) == "tool.no_changes"

    diff_lines = [
        "@@ -1 +1 @@",
        "--- before",
        "+++ after",
        "-old",
        "+new",
        " context",
        "",
        "metadata",
    ]
    widgets = list(
        EditFileApprovalWidget(
            {
                "file_path": "a.txt",
                "diff_lines": diff_lines,
                "old_string": "old",
                "new_string": "new",
            }
        ).compose()
    )
    rendered = [_static_text(widget) for widget in widgets[2:]]
    assert rendered == ["- old", "+ new", "  context", "metadata"]

    long_diff = [f"+line {idx}" for idx in range(51)]
    truncated = list(
        EditFileApprovalWidget(
            {"file_path": "b.txt", "diff_lines": long_diff}
        ).compose()
    )
    assert _static_text(truncated[-1]) == "tool.more_lines(count=1)"


def test_edit_file_widget_renders_old_new_strings_with_preview_limit() -> None:
    old_string = "\n".join(f"old {idx}" for idx in range(21))
    new_string = "\n".join(f"new {idx}" for idx in range(21))

    widgets = list(
        EditFileApprovalWidget(
            {
                "file_path": "a.txt",
                "old_string": old_string,
                "new_string": new_string,
            }
        ).compose()
    )
    rendered = [_static_text(widget) for widget in widgets]

    assert "tool.removing" in rendered
    assert "- old 0" in rendered
    assert "... (1 more lines)" in rendered
    assert "tool.adding" in rendered
    assert "+ new 0" in rendered
