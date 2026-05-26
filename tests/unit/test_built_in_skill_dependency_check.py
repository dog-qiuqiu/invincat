from __future__ import annotations

import importlib

import pytest

from invincat_cli.built_in_skills.dependency_check import require_module


def test_require_module_returns_imported_module() -> None:
    assert require_module("importlib", "pdf") is importlib


def test_require_module_exits_with_extra_hint(monkeypatch, capsys) -> None:
    def fail_import(name: str) -> object:
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", fail_import)

    with pytest.raises(SystemExit) as exc_info:
        require_module("missing_pdf_lib", "pdf")

    assert exc_info.value.code == 1
    assert 'pip install "invincat-cli[pdf]"' in capsys.readouterr().err
