"""Version message helpers for the Textual app."""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError

from invincat_cli.i18n import t

logger = logging.getLogger(__name__)


def build_version_message(
    *,
    cli_version: str | None,
    sdk_version: str | None,
) -> str:
    """Build the `/version` command response from resolved versions."""
    cli_line = (
        t("version.cli_line").format(version=cli_version)
        if cli_version
        else t("version.cli_unknown")
    )
    sdk_line = (
        t("version.sdk_line").format(version=sdk_version)
        if sdk_version
        else t("version.sdk_unknown")
    )
    return f"{cli_line}\n{sdk_line}"


def resolve_version_message(
    *,
    cli_version: str | None = None,
    package_version: Callable[[str], str] | None = None,
) -> str:
    """Resolve installed CLI/SDK versions and build the display message."""
    if cli_version is None:
        try:
            from invincat_cli.core.version import __version__

            cli_version = __version__
        except ImportError:
            logger.debug("deepagents_cli._version module not found")
        except Exception:
            logger.warning("Unexpected error looking up CLI version", exc_info=True)

    version_lookup = package_version
    if version_lookup is None:
        from importlib.metadata import version

        version_lookup = version

    sdk_version: str | None = None
    try:
        sdk_version = version_lookup("deepagents")
    except PackageNotFoundError:
        logger.debug("deepagents SDK package not found in environment")
    except Exception:
        logger.warning("Unexpected error looking up SDK version", exc_info=True)

    return build_version_message(cli_version=cli_version, sdk_version=sdk_version)
