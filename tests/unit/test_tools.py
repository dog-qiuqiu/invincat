"""Unit tests for built-in agent tools."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from invincat_cli.tools import _validate_fetch_url


def test_fetch_url_rejects_non_http_urls() -> None:
    with pytest.raises(ValueError, match="http"):
        _validate_fetch_url("file:///etc/passwd")


def test_fetch_url_rejects_localhost() -> None:
    with pytest.raises(ValueError, match="localhost"):
        _validate_fetch_url("http://localhost:8000")


def test_fetch_url_rejects_private_ip_literals() -> None:
    with pytest.raises(ValueError, match="private"):
        _validate_fetch_url("http://169.254.169.254/latest/meta-data")


def test_fetch_url_rejects_hosts_that_resolve_private() -> None:
    with patch(
        "invincat_cli.tools.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("127.0.0.1", 80))],
    ):
        with pytest.raises(ValueError, match="resolved"):
            _validate_fetch_url("https://example.com")


def test_fetch_url_accepts_public_http_url() -> None:
    with patch(
        "invincat_cli.tools.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 443))],
    ):
        _validate_fetch_url("https://example.com/docs")
