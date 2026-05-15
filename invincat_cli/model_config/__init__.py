"""Model configuration management.

Handles loading and saving model configuration from TOML files, providing a
structured way to define available models and providers.
"""

from __future__ import annotations

import copy
import importlib as importlib
import logging
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import tomli_w as tomli_w  # noqa: F401

from invincat_cli.config.paths import (
    DEFAULT_CONFIG_DIR as DEFAULT_CONFIG_DIR,
)
from invincat_cli.config.paths import (
    DEFAULT_CONFIG_PATH,
)
from invincat_cli.model_config.types import (
    ModelConfigError as ModelConfigError,
)
from invincat_cli.model_config.types import (
    ModelProfileEntry as ModelProfileEntry,
)
from invincat_cli.model_config.types import (
    ModelSpec as ModelSpec,
)
from invincat_cli.model_config.types import (
    ModelTarget as ModelTarget,
)
from invincat_cli.model_config.types import (
    ProviderConfig,
)
from invincat_cli.thread_config import (
    THREAD_COLUMN_DEFAULTS as THREAD_COLUMN_DEFAULTS,
)
from invincat_cli.thread_config import (
    ThreadConfig as ThreadConfig,
)

logger = logging.getLogger(__name__)

_ENV_PREFIX = "DEEPAGENTS_CLI_"


def resolve_env_var(name: str) -> str | None:
    """Look up an env var with `DEEPAGENTS_CLI_` prefix override.

    Checks `DEEPAGENTS_CLI_{name}` first, then falls back to `{name}`.
    If the prefixed variable is present, even as an empty string, the
    canonical variable is never consulted.
    """
    if not name.startswith(_ENV_PREFIX):
        prefixed = f"{_ENV_PREFIX}{name}"
        if prefixed in os.environ:
            val = os.environ[prefixed]
            if not val and os.environ.get(name):
                logger.debug(
                    "%s is set but empty, blocking non-empty %s. "
                    "Unset %s to use the canonical variable.",
                    prefixed,
                    name,
                    prefixed,
                )
            return val or None
    return os.environ.get(name) or None

from invincat_cli.model_config.profiles import (  # noqa: E402, F401
    PROVIDER_API_KEY_ENV,
    _available_models_cache,
    _build_entry,
    _builtin_providers_cache,
    _get_builtin_providers,
    _get_provider_profile_modules,
    _load_provider_profiles,
    _profile_module_from_class_path,
    _profiles_cache,
    _profiles_override_cache,
    _provider_profiles_cache,
    clear_profile_caches,
    get_available_models,
    get_credential_env_var,
    get_model_profiles,
    has_provider_credentials,
)

_default_config_cache: ModelConfig | None = None


def clear_caches() -> None:
    """Reset model, provider, and thread config caches."""
    global _default_config_cache  # noqa: PLW0603
    _default_config_cache = None
    clear_profile_caches()
    invalidate_thread_config_cache()

    from invincat_cli.config import reset_settings_cache

    reset_settings_cache()


@dataclass(frozen=True)
class ModelConfig:
    """Parsed model configuration from `config.toml`.

    Instances are immutable once constructed. The `providers` mapping is
    wrapped in `MappingProxyType` to prevent accidental mutation of the
    globally cached singleton returned by `load()`.
    """

    default_model: str | None = None
    """The user's intentional default model (from config file `[models].default`)."""

    recent_model: str | None = None
    """The most recently switched-to model (from config file `[models].recent`)."""

    memory_default_model: str | None = None
    """Dedicated default for memory agent (from `[models].memory_default`)."""

    providers: Mapping[str, ProviderConfig] = field(default_factory=dict)
    """Read-only mapping of provider names to their configurations."""

    target_params: Mapping[str, Mapping[str, dict[str, Any]]] = field(
        default_factory=dict
    )
    """Read-only target-specific model params keyed by target then provider:model."""

    def __post_init__(self) -> None:
        """Freeze the providers dict into a read-only proxy."""
        if not isinstance(self.providers, MappingProxyType):
            object.__setattr__(self, "providers", MappingProxyType(self.providers))
        if not isinstance(self.target_params, MappingProxyType):
            object.__setattr__(
                self, "target_params", MappingProxyType(self.target_params)
            )

    @classmethod
    def load(cls, config_path: Path | None = None) -> ModelConfig:
        """Load config from file.

        When called with the default path, results are cached for the
        lifetime of the process. Use `clear_caches()` to reset.

        Args:
            config_path: Path to config file. Defaults to ~/.invincat/config.toml.

        Returns:
            Parsed `ModelConfig` instance.
                Returns empty config if file is missing, unreadable, or contains
                invalid TOML syntax.
        """
        global _default_config_cache  # noqa: PLW0603  # Module-level cache requires global statement
        is_default = config_path is None
        if is_default and _default_config_cache is not None:
            return _default_config_cache

        if config_path is None:
            config_path = DEFAULT_CONFIG_PATH

        if not config_path.exists():
            fallback = cls()
            if is_default:
                _default_config_cache = fallback
            return fallback

        try:
            with config_path.open("rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            logger.warning(
                "Config file %s has invalid TOML syntax: %s. "
                "Ignoring config file. Fix the file or delete it to reset.",
                config_path,
                e,
            )
            fallback = cls()
            if is_default:
                _default_config_cache = fallback
            return fallback
        except (PermissionError, OSError) as e:
            logger.warning("Could not read config file %s: %s", config_path, e)
            fallback = cls()
            if is_default:
                _default_config_cache = fallback
            return fallback

        models_section = data.get("models", {})
        config = cls(
            default_model=models_section.get("default"),
            recent_model=models_section.get("recent"),
            memory_default_model=models_section.get("memory_default"),
            providers=models_section.get("providers", {}),
            target_params=models_section.get("target_params", {}),
        )

        # Validate config consistency
        config._validate()

        if is_default:
            _default_config_cache = config

        return config

    def _validate(self) -> None:
        """Validate internal consistency of the config.

        Issues warnings for invalid configurations but does not raise exceptions,
        allowing the app to continue with potentially degraded functionality.
        """
        # Warn if default_model is set but doesn't use provider:model format
        if self.default_model and ":" not in self.default_model:
            logger.warning(
                "default_model '%s' should use provider:model format "
                "(e.g., 'anthropic:claude-sonnet-4-5')",
                self.default_model,
            )

        # Warn if recent_model is set but doesn't use provider:model format
        if self.recent_model and ":" not in self.recent_model:
            logger.warning(
                "recent_model '%s' should use provider:model format "
                "(e.g., 'anthropic:claude-sonnet-4-5')",
                self.recent_model,
            )

        # Warn if memory_default_model is set but doesn't use provider:model format
        if self.memory_default_model and ":" not in self.memory_default_model:
            logger.warning(
                "memory_default_model '%s' should use provider:model format "
                "(e.g., 'openai:gpt-5.2')",
                self.memory_default_model,
            )

        # Validate enabled field type and class_path format / params references
        for name, provider in self.providers.items():
            enabled = provider.get("enabled")
            if enabled is not None and not isinstance(enabled, bool):
                logger.warning(
                    "Provider '%s' has non-boolean 'enabled' value %r "
                    "(expected true/false). Provider will remain visible.",
                    name,
                    enabled,
                )

            class_path = provider.get("class_path")
            if class_path and ":" not in class_path:
                logger.warning(
                    "Provider '%s' has invalid class_path '%s': "
                    "must be in module.path:ClassName format "
                    "(e.g., 'my_package.models:MyChatModel')",
                    name,
                    class_path,
                )

            models = set(provider.get("models", []))

            params = provider.get("params", {})
            for key, value in params.items():
                if isinstance(value, dict) and key not in models:
                    logger.warning(
                        "Provider '%s' has params for '%s' "
                        "which is not in its models list",
                        name,
                        key,
                    )

    def is_provider_enabled(self, provider_name: str) -> bool:
        """Check whether a provider should appear in the model switcher.

        A provider is disabled when its config explicitly sets
        `enabled = false`. Providers not present in the config file are
        always considered enabled.

        Args:
            provider_name: The provider to check.

        Returns:
            `False` if the provider is explicitly disabled, `True` otherwise.
        """
        provider = self.providers.get(provider_name)
        if not provider:
            return True
        return provider.get("enabled") is not False

    def get_all_models(self) -> list[tuple[str, str]]:
        """Get all models as `(model_name, provider_name)` tuples.

        Returns raw config data — does not filter by `is_provider_enabled`.
        For the filtered set shown in the model switcher, use
        `get_available_models()`.

        Returns:
            List of tuples containing `(model_name, provider_name)`.
        """
        return [
            (model, provider_name)
            for provider_name, provider_config in self.providers.items()
            for model in provider_config.get("models", [])
        ]

    def get_provider_for_model(self, model_name: str) -> str | None:
        """Find the provider that contains this model.

        Returns raw config data — does not filter by `is_provider_enabled`.

        Args:
            model_name: The model identifier to look up.

        Returns:
            Provider name if found, None otherwise.
        """
        for provider_name, provider_config in self.providers.items():
            if model_name in provider_config.get("models", []):
                return provider_name
        return None

    def has_credentials(self, provider_name: str) -> bool | None:
        """Check if credentials are available for a provider.

        This is the config-file-driven credential check, supporting custom
        providers (e.g., local Ollama with no key required). For the hardcoded
        `PROVIDER_API_KEY_ENV`-based check used in the hot-swap path, see the
        module-level `has_provider_credentials()`.

        Args:
            provider_name: The provider to check.

        Returns:
            True if credentials are confirmed available, False if confirmed
                missing, or None if no `api_key_env` is configured and
                credential status cannot be determined.
        """
        provider = self.providers.get(provider_name)
        if not provider:
            return False
        env_var = provider.get("api_key_env")
        if not env_var:
            return None  # No key configured — can't verify
        return bool(resolve_env_var(env_var))

    def get_base_url(self, provider_name: str) -> str | None:
        """Get custom base URL.

        Args:
            provider_name: The provider to get base URL for.

        Returns:
            Base URL if configured, None otherwise.
        """
        provider = self.providers.get(provider_name)
        return provider.get("base_url") if provider else None

    def get_api_key_env(self, provider_name: str) -> str | None:
        """Get the environment variable name for a provider's API key.

        Args:
            provider_name: The provider to get API key env var for.

        Returns:
            Environment variable name if configured, None otherwise.
        """
        provider = self.providers.get(provider_name)
        return provider.get("api_key_env") if provider else None

    def get_class_path(self, provider_name: str) -> str | None:
        """Get the custom class path for a provider.

        Args:
            provider_name: The provider to look up.

        Returns:
            Class path in `module.path:ClassName` format, or None.
        """
        provider = self.providers.get(provider_name)
        return provider.get("class_path") if provider else None

    def get_kwargs(
        self, provider_name: str, *, model_name: str | None = None
    ) -> dict[str, Any]:
        """Get extra constructor kwargs for a provider.

        Reads the `params` table from the provider config. Flat keys are
        provider-wide defaults; model-keyed sub-tables are per-model
        overrides that shallow-merge on top (model wins on conflict).

        Args:
            provider_name: The provider to look up.
            model_name: Optional model name for per-model overrides.

        Returns:
            Dictionary of extra kwargs (empty if none configured).
        """
        provider = self.providers.get(provider_name)
        if not provider:
            return {}
        params = provider.get("params", {})
        result = {
            k: copy.deepcopy(v) for k, v in params.items() if not isinstance(v, dict)
        }
        if model_name:
            overrides = params.get(model_name)
            if isinstance(overrides, dict):
                result.update(copy.deepcopy(overrides))
        return result

    def get_profile_overrides(
        self, provider_name: str, *, model_name: str | None = None
    ) -> dict[str, Any]:
        """Get profile overrides for a provider.

        Reads the `profile` table from the provider config. Flat keys are
        provider-wide defaults; model-keyed sub-tables are per-model overrides
        that shallow-merge on top (model wins on conflict).

        Args:
            provider_name: The provider to look up.
            model_name: Optional model name for per-model overrides.

        Returns:
            Dictionary of profile overrides (empty if none configured).
        """
        provider = self.providers.get(provider_name)
        if not provider:
            return {}
        profile = provider.get("profile", {})
        result = {
            k: copy.deepcopy(v) for k, v in profile.items() if not isinstance(v, dict)
        }
        if model_name:
            overrides = profile.get(model_name)
            if isinstance(overrides, dict):
                result.update(copy.deepcopy(overrides))
        return result

    def get_target_model_params(
        self, target: ModelTarget, model_spec: str
    ) -> dict[str, Any]:
        """Get target-specific constructor params for a model spec.

        Args:
            target: Model target (`primary` or `memory`).
            model_spec: Provider-qualified model spec.

        Returns:
            Deep-copied target params, or an empty dict when not configured.
        """
        target_table = self.target_params.get(target)
        if not isinstance(target_table, Mapping):
            return {}
        params = target_table.get(model_spec)
        return copy.deepcopy(params) if isinstance(params, dict) else {}


from invincat_cli.model_config.persistence import (  # noqa: E402, F401
    _clear_model_field,
    _deep_merge_dict,
    _save_model_field,
    clear_default_model,
    clear_memory_default_model,
    get_target_model_params,
    is_warning_suppressed,
    register_provider_model,
    save_default_model,
    save_memory_default_model,
    save_recent_model,
    save_target_model_params,
    suppress_warning,
)
from invincat_cli.model_config.thread_preferences import (  # noqa: E402, F401
    invalidate_thread_config_cache,
    load_thread_columns,
    load_thread_config,
    load_thread_relative_time,
    load_thread_sort_order,
    save_thread_columns,
    save_thread_relative_time,
    save_thread_sort_order,
)
