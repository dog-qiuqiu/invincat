"""Sandbox lifecycle management with provider abstraction."""

from __future__ import annotations

import importlib
import importlib.util
import time as time  # noqa: F401  # Re-exported for tests that patch polling sleeps.
from typing import TYPE_CHECKING

from invincat_cli.integrations.sandbox_agentcore import _AgentCoreProvider
from invincat_cli.integrations.sandbox_cloud_providers import (
    _DaytonaProvider,
    _ModalProvider,
    _RunloopProvider,
)
from invincat_cli.integrations.sandbox_factory_lifecycle import (
    _PROVIDER_TO_WORKING_DIR as _PROVIDER_TO_WORKING_DIR,
)
from invincat_cli.integrations.sandbox_factory_lifecycle import (
    _get_available_sandbox_types as _get_available_sandbox_types,
)
from invincat_cli.integrations.sandbox_factory_lifecycle import (
    _run_sandbox_setup as _run_sandbox_setup,
)
from invincat_cli.integrations.sandbox_factory_lifecycle import (
    create_sandbox,
    get_default_working_dir,
    verify_sandbox_deps,
)
from invincat_cli.integrations.sandbox_langsmith import (
    _LANGSMITH_DEFAULT_IMAGE as _LANGSMITH_DEFAULT_IMAGE,
)
from invincat_cli.integrations.sandbox_langsmith import (
    _LANGSMITH_DEFAULT_TEMPLATE as _LANGSMITH_DEFAULT_TEMPLATE,
)
from invincat_cli.integrations.sandbox_langsmith import (
    _LangSmithProvider,
)
from invincat_cli.integrations.sandbox_provider import (
    SandboxNotFoundError,
    SandboxProvider,
)

if TYPE_CHECKING:
    from types import ModuleType


def _import_provider_module(
    module_name: str,
    *,
    provider: str,
    package: str,
) -> ModuleType:
    """Import an optional provider module with a provider-specific error message."""
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        msg = (
            f"The '{provider}' sandbox provider requires the '{package}' package. "
            f"Install it with: pip install 'invincat-cli[{provider}]'"
        )
        raise ImportError(msg) from exc


def _get_provider(provider_name: str) -> SandboxProvider:
    """Get a `SandboxProvider` instance for the specified provider."""
    if provider_name == "agentcore":
        return _AgentCoreProvider()
    if provider_name == "daytona":
        return _DaytonaProvider()
    if provider_name == "langsmith":
        return _LangSmithProvider()
    if provider_name == "modal":
        return _ModalProvider()
    if provider_name == "runloop":
        return _RunloopProvider()
    msg = (
        f"Unknown sandbox provider: {provider_name}. "
        f"Available providers: {', '.join(_get_available_sandbox_types())}"
    )
    raise ValueError(msg)


__all__ = [
    "SandboxNotFoundError",
    "create_sandbox",
    "get_default_working_dir",
    "verify_sandbox_deps",
]
