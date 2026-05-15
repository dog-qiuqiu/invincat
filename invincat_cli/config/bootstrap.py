"""Lazy environment bootstrap helpers for config."""

from __future__ import annotations

from pathlib import Path


def _find_dotenv_from_start_path(start_path: Path) -> Path | None:
    """Find the nearest `.env` file from an explicit start path upward."""
    from invincat_cli import config as _config

    current = start_path.expanduser().resolve()
    for parent in [current, *list(current.parents)]:
        candidate = parent / ".env"
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            _config.logger.warning("Could not inspect .env candidate %s", candidate)
            continue
    return None


def _load_dotenv(*, start_path: Path | None = None) -> bool:
    """Load environment variables from project and global `.env` files."""
    import dotenv

    from invincat_cli import config as _config

    loaded = False
    dotenv_path: Path | str | None = None
    try:
        if start_path is None:
            loaded = dotenv.load_dotenv(override=False) or loaded
        else:
            dotenv_path = _config._find_dotenv_from_start_path(start_path)  # noqa: SLF001
            if dotenv_path is not None:
                loaded = (
                    dotenv.load_dotenv(dotenv_path=dotenv_path, override=False)
                    or loaded
                )
    except (OSError, ValueError):
        _config.logger.warning(
            "Could not read project dotenv at %s; project env vars will not be loaded",
            dotenv_path or start_path or "cwd",
            exc_info=True,
        )

    try:
        if _config._GLOBAL_DOTENV_PATH.is_file() and dotenv.load_dotenv(  # noqa: SLF001
            dotenv_path=_config._GLOBAL_DOTENV_PATH,  # noqa: SLF001
            override=False,
        ):
            loaded = True
            _config.logger.debug(
                "Loaded global dotenv: %s", _config._GLOBAL_DOTENV_PATH  # noqa: SLF001
            )
    except (OSError, ValueError):
        _config.logger.warning(
            "Could not read global dotenv at %s; global defaults will not be applied",
            _config._GLOBAL_DOTENV_PATH,  # noqa: SLF001
            exc_info=True,
        )

    return loaded


def _ensure_bootstrap() -> None:
    """Run one-time bootstrap: dotenv loading and LangSmith env overrides."""
    from invincat_cli import config as _config

    if _config._bootstrap_done:  # noqa: SLF001
        return

    with _config._bootstrap_lock:  # noqa: SLF001
        if _config._bootstrap_done:  # noqa: SLF001
            return

        try:
            from invincat_cli.project_utils import (
                get_server_project_context as _get_server_project_context,
            )

            ctx = _get_server_project_context()
            _config._bootstrap_start_path = ctx.user_cwd if ctx else None  # noqa: SLF001
            _config._load_dotenv(start_path=_config._bootstrap_start_path)  # noqa: SLF001
            _config._original_langsmith_project = _config.os.environ.get(  # noqa: SLF001
                "LANGSMITH_PROJECT"
            )

            from invincat_cli.core.env_vars import LANGSMITH_PROJECT

            deepagents_project = _config.os.environ.get(LANGSMITH_PROJECT)
            if deepagents_project:
                _config.os.environ["LANGSMITH_PROJECT"] = deepagents_project

            from invincat_cli.model_config import _ENV_PREFIX

            for canonical in (
                "LANGSMITH_API_KEY",
                "LANGCHAIN_API_KEY",
                "LANGSMITH_TRACING",
                "LANGCHAIN_TRACING_V2",
            ):
                prefixed = f"{_ENV_PREFIX}{canonical}"
                if prefixed not in _config.os.environ:
                    continue
                prefixed_val = _config.os.environ[prefixed]
                if canonical not in _config.os.environ:
                    _config.os.environ[canonical] = prefixed_val
                elif _config.os.environ[canonical] != prefixed_val:
                    _config.logger.warning(
                        "Both %s and %s are set with different values; "
                        "the LangSmith SDK will use %s while the CLI "
                        "prefers %s. Unset one to avoid confusion.",
                        canonical,
                        prefixed,
                        canonical,
                        prefixed,
                    )
        except Exception:
            _config.logger.exception(
                "Bootstrap failed; .env values and LANGSMITH_PROJECT override "
                "may be missing. The CLI will proceed with environment as-is.",
            )
        finally:
            _config._bootstrap_done = True  # noqa: SLF001
