"""Provider model discovery, profile loading, and credential checks."""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from invincat_cli.model_config.types import ModelProfileEntry

logger = logging.getLogger(__name__)

PROVIDER_API_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "baseten": "BASETEN_API_KEY",
    "cohere": "COHERE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "google_vertexai": "GOOGLE_CLOUD_PROJECT",
    "groq": "GROQ_API_KEY",
    "huggingface": "HUGGINGFACEHUB_API_TOKEN",
    "ibm": "WATSONX_APIKEY",
    "litellm": "LITELLM_API_KEY",
    "mistralai": "MISTRAL_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "perplexity": "PPLX_API_KEY",
    "together": "TOGETHER_API_KEY",
    "xai": "XAI_API_KEY",
}
"""Well-known providers mapped to their API key environment variable."""

_available_models_cache: dict[str, list[str]] | None = None
_builtin_providers_cache: dict[str, Any] | None = None
_provider_profiles_cache: dict[str, dict[str, Any]] = {}
_provider_profiles_lock = threading.Lock()
_profiles_cache: Mapping[str, ModelProfileEntry] | None = None
_profiles_override_cache: tuple[int, Mapping[str, ModelProfileEntry]] | None = None


def clear_profile_caches() -> None:
    """Reset provider/model profile caches."""
    global _available_models_cache, _builtin_providers_cache, _profiles_cache, _profiles_override_cache  # noqa: PLW0603, E501
    _available_models_cache = None
    _builtin_providers_cache = None
    _provider_profiles_cache.clear()
    _profiles_cache = None
    _profiles_override_cache = None


def _get_builtin_providers() -> dict[str, Any]:
    """Return langchain's built-in provider registry."""
    global _builtin_providers_cache  # noqa: PLW0603
    if _builtin_providers_cache is not None:
        return _builtin_providers_cache

    from langchain.chat_models import base

    registry: dict[str, Any] | None = getattr(base, "_BUILTIN_PROVIDERS", None)
    if registry is None:
        registry = getattr(base, "_SUPPORTED_PROVIDERS", None)
    _builtin_providers_cache = registry if registry is not None else {}
    return _builtin_providers_cache


def _get_provider_profile_modules() -> list[tuple[str, str]]:
    """Build a `(provider, profile_module)` list from langchain's registry."""
    from invincat_cli import model_config as _model_config

    providers = _model_config._get_builtin_providers()  # noqa: SLF001
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for provider_name, (module_path, *_rest) in providers.items():
        package_root = module_path.split(".", maxsplit=1)[0]
        profile_module = f"{package_root}.data._profiles"
        key = (provider_name, profile_module)
        if key not in seen:
            seen.add(key)
            result.append((provider_name, profile_module))

    return result


def _load_provider_profiles(module_path: str) -> dict[str, Any]:
    """Load `_PROFILES` from a provider's data module."""
    from invincat_cli import model_config as _model_config

    with _provider_profiles_lock:
        cached = _provider_profiles_cache.get(module_path)
        if cached is not None:
            return cached

        parts = module_path.split(".")
        package_root = parts[0]

        spec = _model_config.importlib.util.find_spec(package_root)
        if spec is None:
            msg = f"Package {package_root} is not installed"
            raise ImportError(msg)

        if spec.origin:
            package_dir = Path(spec.origin).parent
        elif spec.submodule_search_locations:
            package_dir = Path(next(iter(spec.submodule_search_locations)))
        else:
            msg = f"Cannot determine location for {package_root}"
            raise ImportError(msg)

        relative_parts = parts[1:]
        profiles_path = package_dir.joinpath(
            *relative_parts[:-1], f"{relative_parts[-1]}.py"
        )

        if not profiles_path.exists():
            msg = f"Profile module not found: {profiles_path}"
            raise ImportError(msg)

        file_spec = _model_config.importlib.util.spec_from_file_location(
            module_path, profiles_path
        )
        if file_spec is None or file_spec.loader is None:
            msg = f"Could not create module spec for {profiles_path}"
            raise ImportError(msg)

        module = _model_config.importlib.util.module_from_spec(file_spec)
        file_spec.loader.exec_module(module)
        profiles = getattr(module, "_PROFILES", {})
        _provider_profiles_cache[module_path] = profiles
        return profiles


def _profile_module_from_class_path(class_path: str) -> str | None:
    """Derive the profile module path from a `class_path` config value."""
    if ":" not in class_path:
        return None
    module_part, _ = class_path.split(":", 1)
    package_root = module_part.split(".", maxsplit=1)[0]
    if not package_root:
        return None
    return f"{package_root}.data._profiles"


def get_available_models() -> dict[str, list[str]]:
    """Get models explicitly configured by the user in config.toml."""
    from invincat_cli import model_config as _model_config

    global _available_models_cache  # noqa: PLW0603
    if _available_models_cache is not None:
        return _available_models_cache

    available: dict[str, list[str]] = {}
    config = _model_config.ModelConfig.load()

    for provider_name, provider_config in config.providers.items():
        if not config.is_provider_enabled(provider_name):
            logger.debug("Provider '%s' is disabled in config; skipping", provider_name)
            continue

        config_models = list(provider_config.get("models", []))
        if config_models:
            available[provider_name] = config_models

    _available_models_cache = available
    return available


def _build_entry(
    base: dict[str, Any],
    overrides: dict[str, Any],
    cli_override: dict[str, Any] | None,
) -> ModelProfileEntry:
    """Build a profile entry by merging base, overrides, and CLI override."""
    merged = {**base, **overrides}
    overridden_keys = set(overrides)
    if cli_override:
        merged = {**merged, **cli_override}
        overridden_keys |= set(cli_override)
    return ModelProfileEntry(
        profile=merged,
        overridden_keys=frozenset(overridden_keys),
    )


def _load_registry_profiles(
    result: dict[str, ModelProfileEntry],
    *,
    cli_override: dict[str, Any] | None,
) -> set[str]:
    from invincat_cli import model_config as _model_config

    config = _model_config.ModelConfig.load()
    seen_specs: set[str] = set()
    registry_providers: set[str] = set()
    for provider, module_path in _model_config._get_provider_profile_modules():  # noqa: SLF001
        registry_providers.add(provider)
        if not config.is_provider_enabled(provider):
            logger.debug("Provider '%s' is disabled in config; skipping profiles", provider)
            continue
        try:
            profiles = _model_config._load_provider_profiles(module_path)  # noqa: SLF001
        except ImportError:
            logger.debug(
                "Could not import profiles from %s for provider '%s'",
                module_path,
                provider,
            )
            continue
        except Exception:
            logger.warning(
                "Failed to load profiles from %s for provider '%s'",
                module_path,
                provider,
                exc_info=True,
            )
            continue

        for model_name, upstream_profile in profiles.items():
            spec = f"{provider}:{model_name}"
            seen_specs.add(spec)
            overrides = config.get_profile_overrides(provider, model_name=model_name)
            result[spec] = _model_config._build_entry(  # noqa: SLF001
                upstream_profile, overrides, cli_override
            )

    return registry_providers | seen_specs


def _add_config_profiles(
    result: dict[str, ModelProfileEntry],
    registry_and_seen: set[str],
    *,
    cli_override: dict[str, Any] | None,
) -> None:
    from invincat_cli import model_config as _model_config

    config = _model_config.ModelConfig.load()
    registry_providers = {
        item for item in registry_and_seen if ":" not in item
    }
    seen_specs = {item for item in registry_and_seen if ":" in item}

    for provider_name, provider_config in config.providers.items():
        if not config.is_provider_enabled(provider_name):
            logger.debug(
                "Provider '%s' is disabled in config; skipping profiles",
                provider_name,
            )
            continue
        if provider_name not in registry_providers:
            class_path = provider_config.get("class_path", "")
            profile_module = _model_config._profile_module_from_class_path(class_path)  # noqa: SLF001
            if profile_module:
                _add_class_path_profiles(
                    result,
                    seen_specs,
                    provider_name=provider_name,
                    profile_module=profile_module,
                    cli_override=cli_override,
                )

        for model_name in provider_config.get("models", []):
            spec = f"{provider_name}:{model_name}"
            if spec not in seen_specs:
                overrides = config.get_profile_overrides(
                    provider_name, model_name=model_name
                )
                result[spec] = _model_config._build_entry(  # noqa: SLF001
                    {}, overrides, cli_override
                )


def _add_class_path_profiles(
    result: dict[str, ModelProfileEntry],
    seen_specs: set[str],
    *,
    provider_name: str,
    profile_module: str,
    cli_override: dict[str, Any] | None,
) -> None:
    from invincat_cli import model_config as _model_config

    config = _model_config.ModelConfig.load()
    try:
        pkg_profiles = _model_config._load_provider_profiles(profile_module)  # noqa: SLF001
    except ImportError:
        logger.debug(
            "Could not import profiles from %s for class_path provider '%s' "
            "(package may not be installed)",
            profile_module,
            provider_name,
        )
        return
    except Exception:
        logger.warning(
            "Failed to load profiles from %s for class_path provider '%s'",
            profile_module,
            provider_name,
            exc_info=True,
        )
        return

    for model_name, upstream_profile in pkg_profiles.items():
        spec = f"{provider_name}:{model_name}"
        seen_specs.add(spec)
        overrides = config.get_profile_overrides(provider_name, model_name=model_name)
        result[spec] = _model_config._build_entry(  # noqa: SLF001
            upstream_profile, overrides, cli_override
        )


def get_model_profiles(
    *,
    cli_override: dict[str, Any] | None = None,
) -> Mapping[str, ModelProfileEntry]:
    """Load upstream profiles merged with config.toml overrides."""
    global _profiles_cache, _profiles_override_cache  # noqa: PLW0603
    if cli_override is None and _profiles_cache is not None:
        return _profiles_cache
    if cli_override is not None and _profiles_override_cache is not None:
        cached_id, cached_result = _profiles_override_cache
        if cached_id == id(cli_override):
            return cached_result

    result: dict[str, ModelProfileEntry] = {}
    registry_and_seen = _load_registry_profiles(result, cli_override=cli_override)
    _add_config_profiles(result, registry_and_seen, cli_override=cli_override)

    frozen = MappingProxyType(result)
    if cli_override is None:
        _profiles_cache = frozen
    else:
        _profiles_override_cache = (id(cli_override), frozen)
    return frozen


def has_provider_credentials(provider: str) -> bool | None:
    """Check if credentials are available for a provider."""
    from invincat_cli import model_config as _model_config

    config = _model_config.ModelConfig.load()
    provider_config = config.providers.get(provider)
    if provider_config:
        result = config.has_credentials(provider)
        if result is not None:
            return result
        if provider_config.get("class_path"):
            return True

    env_var = PROVIDER_API_KEY_ENV.get(provider)
    if env_var:
        return bool(_model_config.resolve_env_var(env_var))

    logger.debug(
        "No credential information for provider '%s'; deferring auth to provider",
        provider,
    )
    return None


def get_credential_env_var(provider: str) -> str | None:
    """Return the env var name that holds credentials for a provider."""
    from invincat_cli import model_config as _model_config

    config = _model_config.ModelConfig.load()
    config_env = config.get_api_key_env(provider)
    if config_env:
        return config_env
    return PROVIDER_API_KEY_ENV.get(provider)
