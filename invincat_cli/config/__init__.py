"""Configuration, constants, and model creation for the CLI."""

from __future__ import annotations

import json as json  # noqa: F401
import logging
import os as os  # noqa: F401
import threading
from importlib.metadata import (
    PackageNotFoundError as PackageNotFoundError,
)
from importlib.metadata import (
    distribution as distribution,
)
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote as unquote
from urllib.parse import urlparse as urlparse

from invincat_cli import langsmith_links as _langsmith_links
from invincat_cli import shell_security as _shell_security
from invincat_cli.config import model_factory as _model_factory
from invincat_cli.config.settings_model import Settings as Settings
from invincat_cli.core.version import __version__ as __version__
from invincat_cli.presentation import glyphs as _display_glyphs

logger = logging.getLogger(__name__)

ASCII_GLYPHS = _display_glyphs.ASCII_GLYPHS
UNICODE_GLYPHS = _display_glyphs.UNICODE_GLYPHS
CharsetMode = _display_glyphs.CharsetMode
Glyphs = _display_glyphs.Glyphs
detect_charset_mode = _display_glyphs.detect_charset_mode
newline_shortcut = _display_glyphs.newline_shortcut
render_banner = _display_glyphs.render_banner

DANGEROUS_SHELL_PATTERNS = _shell_security.DANGEROUS_SHELL_PATTERNS
PATH_SCOPED_READ_COMMANDS = _shell_security.PATH_SCOPED_READ_COMMANDS
RECOMMENDED_SAFE_SHELL_COMMANDS = _shell_security.RECOMMENDED_SAFE_SHELL_COMMANDS
SHELL_ALLOW_ALL = _shell_security.SHELL_ALLOW_ALL
SHELL_TOOL_NAMES = _shell_security.SHELL_TOOL_NAMES
_ShellAllowAll = _shell_security._ShellAllowAll
_path_arg_stays_within_cwd = _shell_security._path_arg_stays_within_cwd
contains_dangerous_patterns = _shell_security.contains_dangerous_patterns
is_shell_command_allowed = _shell_security.is_shell_command_allowed
parse_shell_allow_list = _shell_security.parse_shell_allow_list

_LANGSMITH_URL_LOOKUP_TIMEOUT_SECONDS = (
    _langsmith_links.LANGSMITH_URL_LOOKUP_TIMEOUT_SECONDS
)
_fetch_langsmith_project_url = _langsmith_links.fetch_project_url
_reset_langsmith_project_url_cache = _langsmith_links.reset_project_url_cache

# ---------------------------------------------------------------------------
# Lazy bootstrap: dotenv loading, LANGSMITH_PROJECT override, and start-path
# detection are deferred until first access of `settings` (via module
# `__getattr__`).  This avoids disk I/O and path traversal during import for
# callers that never touch `settings` (e.g. `deepagents --help`).
# ---------------------------------------------------------------------------

_bootstrap_done = False
"""Whether `_ensure_bootstrap()` has executed."""

_bootstrap_lock = threading.Lock()
"""Guards `_ensure_bootstrap()` against concurrent access from the main
thread and the prewarm worker thread."""

_singleton_lock = threading.Lock()
"""Guards lazy singleton construction in `_get_console` / `_get_settings`."""

_bootstrap_start_path: Path | None = None
"""Working directory captured at bootstrap time for dotenv and project discovery."""

_original_langsmith_project: str | None = None
"""Caller's `LANGSMITH_PROJECT` value before the CLI overrides it for agent traces.

Captured inside `_ensure_bootstrap()` after dotenv loading but before the
`LANGSMITH_PROJECT` override, so `.env`-only values are visible.
"""


from invincat_cli.config.bootstrap import (  # noqa: E402, F401
    _ensure_bootstrap,
    _find_dotenv_from_start_path,
    _load_dotenv,
)

# Global user-level .env (~/.invincat/.env); sentinel when Path.home() fails.
try:
    _GLOBAL_DOTENV_PATH = Path.home() / ".invincat" / ".env"
except RuntimeError:  # pragma: no cover - import-time fallback for broken home dirs
    _GLOBAL_DOTENV_PATH = Path("/nonexistent/.invincat/.env")



if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.runnables import RunnableConfig
    from rich.console import Console

    # Static type stubs for lazy module attributes resolved by __getattr__.
    # At runtime these are created on first access by _get_settings() /
    # _get_console() and cached in globals().
    settings: Settings
    console: Console

MODE_PREFIXES: dict[str, str] = {
    "shell": "!",
    "command": "/",
}
"""Maps each non-normal mode to its trigger character."""

MODE_DISPLAY_GLYPHS: dict[str, str] = {
    "shell": "$",
    "command": "/",
}
"""Maps each non-normal mode to its display glyph shown in the prompt/UI."""

if MODE_PREFIXES.keys() != MODE_DISPLAY_GLYPHS.keys():  # pragma: no cover - invariant
    _only_prefixes = MODE_PREFIXES.keys() - MODE_DISPLAY_GLYPHS.keys()
    _only_glyphs = MODE_DISPLAY_GLYPHS.keys() - MODE_PREFIXES.keys()
    msg = (
        "MODE_PREFIXES and MODE_DISPLAY_GLYPHS have mismatched keys: "
        f"only in PREFIXES={_only_prefixes}, only in GLYPHS={_only_glyphs}"
    )
    raise ValueError(msg)

PREFIX_TO_MODE: dict[str, str] = {v: k for k, v in MODE_PREFIXES.items()}
"""Reverse lookup: trigger character -> mode name."""


_glyphs_cache: Glyphs | None = None
"""Module-level cache for detected glyphs."""

_editable_cache: tuple[bool, str | None] | None = None
"""Module-level cache for editable install info: (is_editable, source_path)."""

from invincat_cli.config.display import (  # noqa: E402, F401
    _detect_charset_mode,
    _get_editable_install_path,
    _is_editable_install,
    _resolve_editable_info,
    get_banner,
    get_glyphs,
    is_ascii_mode,
    reset_glyphs_cache,
)

MAX_ARG_LENGTH = 150
"""Character limit for tool argument values in the UI.

Longer values are truncated with an ellipsis by `truncate_value`
in `tool_display`.
"""

config: RunnableConfig = {
    "recursion_limit": 1000,
}
"""Default LangGraph runnable config.

Sets `recursion_limit` to 1000 to accommodate deeply nested agent graphs without
hitting the default LangGraph ceiling.
"""

_git_branch_cache: dict[str, str | None] = {}
"""Per-cwd cache of resolved git branch names.

Avoids repeated `git rev-parse` subprocess calls within the same session. Keyed
by `str(Path.cwd())`; `None` values indicate the directory is not inside a git
repository.
"""


from invincat_cli.config.langsmith import (  # noqa: E402, F401
    build_langsmith_thread_url,
    fetch_langsmith_project_url,
    get_langsmith_project_name,
    reset_langsmith_url_cache,
)
from invincat_cli.config.runtime import (  # noqa: E402, F401
    _get_git_branch,
    _parse_extra_skills_dirs,
    _read_config_toml_skills_dirs,
    build_stream_config,
)
from invincat_cli.config.session import SessionState as SessionState  # noqa: E402, F401


def detect_provider(model_name: str) -> str | None:
    """Auto-detect provider from model name."""
    return _model_factory.detect_provider(model_name, settings=_get_settings())


def _get_default_model_spec() -> str:
    """Get default model specification from configured models."""
    return _model_factory.get_default_model_spec()


def _get_default_memory_model_spec() -> str | None:
    """Get the dedicated default model specification for memory agent."""
    return _model_factory.get_default_memory_model_spec()


_OPENROUTER_APP_URL = _model_factory._OPENROUTER_APP_URL
_OPENROUTER_APP_TITLE = _model_factory._OPENROUTER_APP_TITLE
_OPENROUTER_APP_CATEGORIES = _model_factory._OPENROUTER_APP_CATEGORIES
ModelResult = _model_factory.ModelResult


def _apply_openrouter_defaults(kwargs: dict[str, Any]) -> None:
    """Inject default OpenRouter attribution kwargs."""
    _model_factory.apply_openrouter_defaults(kwargs)


def _get_provider_kwargs(
    provider: str, *, model_name: str | None = None
) -> dict[str, Any]:
    """Get provider-specific kwargs from the config file."""
    return _model_factory.get_provider_kwargs(provider, model_name=model_name)


def _create_model_from_class(
    class_path: str,
    model_name: str,
    provider: str,
    kwargs: dict[str, Any],
) -> BaseChatModel:
    """Import and instantiate a custom `BaseChatModel` class."""
    return _model_factory.create_model_from_class(
        class_path,
        model_name,
        provider,
        kwargs,
    )


def _create_model_via_init(
    model_name: str,
    provider: str,
    kwargs: dict[str, Any],
) -> BaseChatModel:
    """Create a model using LangChain's `init_chat_model`."""
    return _model_factory.create_model_via_init(model_name, provider, kwargs)


def _is_deepseek_openai_compatible_path(provider: str, kwargs: dict[str, Any]) -> bool:
    """Return True when OpenAI provider is pointed at DeepSeek's compatible API."""
    return _model_factory.is_deepseek_openai_compatible_path(provider, kwargs)


def _apply_deepseek_thinking_defaults(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Explicitly enable DeepSeek thinking mode unless caller already set it."""
    return _model_factory.apply_deepseek_thinking_defaults(kwargs)


def _sanitize_deepseek_thinking_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Remove DeepSeek-incompatible reasoning params when thinking is disabled."""
    return _model_factory.sanitize_deepseek_thinking_params(kwargs)


def _apply_profile_overrides(
    model: BaseChatModel,
    overrides: dict[str, Any],
    model_name: str,
    *,
    label: str,
    raise_on_failure: bool = False,
) -> None:
    """Merge `overrides` into `model.profile`."""
    _model_factory.apply_profile_overrides(
        model,
        overrides,
        model_name,
        label=label,
        raise_on_failure=raise_on_failure,
    )


class _ConfigModelFactoryHooks:
    """Adapter that lets model factory call the current config module globals."""

    def get_default_model_spec(self) -> str:
        return _get_default_model_spec()

    def detect_provider(self, model_name: str) -> str | None:
        return detect_provider(model_name)

    def get_provider_kwargs(
        self, provider: str, *, model_name: str | None = None
    ) -> dict[str, Any]:
        return _get_provider_kwargs(provider, model_name=model_name)

    def create_model_from_class(
        self,
        class_path: str,
        model_name: str,
        provider: str,
        kwargs: dict[str, Any],
    ) -> BaseChatModel:
        return _create_model_from_class(class_path, model_name, provider, kwargs)

    def create_model_via_init(
        self,
        model_name: str,
        provider: str,
        kwargs: dict[str, Any],
    ) -> BaseChatModel:
        return _create_model_via_init(model_name, provider, kwargs)

    def apply_profile_overrides(
        self,
        model: BaseChatModel,
        overrides: dict[str, Any],
        model_name: str,
        *,
        label: str,
        raise_on_failure: bool = False,
    ) -> None:
        _apply_profile_overrides(
            model,
            overrides,
            model_name,
            label=label,
            raise_on_failure=raise_on_failure,
        )


def create_model(
    model_spec: str | None = None,
    *,
    extra_kwargs: dict[str, Any] | None = None,
    profile_overrides: dict[str, Any] | None = None,
    enable_thinking_default: bool = True,
) -> ModelResult:
    """Create a chat model."""
    return _model_factory.create_model(
        model_spec,
        extra_kwargs=extra_kwargs,
        profile_overrides=profile_overrides,
        enable_thinking_default=enable_thinking_default,
        hooks=_ConfigModelFactoryHooks(),
    )


def validate_model_capabilities(model: BaseChatModel, model_name: str) -> None:
    """Validate that the model has required capabilities for `deepagents`."""
    _model_factory.validate_model_capabilities(
        model,
        model_name,
        console=_get_console(),
    )



def _get_console() -> Console:
    """Return the lazily-initialized global `Console` instance.

    Defers the `rich.console` import until console output is actually
    needed. The result is cached in `globals()["console"]`.

    Returns:
        The global Rich `Console` singleton.
    """
    cached = globals().get("console")
    if cached is not None:
        return cached
    with _singleton_lock:
        cached = globals().get("console")
        if cached is not None:
            return cached
        from rich.console import Console

        inst = Console(highlight=False)
        globals()["console"] = inst
        return inst


def _get_settings() -> Settings:
    """Return the lazily-initialized global `Settings` instance.

    Ensures bootstrap has run before constructing settings. The result is cached
    in `globals()["settings"]` so subsequent access — including
    `from config import settings` in other modules — resolves instantly.

    Returns:
        The global `Settings` singleton.
    """
    cached = globals().get("settings")
    if cached is not None:
        return cached
    with _singleton_lock:
        cached = globals().get("settings")
        if cached is not None:
            return cached
        _ensure_bootstrap()
        try:
            inst = Settings.from_environment(start_path=_bootstrap_start_path)
        except Exception:
            logger.exception(
                "Failed to initialize settings from environment (start_path=%s)",
                _bootstrap_start_path,
            )
            raise
        globals()["settings"] = inst
        return inst


def reset_settings_cache() -> None:
    """Reset the global settings cache.

    Intended for use after model registration to ensure new models are available.
    """
    with _singleton_lock:
        if "settings" in globals():
            del globals()["settings"]


def __getattr__(name: str) -> Settings | Console:
    """Lazy module attributes for `settings` and `console`.

    Defers heavy initialization until first access. Subsequent accesses hit
    the module-level attribute directly (no `__getattr__` overhead).

    Returns:
        The requested lazy singleton.

    Raises:
        AttributeError: If *name* is not a lazily-provided attribute.
    """
    if name == "settings":
        return _get_settings()
    if name == "console":
        return _get_console()
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
