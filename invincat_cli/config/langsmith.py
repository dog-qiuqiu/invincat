"""LangSmith project and thread URL helpers for config."""

from __future__ import annotations


def get_langsmith_project_name() -> str | None:
    """Resolve the LangSmith project name if tracing is configured."""
    from invincat_cli import config as _config
    from invincat_cli.model_config import resolve_env_var

    langsmith_key = resolve_env_var("LANGSMITH_API_KEY") or resolve_env_var(
        "LANGCHAIN_API_KEY"
    )
    langsmith_tracing = resolve_env_var("LANGSMITH_TRACING") or resolve_env_var(
        "LANGCHAIN_TRACING_V2"
    )
    if not (langsmith_key and langsmith_tracing):
        return None

    return (
        _config._get_settings().deepagents_langchain_project  # noqa: SLF001
        or _config.os.environ.get("LANGSMITH_PROJECT")
        or "invincat-cli"
    )


def fetch_langsmith_project_url(project_name: str) -> str | None:
    """Fetch the LangSmith project URL via the LangSmith client."""
    from invincat_cli import config as _config

    return _config._fetch_langsmith_project_url(  # noqa: SLF001
        project_name,
        threading_module=_config.threading,
        timeout_seconds=_config._LANGSMITH_URL_LOOKUP_TIMEOUT_SECONDS,  # noqa: SLF001
    )


def build_langsmith_thread_url(thread_id: str) -> str | None:
    """Build a full LangSmith thread URL if tracing is configured."""
    from invincat_cli import config as _config

    project_name = _config.get_langsmith_project_name()
    if not project_name:
        return None

    project_url = _config.fetch_langsmith_project_url(project_name)
    if not project_url:
        return None

    return f"{project_url.rstrip('/')}/t/{thread_id}?utm_source=invincat-cli"


def reset_langsmith_url_cache() -> None:
    """Reset the LangSmith URL cache."""
    from invincat_cli import config as _config

    _config._reset_langsmith_project_url_cache()  # noqa: SLF001
