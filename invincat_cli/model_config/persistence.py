"""Persistence helpers for model configuration files."""

from __future__ import annotations

import contextlib
import copy
import logging
import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

from invincat_cli.model_config.types import ModelTarget

logger = logging.getLogger(__name__)


def _default_config_path() -> Path:
    from invincat_cli import model_config as _model_config

    return _model_config.DEFAULT_CONFIG_PATH


def _invalidate_default_model_config_cache() -> None:
    from invincat_cli import model_config as _model_config

    _model_config._default_config_cache = None  # noqa: SLF001


def _save_model_field(
    field: str, model_spec: str, config_path: Path | None = None
) -> bool:
    """Read-modify-write a `[models].<field>` key in the config file."""
    if config_path is None:
        config_path = _default_config_path()

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)

        if config_path.exists():
            with config_path.open("rb") as f:
                data = tomllib.load(f)
        else:
            data = {}

        if "models" not in data:
            data["models"] = {}
        data["models"][field] = model_spec

        fd, tmp_path = tempfile.mkstemp(dir=config_path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(data, f)
            Path(tmp_path).replace(config_path)
        except BaseException:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()
            raise
    except (OSError, tomllib.TOMLDecodeError):
        logger.exception("Could not save %s model preference", field)
        return False
    else:
        _invalidate_default_model_config_cache()
        return True


def save_default_model(model_spec: str, config_path: Path | None = None) -> bool:
    """Update the default model in config file."""
    return _save_model_field("default", model_spec, config_path)


def clear_default_model(config_path: Path | None = None) -> bool:
    """Remove the default model from the config file."""
    return _clear_model_field("default", config_path)


def save_memory_default_model(model_spec: str, config_path: Path | None = None) -> bool:
    """Update the dedicated memory default model in config file."""
    return _save_model_field("memory_default", model_spec, config_path)


def clear_memory_default_model(config_path: Path | None = None) -> bool:
    """Remove the dedicated memory default model from the config file."""
    return _clear_model_field("memory_default", config_path)


def get_target_model_params(
    target: ModelTarget,
    model_spec: str,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Load target-specific constructor params for a model spec."""
    from invincat_cli import model_config as _model_config

    return _model_config.ModelConfig.load(config_path).get_target_model_params(
        target, model_spec
    )


def save_target_model_params(
    target: ModelTarget,
    model_spec: str,
    params: dict[str, Any] | None,
    config_path: Path | None = None,
) -> bool:
    """Save target-specific constructor params for a model spec."""
    if config_path is None:
        config_path = _default_config_path()

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if config_path.exists():
            with config_path.open("rb") as f:
                data = tomllib.load(f)
        else:
            data = {}

        models_section = data.setdefault("models", {})
        target_params = models_section.setdefault("target_params", {})
        target_table = target_params.setdefault(target, {})

        if params:
            target_table[model_spec] = copy.deepcopy(params)
        else:
            target_table.pop(model_spec, None)
            if not target_table:
                target_params.pop(target, None)
            if not target_params:
                models_section.pop("target_params", None)

        fd, tmp_path = tempfile.mkstemp(dir=config_path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(data, f)
            Path(tmp_path).replace(config_path)
        except BaseException:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()
            raise
    except (OSError, tomllib.TOMLDecodeError):
        logger.exception(
            "Could not save %s target params for model %s", target, model_spec
        )
        return False
    else:
        _invalidate_default_model_config_cache()
        return True


def _clear_model_field(field: str, config_path: Path | None = None) -> bool:
    """Remove a single key from `[models]` in the config file."""
    if config_path is None:
        config_path = _default_config_path()

    if not config_path.exists():
        return True

    try:
        with config_path.open("rb") as f:
            data = tomllib.load(f)

        models_section = data.get("models")
        if not isinstance(models_section, dict) or field not in models_section:
            return True

        del models_section[field]

        fd, tmp_path = tempfile.mkstemp(dir=config_path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(data, f)
            Path(tmp_path).replace(config_path)
        except BaseException:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()
            raise
    except (OSError, tomllib.TOMLDecodeError):
        logger.exception("Could not clear %s model preference", field)
        return False
    else:
        _invalidate_default_model_config_cache()
        return True


def is_warning_suppressed(key: str, config_path: Path | None = None) -> bool:
    """Check if a warning key is suppressed in the config file."""
    if config_path is None:
        config_path = _default_config_path()

    try:
        if not config_path.exists():
            return False
        with config_path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        logger.debug(
            "Could not read config file %s for warning suppression check",
            config_path,
            exc_info=True,
        )
        return False

    suppress_list = data.get("warnings", {}).get("suppress", [])
    if not isinstance(suppress_list, list):
        logger.debug(
            "[warnings].suppress in %s should be a list, got %s",
            config_path,
            type(suppress_list).__name__,
        )
        return False
    return key in suppress_list


def suppress_warning(key: str, config_path: Path | None = None) -> bool:
    """Add a warning key to the suppression list in the config file."""
    if config_path is None:
        config_path = _default_config_path()

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)

        if config_path.exists():
            with config_path.open("rb") as f:
                data = tomllib.load(f)
        else:
            data = {}

        if "warnings" not in data:
            data["warnings"] = {}
        suppress_list: list[str] = data["warnings"].get("suppress", [])
        if key not in suppress_list:
            suppress_list.append(key)
        data["warnings"]["suppress"] = suppress_list

        fd, tmp_path = tempfile.mkstemp(dir=config_path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(data, f)
            Path(tmp_path).replace(config_path)
        except BaseException:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()
            raise
    except (OSError, tomllib.TOMLDecodeError):
        logger.exception("Could not save warning suppression for '%s'", key)
        return False
    return True


def save_recent_model(model_spec: str, config_path: Path | None = None) -> bool:
    """Update the recently used model in config file."""
    return _save_model_field("recent", model_spec, config_path)


def _deep_merge_dict(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge `source` into `target`, recursing into nested dict values."""
    for key, value in source.items():
        existing = target.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            _deep_merge_dict(existing, value)
        else:
            target[key] = value


def register_provider_model(
    provider_name: str,
    model_name: str,
    *,
    api_key_env: str | None = None,
    base_url: str | None = None,
    max_input_tokens: int | None = None,
    extra_params: dict[str, Any] | None = None,
    class_path: str | None = None,
    config_path: Path | None = None,
) -> bool:
    """Register a new model under a provider in the config file."""
    from invincat_cli import model_config as _model_config

    if config_path is None:
        config_path = _default_config_path()

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)

        if config_path.exists():
            with config_path.open("rb") as f:
                data = tomllib.load(f)
        else:
            data = {}

        models_section = data.setdefault("models", {})
        providers_section = models_section.setdefault("providers", {})
        provider_cfg = providers_section.setdefault(provider_name, {})

        models_list: list[str] = provider_cfg.get("models", [])
        if model_name not in models_list:
            models_list.append(model_name)
        provider_cfg["models"] = models_list

        if "class_path" not in provider_cfg and class_path:
            provider_cfg["class_path"] = class_path

        if base_url or api_key_env or extra_params:
            params_section = provider_cfg.setdefault("params", {})
            model_params = params_section.setdefault(model_name, {})
            if base_url:
                model_params["base_url"] = base_url
            if api_key_env:
                model_params["api_key_env"] = api_key_env
            if extra_params:
                _deep_merge_dict(model_params, extra_params)

        if max_input_tokens is not None:
            profile = provider_cfg.setdefault("profile", {})
            model_profile = profile.setdefault(model_name, {})
            model_profile["max_input_tokens"] = max_input_tokens

        fd, tmp_path = tempfile.mkstemp(dir=config_path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(data, f)
            Path(tmp_path).replace(config_path)
        except BaseException:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()
            raise
    except (OSError, tomllib.TOMLDecodeError):
        logger.exception("Could not register model %s:%s", provider_name, model_name)
        return False
    else:
        _model_config.clear_caches()
        return True
