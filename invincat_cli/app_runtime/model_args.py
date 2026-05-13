"""Parsing helpers for `/model` command arguments."""

from __future__ import annotations

import json
import shlex
from typing import Any

from invincat_cli.model_config import ModelTarget


def extract_model_params_flag(raw_arg: str) -> tuple[str, dict[str, Any] | None]:
    """Extract `--model-params` and its JSON value from a `/model` arg string."""
    flag = "--model-params"
    idx = raw_arg.find(flag)
    if idx == -1:
        return raw_arg, None

    before = raw_arg[:idx].rstrip()
    after = raw_arg[idx + len(flag) :].lstrip()

    if not after:
        msg = "--model-params requires a JSON object value"
        raise ValueError(msg)

    if after[0] in {"'", '"'}:
        quote = after[0]
        end = -1
        backslash_count = 0
        for i, ch in enumerate(after[1:], start=1):
            if ch == "\\":
                backslash_count += 1
                continue
            if ch == quote and backslash_count % 2 == 0:
                end = i
                break
            backslash_count = 0
        if end == -1:
            msg = f"Unclosed {quote} in --model-params value"
            raise ValueError(msg)
        json_str = shlex.split(after[: end + 1], posix=True)[0]
        rest = after[end + 1 :].lstrip()
    elif after[0] == "{":
        depth = 0
        end = -1
        for i, ch in enumerate(after):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end == -1:
            msg = "Unbalanced braces in --model-params value"
            raise ValueError(msg)
        json_str = after[: end + 1]
        rest = after[end + 1 :].lstrip()
    else:
        parts = after.split(None, 1)
        json_str = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

    remaining = f"{before} {rest}".strip()
    try:
        params = json.loads(json_str)
    except json.JSONDecodeError:
        msg = (
            f"Invalid JSON in --model-params: {json_str!r}. "
            'Expected format: --model-params \'{"key": "value"}\''
        )
        raise ValueError(msg) from None
    if not isinstance(params, dict):
        msg = "--model-params must be a JSON object, got " + type(params).__name__
        raise TypeError(msg)
    return remaining, params


def parse_model_target(raw_arg: str) -> tuple[ModelTarget, str]:
    """Parse optional model-target prefix from `/model` args."""
    stripped = raw_arg.strip()
    if not stripped:
        return "primary", ""

    first, *rest = stripped.split(maxsplit=1)
    first_norm = first.strip().lower()
    if first_norm in {"1", "primary", "main"}:
        return "primary", rest[0].strip() if rest else ""
    if first_norm in {"2", "memory", "secondary"}:
        return "memory", rest[0].strip() if rest else ""
    return "primary", stripped


def split_model_spec(spec: str | None) -> tuple[str, str]:
    """Split `provider:model` spec for status display fallback logic."""
    if not spec:
        return "", ""
    if ":" in spec:
        provider, model = spec.split(":", 1)
        return provider.strip(), model.strip()
    return "", spec.strip()
