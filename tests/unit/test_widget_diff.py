from __future__ import annotations

from types import SimpleNamespace

import pytest
from textual.widgets import Static

import invincat_cli.widgets.diff as diff_mod
from invincat_cli.widgets.diff import EnhancedDiff, compose_diff_lines


@pytest.fixture(autouse=True)
def stable_diff_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        diff_mod.theme,
        "get_theme_colors",
        lambda *_args: SimpleNamespace(
            success="green",
            error="red",
            primary="blue",
        ),
    )
    monkeypatch.setattr(
        diff_mod,
        "get_glyphs",
        lambda: SimpleNamespace(
            gutter_bar="|",
            box_vertical=":",
            box_double_horizontal="=",
        ),
    )
    monkeypatch.setattr(
        diff_mod, "t", lambda key: {"diff.no_changes": "No changes"}[key]
    )


def _static_text(widget: Static) -> str:
    return str(widget._Static__content)  # noqa: SLF001


def test_compose_diff_lines_renders_empty_diff_message() -> None:
    [widget] = list(compose_diff_lines(""))

    assert _static_text(widget) == "No changes"


def test_compose_diff_lines_renders_stats_and_line_classes() -> None:
    diff = "\n".join(
        [
            "--- before.txt",
            "+++ after.txt",
            "@@ -10,2 +20,2 @@",
            "-old",
            "+new",
            " context",
            "...",
            "\\ No newline at end of file",
        ]
    )

    widgets = list(compose_diff_lines(diff, max_lines=None))

    assert _static_text(widgets[0]) == "+1 -1"
    assert _static_text(widgets[1]) == "| 10 old"
    assert "diff-line-removed" in widgets[1].classes
    assert _static_text(widgets[2]) == "| 20 new"
    assert "diff-line-added" in widgets[2].classes
    assert _static_text(widgets[3]) == ": 11  context"
    assert _static_text(widgets[4]) == "..."
    assert _static_text(widgets[5]) == "\\ No newline at end of file"


def test_compose_diff_lines_truncates_after_max_display_lines() -> None:
    diff = "\n".join(
        [
            "@@ -1,4 +1,4 @@",
            " one",
            "-two",
            "+three",
            " four",
        ]
    )

    widgets = list(compose_diff_lines(diff, max_lines=2))

    assert [_static_text(widget) for widget in widgets] == [
        "+1 -1",
        ":  1  one",
        "|  2 two",
        "\n... (3 more lines)",
    ]


def test_enhanced_diff_computes_stats_and_composes_title_and_footer() -> None:
    widget = EnhancedDiff(
        "\n".join(
            [
                "--- before.txt",
                "+++ after.txt",
                "@@ -1 +1 @@",
                "-old",
                "+new",
            ]
        ),
        title="Patch",
    )

    assert widget._stats == (1, 1)

    children = list(widget.compose())

    assert _static_text(children[0]) == "=== Patch ==="
    assert "diff-title" in children[0].classes
    assert _static_text(children[-1]) == "+1 -1"
    assert "diff-stats" in children[-1].classes


def test_enhanced_diff_omits_footer_when_no_stats() -> None:
    widget = EnhancedDiff("metadata only", title="Patch")

    assert widget._stats == (0, 0)
    assert [_static_text(child) for child in widget.compose()] == [
        "=== Patch ===",
        "metadata only",
    ]


def test_enhanced_diff_uses_ascii_border_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(diff_mod, "is_ascii_mode", lambda: True)
    widget = EnhancedDiff("")

    widget.on_mount()

    assert {edge[0] for edge in widget.styles.border} == {"ascii"}


def test_enhanced_diff_keeps_default_border_outside_ascii_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(diff_mod, "is_ascii_mode", lambda: False)
    widget = EnhancedDiff("")

    widget.on_mount()

    assert {edge[0] for edge in widget.styles.border} != {"ascii"}
