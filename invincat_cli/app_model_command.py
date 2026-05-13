"""Pure parsing for the Textual app `/model` command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from invincat_cli.app_model_args import (
    extract_model_params_flag,
    parse_model_target,
)
from invincat_cli.model_config import ModelTarget

ModelCommandKind = Literal[
    "selector",
    "switch",
    "set_default",
    "clear_default",
    "usage",
    "error",
]

MODEL_DEFAULT_USAGE = (
    "Usage: /model [1|2] --default provider:model\n"
    "       /model [1|2] --default --clear"
)


@dataclass(frozen=True, slots=True)
class ModelCommandAction:
    """Parsed `/model` command action."""

    kind: ModelCommandKind
    target: ModelTarget = "primary"
    model_arg: str | None = None
    extra_kwargs: dict[str, Any] | None = None
    error: str | None = None


def parse_model_command(command: str) -> ModelCommandAction:
    """Parse a full `/model` command into an executable app action."""
    raw_arg = command.strip()[len("/model") :].strip()
    if not raw_arg:
        return ModelCommandAction(kind="selector")

    try:
        raw_arg, extra_kwargs = extract_model_params_flag(raw_arg)
    except (ValueError, TypeError) as exc:
        return ModelCommandAction(kind="error", error=str(exc))

    target, raw_arg = parse_model_target(raw_arg)
    if not raw_arg:
        return ModelCommandAction(
            kind="selector",
            target=target,
            extra_kwargs=extra_kwargs,
        )

    if raw_arg.startswith("--default"):
        model_arg = raw_arg[len("--default") :].strip() or None
        if extra_kwargs:
            return ModelCommandAction(
                kind="error",
                target=target,
                error=(
                    "--model-params cannot be used with --default. "
                    "Model params are applied per-session, not persisted."
                ),
            )
        if model_arg == "--clear":
            return ModelCommandAction(kind="clear_default", target=target)
        if model_arg:
            return ModelCommandAction(
                kind="set_default",
                target=target,
                model_arg=model_arg,
            )
        return ModelCommandAction(kind="usage", target=target)

    return ModelCommandAction(
        kind="switch",
        target=target,
        model_arg=raw_arg,
        extra_kwargs=extra_kwargs,
    )
