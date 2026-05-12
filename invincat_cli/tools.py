"""Custom tools for the CLI agent."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from tavily import TavilyClient

_UNSET = object()
_tavily_client: TavilyClient | object | None = _UNSET

_FETCH_URL_MAX_BYTES = 2 * 1024 * 1024
_FETCH_URL_MAX_REDIRECTS = 5
_FETCH_URL_CHUNK_BYTES = 64 * 1024
_FORBIDDEN_HOSTS = {"localhost", "localhost.localdomain"}


def _get_tavily_client() -> TavilyClient | None:
    """Get or initialize the lazy Tavily client singleton.

    Returns:
        TavilyClient instance, or None if API key is not configured.
    """
    global _tavily_client  # noqa: PLW0603  # Module-level cache requires global statement
    if _tavily_client is not _UNSET:
        return _tavily_client  # type: ignore[return-value]  # narrowed by sentinel check

    from invincat_cli.config import settings

    if settings.has_tavily:
        from tavily import TavilyClient as _TavilyClient

        _tavily_client = _TavilyClient(api_key=settings.tavily_api_key)
    else:
        _tavily_client = None
    return _tavily_client


def web_search(  # noqa: ANN201  # Return type depends on dynamic tool configuration
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
):
    """Search the web using Tavily for current information and documentation.

    This tool searches the web and returns relevant results. After receiving results,
    you MUST synthesize the information into a natural, helpful response for the user.

    Args:
        query: The search query (be specific and detailed)
        max_results: Number of results to return (default: 5)
        topic: Search topic type - "general" for most queries, "news" for current events
        include_raw_content: Include full page content (warning: uses more tokens)

    Returns:
        Dictionary containing:
        - results: List of search results, each with:
            - title: Page title
            - url: Page URL
            - content: Relevant excerpt from the page
            - score: Relevance score (0-1)
        - query: The original search query

    IMPORTANT: After using this tool:
    1. Read through the 'content' field of each result
    2. Extract relevant information that answers the user's question
    3. Synthesize this into a clear, natural language response
    4. Cite sources by mentioning the page titles or URLs
    5. NEVER show the raw JSON to the user - always provide a formatted response
    """
    try:
        import requests
        from tavily import (
            BadRequestError,
            InvalidAPIKeyError,
            MissingAPIKeyError,
            UsageLimitExceededError,
        )
        from tavily.errors import ForbiddenError, TimeoutError as TavilyTimeoutError
    except ImportError as exc:
        return {
            "error": f"Required package not installed: {exc.name}. "
            "Install with: pip install 'invincat-cli'",
            "query": query,
        }

    client = _get_tavily_client()
    if client is None:
        return {
            "error": "Tavily API key not configured. "
            "Please set TAVILY_API_KEY environment variable.",
            "query": query,
        }

    try:
        return client.search(
            query,
            max_results=max_results,
            include_raw_content=include_raw_content,
            topic=topic,
        )
    except (
        requests.exceptions.RequestException,
        ValueError,
        TypeError,
        # Tavily-specific exceptions
        BadRequestError,
        ForbiddenError,
        InvalidAPIKeyError,
        MissingAPIKeyError,
        TavilyTimeoutError,
        UsageLimitExceededError,
    ) as e:
        return {"error": f"Web search error: {e!s}", "query": query}


def _is_forbidden_address(address: str) -> bool:
    """Return True for local, private, or otherwise non-public IP addresses."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(
        (
            not ip.is_global,
            ip.is_loopback,
            ip.is_private,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def _validate_fetch_url(url: str) -> None:
    """Validate that a URL is safe for the agent fetch tool."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("fetch_url only supports absolute http(s) URLs")
    if parsed.username or parsed.password:
        raise ValueError("fetch_url does not allow credentials in URLs")

    host = parsed.hostname.strip().rstrip(".").lower()
    if host in _FORBIDDEN_HOSTS or host.endswith(".localhost"):
        raise ValueError("fetch_url does not allow localhost URLs")
    if _is_forbidden_address(host):
        raise ValueError("fetch_url does not allow private or local IP addresses")

    try:
        infos = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ValueError(f"Could not resolve URL host: {parsed.hostname}") from exc

    for info in infos:
        address = info[4][0]
        if _is_forbidden_address(address):
            raise ValueError("fetch_url resolved to a private or local IP address")


def _bounded_response_text(response: Any) -> str:  # noqa: ANN401
    """Read a streaming requests response with a fixed byte ceiling."""
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _FETCH_URL_MAX_BYTES:
                raise ValueError("URL response is larger than the 2 MB limit")
        except ValueError as exc:
            if "larger" in str(exc):
                raise

    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(chunk_size=_FETCH_URL_CHUNK_BYTES):
        if not chunk:
            continue
        size += len(chunk)
        if size > _FETCH_URL_MAX_BYTES:
            raise ValueError("URL response is larger than the 2 MB limit")
        chunks.append(chunk)
    encoding = response.encoding or "utf-8"
    return b"".join(chunks).decode(encoding, errors="replace")


def fetch_url(url: str, timeout: int = 30) -> dict[str, Any]:
    """Fetch content from a URL and convert HTML to markdown format.

    This tool fetches web page content and converts it to clean markdown text,
    making it easy to read and process HTML content. After receiving the markdown,
    you MUST synthesize the information into a natural, helpful response for the user.

    Args:
        url: The URL to fetch (must be a valid HTTP/HTTPS URL)
        timeout: Request timeout in seconds (default: 30)

    Returns:
        Dictionary containing:
        - success: Whether the request succeeded
        - url: The final URL after redirects
        - markdown_content: The page content converted to markdown
        - status_code: HTTP status code
        - content_length: Length of the markdown content in characters

    IMPORTANT: After using this tool:
    1. Read through the markdown content
    2. Extract relevant information that answers the user's question
    3. Synthesize this into a clear, natural language response
    4. NEVER show the raw markdown to the user unless specifically requested
    """
    try:
        import requests
        from markdownify import markdownify
    except ImportError as exc:
        return {
            "error": f"Required package not installed: {exc.name}. "
            "Install with: pip install 'invincat-cli'",
            "url": url,
        }

    try:
        _validate_fetch_url(url)
        current_url = url
        response = None
        with requests.Session() as session:
            for _ in range(_FETCH_URL_MAX_REDIRECTS + 1):
                response = session.get(
                    current_url,
                    timeout=timeout,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; Invincat/1.0)"},
                    stream=True,
                    allow_redirects=False,
                )
                if response.is_redirect or response.is_permanent_redirect:
                    location = response.headers.get("location")
                    response.close()
                    if not location:
                        return {"error": "Fetch URL error: redirect missing Location", "url": url}
                    current_url = urljoin(current_url, location)
                    _validate_fetch_url(current_url)
                    continue
                break
            else:
                return {"error": "Fetch URL error: too many redirects", "url": url}

            if response is None:
                return {"error": "Fetch URL error: no response", "url": url}
            response.raise_for_status()

            # Convert HTML content to markdown
            markdown_content = markdownify(_bounded_response_text(response))

        return {
            "success": True,
            "url": str(response.url),
            "markdown_content": markdown_content,
            "status_code": response.status_code,
            "content_length": len(markdown_content),
        }
    except (requests.exceptions.RequestException, ValueError) as e:
        return {"error": f"Fetch URL error: {e!s}", "url": url}
