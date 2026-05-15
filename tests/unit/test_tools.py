"""Unit tests for built-in agent tools."""

from __future__ import annotations

import builtins
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from invincat_cli import config, tools
from invincat_cli.tools import _bounded_response_text, _validate_fetch_url


def _install_fake_tavily(monkeypatch) -> None:
    tavily = ModuleType("tavily")
    tavily_errors = ModuleType("tavily.errors")

    class TavilyError(Exception):
        pass

    class FakeTavilyClient:
        def __init__(self, *, api_key: str | None) -> None:
            self.api_key = api_key

    tavily.TavilyClient = FakeTavilyClient
    tavily.BadRequestError = TavilyError
    tavily.InvalidAPIKeyError = TavilyError
    tavily.MissingAPIKeyError = TavilyError
    tavily.UsageLimitExceededError = TavilyError
    tavily_errors.ForbiddenError = TavilyError
    tavily_errors.TimeoutError = TavilyError

    monkeypatch.setitem(sys.modules, "tavily", tavily)
    monkeypatch.setitem(sys.modules, "tavily.errors", tavily_errors)


def test_fetch_url_rejects_non_http_urls() -> None:
    with pytest.raises(ValueError, match="http"):
        _validate_fetch_url("file:///etc/passwd")


def test_get_tavily_client_uses_settings_and_cache(monkeypatch) -> None:
    _install_fake_tavily(monkeypatch)
    monkeypatch.setattr(tools, "_tavily_client", tools._UNSET)
    monkeypatch.setattr(
        config,
        "settings",
        SimpleNamespace(has_tavily=True, tavily_api_key="key"),
    )

    client = tools._get_tavily_client()

    assert client is tools._get_tavily_client()
    assert client.api_key == "key"

    monkeypatch.setattr(tools, "_tavily_client", tools._UNSET)
    monkeypatch.setattr(
        config,
        "settings",
        SimpleNamespace(has_tavily=False, tavily_api_key=None),
    )

    assert tools._get_tavily_client() is None


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


def test_fetch_url_rejects_credentials_localhost_suffix_and_dns_failure() -> None:
    with pytest.raises(ValueError, match="credentials"):
        _validate_fetch_url("https://user:pass@example.com")

    with pytest.raises(ValueError, match="localhost"):
        _validate_fetch_url("https://app.localhost")

    with patch("invincat_cli.tools.socket.getaddrinfo", side_effect=OSError("dns")):
        with pytest.raises(ValueError, match="Could not resolve"):
            _validate_fetch_url("https://example.com")


def test_is_forbidden_address_flags_non_public_addresses() -> None:
    assert tools._is_forbidden_address("127.0.0.1")
    assert tools._is_forbidden_address("10.0.0.1")
    assert not tools._is_forbidden_address("93.184.216.34")
    assert not tools._is_forbidden_address("not-an-ip")


class FakeResponse:
    def __init__(
        self,
        *,
        url: str = "https://example.com",
        status_code: int = 200,
        body: bytes = b"<h1>Hello</h1>",
        headers: dict[str, str] | None = None,
        redirect: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}
        self.is_redirect = redirect
        self.is_permanent_redirect = False
        self.error = error
        self.encoding = "utf-8"
        self.closed = False

    def iter_content(self, *, chunk_size: int) -> list[bytes]:
        return [self.body[:chunk_size], self.body[chunk_size:]]

    def raise_for_status(self) -> None:
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, url: str, **_kwargs: object) -> FakeResponse:
        self.calls.append(url)
        return self.responses.pop(0)


def test_bounded_response_text_enforces_content_length_and_stream_limit() -> None:
    assert _bounded_response_text(FakeResponse(body=b"hello")) == "hello"

    with pytest.raises(ValueError, match="larger"):
        _bounded_response_text(
            FakeResponse(
                headers={"content-length": str(tools._FETCH_URL_MAX_BYTES + 1)}
            )
        )

    with pytest.raises(ValueError, match="larger"):
        _bounded_response_text(
            FakeResponse(body=b"x" * (tools._FETCH_URL_MAX_BYTES + 1))
        )


def test_fetch_url_success_and_redirect(monkeypatch) -> None:
    redirect = FakeResponse(
        url="https://example.com/start",
        redirect=True,
        headers={"location": "/final"},
    )
    final = FakeResponse(url="https://example.com/final", body=b"<h1>Hello</h1>")
    session = FakeSession([redirect, final])
    monkeypatch.setattr(requests, "Session", lambda: session)
    monkeypatch.setattr(
        tools.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )

    result = tools.fetch_url("https://example.com/start")

    assert result["success"] is True
    assert result["url"] == "https://example.com/final"
    assert "Hello" in result["markdown_content"]
    assert session.calls == ["https://example.com/start", "https://example.com/final"]


def test_fetch_url_reports_redirect_and_request_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        tools.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )
    monkeypatch.setattr(
        requests,
        "Session",
        lambda: FakeSession([FakeResponse(redirect=True)]),
    )

    assert (
        "redirect missing Location" in tools.fetch_url("https://example.com")["error"]
    )

    monkeypatch.setattr(
        requests,
        "Session",
        lambda: FakeSession(
            [
                FakeResponse(
                    error=requests.exceptions.HTTPError("bad status"),
                )
            ]
        ),
    )

    assert "bad status" in tools.fetch_url("https://example.com")["error"]


def test_fetch_url_reports_too_many_redirects(monkeypatch) -> None:
    monkeypatch.setattr(
        tools.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )
    redirects = [
        FakeResponse(redirect=True, headers={"location": f"/{index}"})
        for index in range(tools._FETCH_URL_MAX_REDIRECTS + 1)
    ]
    monkeypatch.setattr(requests, "Session", lambda: FakeSession(redirects))

    assert "too many redirects" in tools.fetch_url("https://example.com")["error"]


def test_web_search_reports_missing_api_key(monkeypatch) -> None:
    _install_fake_tavily(monkeypatch)
    monkeypatch.setattr(tools, "_get_tavily_client", lambda: None)

    result = tools.web_search("query")

    assert "Tavily API key not configured" in result["error"]


def test_web_search_reports_missing_dependency(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object):
        if name == "tavily":
            raise ImportError("No module named tavily", name="tavily")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = tools.web_search("query")

    assert "Required package not installed: tavily" in result["error"]


def test_web_search_returns_results_and_wraps_client_errors(monkeypatch) -> None:
    _install_fake_tavily(monkeypatch)

    class Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.error: Exception | None = None

        def search(self, query: str, **kwargs: object) -> dict[str, object]:
            self.calls.append({"query": query, **kwargs})
            if self.error is not None:
                raise self.error
            return {"results": [{"title": "Result"}], "query": query}

    client = Client()
    monkeypatch.setattr(tools, "_get_tavily_client", lambda: client)

    assert tools.web_search(
        "query",
        max_results=2,
        topic="news",
        include_raw_content=True,
    ) == {"results": [{"title": "Result"}], "query": "query"}
    assert client.calls == [
        {
            "query": "query",
            "max_results": 2,
            "include_raw_content": True,
            "topic": "news",
        }
    ]

    client.error = ValueError("bad query")

    assert "Web search error: bad query" in tools.web_search("query")["error"]


def test_fetch_url_reports_missing_dependency(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object):
        if name == "markdownify":
            raise ImportError("No module named markdownify", name="markdownify")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = tools.fetch_url("https://example.com")

    assert "Required package not installed: markdownify" in result["error"]
