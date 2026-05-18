"""Version information and lightweight constants for `invincat-cli`."""

__version__ = "0.0.34"  # x-release-please-version

CLI_PACKAGE_NAME = "invincat-cli"
"""PyPI distribution name for this CLI."""

CLI_COMMAND = "invincat-cli"
"""Console script command exposed by the package."""

DOCS_URL = "https://github.com/dog-qiuqiu/invincat#readme"
"""URL for `invincat-cli` documentation."""

PYPI_URL = "https://pypi.org/pypi/invincat-cli/json"
"""PyPI JSON API endpoint for version checks."""

CHANGELOG_URL = "https://github.com/dog-qiuqiu/invincat/releases"
"""URL for the full changelog."""

USER_AGENT = f"invincat-cli/{__version__} update-check"
"""User-Agent header sent with PyPI requests."""
