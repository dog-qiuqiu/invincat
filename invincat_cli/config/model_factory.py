"""Model provider detection and chat-model construction helpers."""

from __future__ import annotations

import copy
import importlib
import logging
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


def detect_provider(model_name: str, *, settings: Any) -> str | None:  # noqa: ANN401
    model_lower = model_name.lower()

    if model_lower.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")):
        return "openai"

    if model_lower.startswith("claude"):
        if not settings.has_anthropic and settings.has_vertex_ai:
            return "google_vertexai"
        return "anthropic"

    if model_lower.startswith("gemini"):
        if settings.has_vertex_ai and not settings.has_google:
            return "google_vertexai"
        return "google_genai"

    if model_lower.startswith(("nemotron", "nvidia/")):
        return "nvidia"

    return None


def get_default_model_spec() -> str:
    from invincat_cli.model_config import ModelConfig, ModelConfigError, ModelSpec

    config = ModelConfig.load()
    registered_specs: list[str] = [
        f"{provider_name}:{model_name}"
        for provider_name, provider_config in config.providers.items()
        if config.is_provider_enabled(provider_name)
        for model_name in provider_config.get("models", [])
    ]
    registered_set = set(registered_specs)

    if config.default_model and config.default_model in registered_set:
        return config.default_model

    if config.recent_model and config.recent_model in registered_set:
        return config.recent_model

    for preferred in (config.default_model, config.recent_model):
        if not preferred or ":" in preferred:
            continue
        matches = [
            spec
            for spec in registered_specs
            if (parsed := ModelSpec.try_parse(spec)) and parsed.model == preferred
        ]
        if len(matches) == 1:
            return matches[0]

    if registered_specs:
        return registered_specs[0]

    msg = "No model configured. Run /model, press Ctrl+N, and register a model first."
    raise ModelConfigError(msg)


def get_default_memory_model_spec() -> str | None:
    from invincat_cli.model_config import ModelConfig

    config = ModelConfig.load()
    if config.memory_default_model:
        return config.memory_default_model
    return None


_OPENROUTER_APP_URL = "https://pypi.org/project/invincat-cli/"
_OPENROUTER_APP_TITLE = "Invincat CLI"
_OPENROUTER_APP_CATEGORIES: list[str] = ["cli-agent"]


def apply_openrouter_defaults(kwargs: dict[str, Any]) -> None:
    kwargs.setdefault("app_url", _OPENROUTER_APP_URL)
    kwargs.setdefault("app_title", _OPENROUTER_APP_TITLE)
    kwargs.setdefault("app_categories", _OPENROUTER_APP_CATEGORIES)


def get_provider_kwargs(provider: str, *, model_name: str | None = None) -> dict[str, Any]:
    from invincat_cli.model_config import (
        PROVIDER_API_KEY_ENV,
        ModelConfig,
        resolve_env_var,
    )

    config = ModelConfig.load()
    result: dict[str, Any] = config.get_kwargs(provider, model_name=model_name)

    if "base_url" not in result:
        base_url = config.get_base_url(provider)
        if base_url:
            result["base_url"] = base_url

    if "api_key" not in result:
        api_key_env = result.pop("api_key_env", None)
        if not api_key_env:
            api_key_env = config.get_api_key_env(provider)
        if not api_key_env:
            api_key_env = PROVIDER_API_KEY_ENV.get(provider)
            if api_key_env:
                logger.debug(
                    "No api_key_env in config.toml for '%s';"
                    " using hardcoded provider env var",
                    provider,
                )
        if api_key_env:
            api_key = resolve_env_var(api_key_env)
            if api_key:
                result["api_key"] = api_key

    if provider == "openrouter":
        try:
            from deepagents._models import check_openrouter_version  # noqa: PLC2701
        except ImportError:
            logger.debug(
                "deepagents._models.check_openrouter_version is unavailable; "
                "skipping optional OpenRouter version check",
                exc_info=True,
            )
        else:
            check_openrouter_version()
        apply_openrouter_defaults(result)

    return result


def create_model_from_class(
    class_path: str,
    model_name: str,
    provider: str,
    kwargs: dict[str, Any],
) -> BaseChatModel:
    from langchain_core.language_models import BaseChatModel as _BaseChatModel

    from invincat_cli.model_config import ModelConfigError

    if ":" not in class_path:
        msg = (
            f"Invalid class_path '{class_path}' for provider '{provider}': "
            "must be in module.path:ClassName format"
        )
        raise ModelConfigError(msg)

    module_path, class_name = class_path.rsplit(":", 1)

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        msg = f"Could not import module '{module_path}' for provider '{provider}': {e}"
        raise ModelConfigError(msg) from e

    cls = getattr(module, class_name, None)
    if cls is None:
        msg = (
            f"Class '{class_name}' not found in module '{module_path}' "
            f"for provider '{provider}'"
        )
        raise ModelConfigError(msg)

    if not (isinstance(cls, type) and issubclass(cls, _BaseChatModel)):
        msg = (
            f"'{class_path}' is not a BaseChatModel subclass (got {type(cls).__name__})"
        )
        raise ModelConfigError(msg)

    try:
        return cls(model=model_name, **kwargs)
    except Exception as e:
        msg = f"Failed to instantiate '{class_path}' for '{provider}:{model_name}': {e}"
        raise ModelConfigError(msg) from e


def create_model_via_init(
    model_name: str,
    provider: str,
    kwargs: dict[str, Any],
) -> BaseChatModel:
    from langchain.chat_models import init_chat_model

    from invincat_cli.model_config import ModelConfigError

    try:
        if provider:
            return init_chat_model(model_name, model_provider=provider, **kwargs)
        return init_chat_model(model_name, **kwargs)
    except ImportError as e:
        import importlib.util

        package_map = {
            "anthropic": "langchain-anthropic",
            "openai": "langchain-openai",
            "google_genai": "langchain-google-genai",
            "google_vertexai": "langchain-google-vertexai",
            "nvidia": "langchain-nvidia-ai-endpoints",
        }
        package = package_map.get(provider, f"langchain-{provider}")
        module_name = package.replace("-", "_")
        try:
            spec_found = importlib.util.find_spec(module_name) is not None
        except (ImportError, ValueError):
            spec_found = False
        if spec_found:
            msg = (
                f"Provider package '{package}' is installed but failed to "
                f"import for provider '{provider}': {e}"
            )
        else:
            msg = (
                f"Missing package for provider '{provider}'. "
                f"Install: pip install {package}"
            )
        raise ModelConfigError(msg) from e
    except (ValueError, TypeError) as e:
        spec = f"{provider}:{model_name}" if provider else model_name
        msg = f"Invalid model configuration for '{spec}': {e}"
        raise ModelConfigError(msg) from e
    except Exception as e:
        spec = f"{provider}:{model_name}" if provider else model_name
        msg = f"Failed to initialize model '{spec}': {e}"
        raise ModelConfigError(msg) from e


def is_deepseek_openai_compatible_path(provider: str, kwargs: dict[str, Any]) -> bool:
    if provider != "openai":
        return False
    base_url = kwargs.get("base_url")
    if not isinstance(base_url, str):
        return False
    normalized = base_url.lower().rstrip("/")
    return "api.deepseek.com" in normalized


def apply_deepseek_thinking_defaults(kwargs: dict[str, Any]) -> dict[str, Any]:
    patched = dict(kwargs)
    extra_body = dict(patched.get("extra_body") or {})
    thinking = dict(extra_body.get("thinking") or {})
    thinking.setdefault("type", "enabled")
    extra_body["thinking"] = thinking
    patched["extra_body"] = extra_body
    patched.setdefault("reasoning_effort", "high")
    return patched


def sanitize_deepseek_thinking_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    patched = copy.deepcopy(kwargs)
    extra_body = patched.get("extra_body")
    if not isinstance(extra_body, dict):
        return patched
    thinking = extra_body.get("thinking")
    if not isinstance(thinking, dict):
        return patched
    if thinking.get("type") == "disabled":
        patched.pop("reasoning_effort", None)
    return patched


@dataclass(frozen=True)
class ModelResult:
    """Result of creating a chat model, bundling model and metadata."""

    model: BaseChatModel
    model_name: str
    provider: str
    context_limit: int | None = None
    unsupported_modalities: frozenset[str] = frozenset()

    def apply_to_settings(self) -> None:
        """Commit this result's metadata to global `settings`."""
        from invincat_cli import config as config_mod

        s = config_mod._get_settings()
        s.model_name = self.model_name
        s.model_provider = self.provider
        s.model_context_limit = self.context_limit
        s.model_unsupported_modalities = self.unsupported_modalities


def apply_profile_overrides(
    model: BaseChatModel,
    overrides: dict[str, Any],
    model_name: str,
    *,
    label: str,
    raise_on_failure: bool = False,
) -> None:
    from invincat_cli.model_config import ModelConfigError

    logger.debug("Applying %s profile overrides: %s", label, overrides)
    profile = getattr(model, "profile", None)
    copied_overrides = copy.deepcopy(overrides)
    merged = (
        {**copy.deepcopy(profile), **copied_overrides}
        if isinstance(profile, dict)
        else copied_overrides
    )
    try:
        model.profile = merged  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError) as exc:
        if raise_on_failure:
            msg = (
                f"Could not apply {label} to model '{model_name}': {exc}. "
                f"The model may not support profile assignment."
            )
            raise ModelConfigError(msg) from exc
        logger.warning(
            "Could not apply %s profile overrides to model '%s': %s. "
            "Overrides will be ignored.",
            label,
            model_name,
            exc,
        )


def create_model(
    model_spec: str | None,
    *,
    extra_kwargs: dict[str, Any] | None,
    profile_overrides: dict[str, Any] | None,
    enable_thinking_default: bool,
    hooks: Any,  # noqa: ANN401
) -> ModelResult:
    from invincat_cli.model_config import ModelConfig, ModelConfigError, ModelSpec

    if not model_spec:
        model_spec = hooks.get_default_model_spec()

    provider: str
    model_name: str
    parsed = ModelSpec.try_parse(model_spec)
    if parsed:
        provider, model_name = parsed.provider, parsed.model
    elif ":" in model_spec:
        _, _, after = model_spec.partition(":")
        if after:
            model_name = after
            provider = hooks.detect_provider(model_name) or ""
        else:
            msg = (
                f"Invalid model spec '{model_spec}': model name is required "
                "(e.g., 'anthropic:claude-sonnet-4-5' or 'claude-sonnet-4-5')"
            )
            raise ModelConfigError(msg)
    else:
        model_name = model_spec
        provider = hooks.detect_provider(model_spec) or ""

    kwargs = hooks.get_provider_kwargs(provider, model_name=model_name)

    if extra_kwargs:
        kwargs.update(copy.deepcopy(extra_kwargs))

    config = ModelConfig.load()
    class_path = config.get_class_path(provider) if provider else None

    if class_path:
        model = hooks.create_model_from_class(class_path, model_name, provider, kwargs)
    elif is_deepseek_openai_compatible_path(provider, kwargs):
        from invincat_cli.models.deepseek_chat_openai import DeepSeekChatOpenAICompat

        model_kwargs = (
            apply_deepseek_thinking_defaults(kwargs)
            if enable_thinking_default
            else kwargs
        )
        model_kwargs = sanitize_deepseek_thinking_params(model_kwargs)
        model = DeepSeekChatOpenAICompat(model=model_name, **model_kwargs)
    else:
        model = hooks.create_model_via_init(model_name, provider, kwargs)

    resolved_provider = provider or getattr(model, "_model_provider", provider)

    if provider:
        config_profile_overrides = config.get_profile_overrides(
            provider, model_name=model_name
        )
        if config_profile_overrides:
            hooks.apply_profile_overrides(
                model,
                config_profile_overrides,
                model_name,
                label=f"config.toml (provider '{provider}')",
            )

    if profile_overrides:
        hooks.apply_profile_overrides(
            model,
            profile_overrides,
            model_name,
            label="CLI --profile-override",
            raise_on_failure=True,
        )

    context_limit: int | None = None
    unsupported_modalities: frozenset[str] = frozenset()
    profile = getattr(model, "profile", None)
    if isinstance(profile, dict):
        if isinstance(profile.get("max_input_tokens"), int):
            context_limit = profile["max_input_tokens"]

        modality_keys = {
            "image_inputs": "image",
            "audio_inputs": "audio",
            "video_inputs": "video",
            "pdf_inputs": "pdf",
        }
        unsupported_modalities = frozenset(
            label for key, label in modality_keys.items() if profile.get(key) is False
        )

    return ModelResult(
        model=model,
        model_name=model_name,
        provider=resolved_provider,
        context_limit=context_limit,
        unsupported_modalities=unsupported_modalities,
    )


def validate_model_capabilities(
    model: BaseChatModel,
    model_name: str,
    *,
    console: Any,
) -> None:
    profile = getattr(model, "profile", None)

    if profile is None:
        console.print(
            f"[dim][yellow]Note:[/yellow] No capability profile for "
            f"'{model_name}'. Cannot verify tool calling support.[/dim]"
        )
        return

    if not isinstance(profile, dict):
        return

    tool_calling = profile.get("tool_calling")
    if tool_calling is False:
        console.print(
            f"[bold red]Error:[/bold red] Model '{model_name}' "
            "does not support tool calling."
        )
        console.print(
            "\nDeep Agents requires tool calling for agent functionality. "
            "Please choose a model that supports tool calling."
        )
        console.print("\nSee MODELS.md for supported models.")
        sys.exit(1)

    max_input_tokens = profile.get("max_input_tokens")
    if max_input_tokens and max_input_tokens < 8000:  # noqa: PLR2004
        console.print(
            f"[dim][yellow]Warning:[/yellow] Model '{model_name}' has limited context "
            f"({max_input_tokens:,} tokens). Agent performance may be affected.[/dim]"
        )
