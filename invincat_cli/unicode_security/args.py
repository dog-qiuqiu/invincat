"""Recursive argument inspection helpers for Unicode safety checks."""

from __future__ import annotations

from typing import Any

URL_ARG_KEYS: frozenset[str] = frozenset(
    {"url", "uri", "href", "link", "base_url", "endpoint"}
)
"""Argument key names that likely contain URLs and should be safety-checked."""


def iter_string_values(
    data: dict[str, Any],
    *,
    prefix: str = "",
) -> list[tuple[str, str]]:
    """Flatten nested dict/list structures into key-path/string pairs."""
    values: list[tuple[str, str]] = []
    for key, value in data.items():
        key_path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, str):
            values.append((key_path, value))
            continue
        if isinstance(value, dict):
            values.extend(iter_string_values(value, prefix=key_path))
            continue
        if isinstance(value, list):
            values.extend(_iter_string_values_from_list(value, prefix=key_path))
    return values


def _iter_string_values_from_list(
    values: list[Any],
    *,
    prefix: str,
) -> list[tuple[str, str]]:
    """Flatten nested list values into key-path/string pairs."""
    entries: list[tuple[str, str]] = []
    for index, value in enumerate(values):
        key_path = f"{prefix}[{index}]"
        if isinstance(value, str):
            entries.append((key_path, value))
            continue
        if isinstance(value, dict):
            entries.extend(iter_string_values(value, prefix=key_path))
            continue
        if isinstance(value, list):
            entries.extend(_iter_string_values_from_list(value, prefix=key_path))
    return entries


def looks_like_url_key(arg_path: str) -> bool:
    """Return whether a key path suggests URL-like content."""
    key = arg_path.rsplit(".", maxsplit=1)[-1]
    key = key.split("[", maxsplit=1)[0].lower()
    return key in URL_ARG_KEYS
