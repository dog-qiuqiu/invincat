from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from invincat_cli.widgets.history import HistoryManager


def test_history_loads_json_lines_plain_lines_and_trims_to_max(
    tmp_path: Path,
) -> None:
    history_file = tmp_path / "history.jsonl"
    history_file.write_text(
        "\n".join(
            [
                json.dumps("first"),
                "not-json",
                json.dumps({"content": "object"}),
                "",
                json.dumps("last"),
            ]
        ),
        encoding="utf-8",
    )

    history = HistoryManager(history_file, max_entries=3)

    assert history._entries == ["not-json", "{'content': 'object'}", "last"]


def test_history_load_failure_starts_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history_file = tmp_path / "history.jsonl"
    history_file.write_text("entry", encoding="utf-8")

    def fail_open(self: Path, *_args: Any, **_kwargs: Any):
        if self == history_file:
            raise UnicodeDecodeError("utf-8", b"x", 0, 1, "bad")
        return original_open(self, *_args, **_kwargs)

    original_open = type(history_file).open
    monkeypatch.setattr(type(history_file), "open", fail_open)

    history = HistoryManager(history_file)

    assert history._entries == []


def test_history_add_filters_duplicates_commands_and_compacts(
    tmp_path: Path,
) -> None:
    history_file = tmp_path / "history.jsonl"
    history = HistoryManager(history_file, max_entries=2)

    history.add("")
    history.add("   /help   ")
    history.add(" first ")
    history.add("first")
    history.add("second")
    history.add("third")
    history.add("fourth")
    history.add("fifth")

    assert history._entries == ["fourth", "fifth"]
    assert history.in_history is False
    assert history_file.read_text(encoding="utf-8").splitlines() == [
        json.dumps("fourth"),
        json.dumps("fifth"),
    ]


def test_history_append_and_compact_failures_are_tolerated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history_file = tmp_path / "history.jsonl"
    history = HistoryManager(history_file, max_entries=1)

    def fail_open(self: Path, *_args: Any, **_kwargs: Any):
        if self == history_file:
            raise OSError("disk full")
        return original_open(self, *_args, **_kwargs)

    original_open = type(history_file).open
    monkeypatch.setattr(type(history_file), "open", fail_open)

    history.add("first")
    history.add("second")
    history.add("third")

    assert history._entries == ["third"]


def test_history_navigation_tracks_temp_input_and_query(tmp_path: Path) -> None:
    history = HistoryManager(tmp_path / "history.jsonl")
    assert history.get_previous("draft") is None

    for entry in ["deploy staging", "test unit", "deploy prod"]:
        history.add(entry)

    assert history.get_previous("draft", query="deploy") == "deploy prod"
    assert history.in_history is True
    assert history.get_previous("ignored", query="test") == "deploy staging"
    assert history.get_previous("ignored") is None
    assert history.get_next() == "deploy prod"
    assert history.get_next() == "draft"
    assert history.in_history is False
    assert history.get_next() is None


def test_history_reset_navigation_clears_state(tmp_path: Path) -> None:
    history = HistoryManager(tmp_path / "history.jsonl")
    history.add("one")

    assert history.get_previous("draft") == "one"
    history.reset_navigation()

    assert history.in_history is False
    assert history._temp_input == ""
    assert history._query == ""
