"""Shared model configuration types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict

ModelTarget = Literal["primary", "memory"]


class ModelConfigError(Exception):
    """Raised when model configuration or creation fails."""


@dataclass(frozen=True)
class ModelSpec:
    """A model specification in `provider:model` format."""

    provider: str
    """The provider name (e.g., `'anthropic'`, `'openai'`)."""

    model: str
    """The model identifier (e.g., `'claude-sonnet-4-5'`, `'gpt-4o'`)."""

    def __post_init__(self) -> None:
        """Validate the model spec after initialization."""
        if not self.provider:
            msg = "Provider cannot be empty"
            raise ValueError(msg)
        if not self.model:
            msg = "Model cannot be empty"
            raise ValueError(msg)

    @classmethod
    def parse(cls, spec: str) -> ModelSpec:
        """Parse a model specification string."""
        if ":" not in spec:
            msg = (
                f"Invalid model spec '{spec}': must be in provider:model format "
                "(e.g., 'anthropic:claude-sonnet-4-5')"
            )
            raise ValueError(msg)
        provider, model = spec.split(":", 1)
        return cls(provider=provider, model=model)

    @classmethod
    def try_parse(cls, spec: str) -> ModelSpec | None:
        """Non-raising variant of `parse`."""
        try:
            return cls.parse(spec)
        except ValueError:
            return None

    def __str__(self) -> str:
        """Return the model spec as a string in `provider:model` format."""
        return f"{self.provider}:{self.model}"


class ModelProfileEntry(TypedDict):
    """Profile data for a model with override tracking."""

    profile: dict[str, Any]
    """Merged profile dict (upstream defaults + config.toml overrides)."""

    overridden_keys: frozenset[str]
    """Keys in `profile` whose values came from config.toml overrides."""


class ProviderConfig(TypedDict, total=False):
    """Configuration for a model provider."""

    enabled: bool
    """Whether this provider appears in the model switcher."""

    models: list[str]
    """List of model identifiers available from this provider."""

    api_key_env: str
    """Environment variable name containing the API key."""

    base_url: str
    """Custom base URL."""

    class_path: str
    """Fully-qualified Python class in `module.path:ClassName` format."""

    params: dict[str, Any]
    """Extra keyword arguments forwarded to the model constructor."""

    profile: dict[str, Any]
    """Overrides merged into the model's runtime profile dict."""
