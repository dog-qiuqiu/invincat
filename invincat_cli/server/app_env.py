"""Scoped environment variable mutation helpers."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from types import ModuleType


@contextlib.contextmanager
def scoped_env_overrides(
    overrides: dict[str, str],
    *,
    os_module: ModuleType = os,
) -> Iterator[None]:
    """Apply env-var overrides, rolling back only on exception."""
    prev: dict[str, str | None] = {}
    for key, val in overrides.items():
        prev[key] = os_module.environ.get(key)
        os_module.environ[key] = val
    try:
        yield
    except Exception:
        for key, old_val in prev.items():
            if old_val is None:
                os_module.environ.pop(key, None)
            else:
                os_module.environ[key] = old_val
        raise
