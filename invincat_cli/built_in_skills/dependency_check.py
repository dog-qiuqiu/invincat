"""Dependency checks for standalone built-in skill scripts."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType


def require_module(module_name: str, extra: str) -> ModuleType:
    """Import a module or exit with the matching extras install hint."""
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        print(
            f"Missing optional dependency '{module_name}'. "
            f"Install it with: pip install \"invincat-cli[{extra}]\"",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
