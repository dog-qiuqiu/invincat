"""LangSmith project URL lookup helpers."""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

LANGSMITH_URL_LOOKUP_TIMEOUT_SECONDS = 2.0
"""Max seconds to wait for LangSmith project URL lookup."""

_langsmith_url_cache: tuple[str, str] | None = None


def fetch_project_url(
    project_name: str,
    *,
    threading_module: Any = threading,  # noqa: ANN401  # Test hook for fake threading
    timeout_seconds: float = LANGSMITH_URL_LOOKUP_TIMEOUT_SECONDS,
) -> str | None:
    """Fetch and cache a LangSmith project URL using a bounded background thread."""
    global _langsmith_url_cache  # noqa: PLW0603

    if _langsmith_url_cache is not None:
        cached_name, cached_url = _langsmith_url_cache
        if cached_name == project_name:
            return cached_url

    try:
        from langsmith import Client
    except ImportError:
        logger.debug(
            "Could not fetch LangSmith project URL for '%s'",
            project_name,
            exc_info=True,
        )
        return None

    result: str | None = None
    lookup_error: Exception | None = None
    done = threading_module.Event()

    def _lookup_url() -> None:
        nonlocal result, lookup_error
        try:
            from invincat_cli.model_config import resolve_env_var

            api_key = resolve_env_var("LANGSMITH_API_KEY") or resolve_env_var(
                "LANGCHAIN_API_KEY"
            )
            project = Client(api_key=api_key).read_project(project_name=project_name)
            result = project.url or None
        except Exception as exc:  # noqa: BLE001  # LangSmith SDK error types are not stable
            lookup_error = exc
        finally:
            done.set()

    thread = threading_module.Thread(target=_lookup_url, daemon=True)
    thread.start()

    if not done.wait(timeout_seconds):
        logger.debug(
            "Timed out fetching LangSmith project URL for '%s' after %.1fs",
            project_name,
            timeout_seconds,
        )
        return None

    if lookup_error is not None:
        logger.debug(
            "Could not fetch LangSmith project URL for '%s'",
            project_name,
            exc_info=(
                type(lookup_error),
                lookup_error,
                lookup_error.__traceback__,
            ),
        )
        return None

    if result is not None:
        _langsmith_url_cache = (project_name, result)
    return result


def reset_project_url_cache() -> None:
    """Reset the cached LangSmith project URL."""
    global _langsmith_url_cache  # noqa: PLW0603
    _langsmith_url_cache = None
