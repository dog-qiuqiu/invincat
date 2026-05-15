"""Tests for `python -m invincat_cli` entrypoint wiring."""

from __future__ import annotations

import runpy
import sys
from types import ModuleType


def test_module_entrypoint_calls_cli_main(monkeypatch) -> None:
    calls: list[str] = []
    fake_main = ModuleType("invincat_cli.main")
    fake_main.cli_main = lambda: calls.append("called")
    monkeypatch.setitem(sys.modules, "invincat_cli.main", fake_main)

    runpy.run_module("invincat_cli.__main__", run_name="__main__")

    assert calls == ["called"]
