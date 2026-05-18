"""Tests for `python -m invincat_cli` entrypoint wiring."""

from __future__ import annotations

import runpy
import subprocess
import sys
from types import ModuleType

import invincat_cli


def test_module_entrypoint_calls_cli_main(monkeypatch) -> None:
    calls: list[str] = []
    fake_main = ModuleType("invincat_cli.main")
    fake_main.cli_main = lambda: calls.append("called")
    monkeypatch.setitem(sys.modules, "invincat_cli.main", fake_main)
    monkeypatch.setattr(invincat_cli, "main", fake_main, raising=False)
    monkeypatch.setattr(sys, "argv", ["python", "-m", "invincat_cli"])

    runpy.run_module("invincat_cli.__main__", run_name="__main__")

    assert calls == ["called"]


def test_main_package_entrypoint_is_runnable() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "invincat_cli.main", "--version"],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "invincat-cli" in result.stdout
