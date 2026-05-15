"""Tests for machine-readable CLI output helpers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from invincat_cli.io.output import add_json_output_arg, write_json


def test_add_json_output_arg_sets_default_for_root_parser() -> None:
    parser = argparse.ArgumentParser()
    add_json_output_arg(parser, default="text")

    assert parser.parse_args([]).output_format == "text"
    assert parser.parse_args(["--json"]).output_format == "json"


def test_add_json_output_arg_preserves_parent_default_for_subparser() -> None:
    parser = argparse.ArgumentParser()
    add_json_output_arg(parser)

    assert not hasattr(parser.parse_args([]), "output_format")
    assert parser.parse_args(["--json"]).output_format == "json"


def test_write_json_emits_stable_single_line_envelope(capsys) -> None:
    write_json("example", {"path": Path("notes.md")})

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.endswith("\n")
    assert json.loads(captured.out) == {
        "schema_version": 1,
        "command": "example",
        "data": {"path": "notes.md"},
    }
