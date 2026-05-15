"""Dropped and pasted file-path parsing."""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

_UNICODE_SPACE_EQUIVALENTS = str.maketrans(
    {
        "\u00a0": " ",
        "\u202f": " ",
    }
)

_WINDOWS_DRIVE_PATH_PATTERN = __import__("re").compile(r"^[A-Za-z]:[\\/]")


@dataclass(frozen=True)
class ParsedPastedPathPayload:
    """Unified parse result for dropped-path payload detection."""

    paths: list[Path]
    token_end: int | None = None


def parse_pasted_file_paths(text: str) -> list[Path]:
    r"""Parse a paste payload that may contain dragged-and-dropped file paths."""
    payload = text.strip()
    if not payload:
        return []

    tokens: list[str] = []
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line_tokens = _split_paste_line(line)
        if not line_tokens:
            return []
        tokens.extend(line_tokens)

    if not tokens:  # pragma: no cover - defensive guard after non-empty parsed lines
        return []

    paths: list[Path] = []
    for token in tokens:
        path = _token_to_path(token)
        if path is None:
            return []
        resolved = _resolve_existing_pasted_path(path)
        if resolved is None:
            return []
        paths.append(resolved)

    return paths


def parse_pasted_path_payload(
    text: str, *, allow_leading_path: bool = False
) -> ParsedPastedPathPayload | None:
    """Parse dropped-path payload variants through one entrypoint."""
    paths = parse_pasted_file_paths(text)
    if paths:
        return ParsedPastedPathPayload(paths=paths)

    single_path = parse_single_pasted_file_path(text)
    if single_path is not None:
        return ParsedPastedPathPayload(paths=[single_path])

    if not allow_leading_path:
        return None

    leading = extract_leading_pasted_file_path(text)
    if leading is None:
        return None

    path, token_end = leading
    return ParsedPastedPathPayload(paths=[path], token_end=token_end)


def parse_single_pasted_file_path(text: str) -> Path | None:
    """Parse and resolve a single pasted path payload."""
    candidate = normalize_pasted_path(text)
    if candidate is None:
        return None
    return _resolve_existing_pasted_path(candidate)


def extract_leading_pasted_file_path(text: str) -> tuple[Path, int] | None:
    """Extract and resolve a leading pasted path token from input text."""
    if not text:
        return None

    start = len(text) - len(text.lstrip())
    payload = text[start:]
    token_end = _leading_token_end(payload)
    if token_end is None:
        return None

    token_text = payload[:token_end]
    path = parse_single_pasted_file_path(token_text)
    if path is None:
        spaced = _extract_unquoted_leading_path_with_spaces(payload)
        if spaced is None:
            return None
        spaced_path, spaced_end = spaced
        return spaced_path, start + spaced_end

    return path, start + token_end


def normalize_pasted_path(text: str) -> Path | None:
    """Normalize pasted text that may represent a single filesystem path."""
    payload = text.strip()
    if not payload:
        return None

    unquoted = (
        payload.removeprefix('"').removesuffix('"')
        if payload.startswith('"') and payload.endswith('"')
        else payload
    )
    unquoted = (
        unquoted.removeprefix("'").removesuffix("'")
        if unquoted.startswith("'") and unquoted.endswith("'")
        else unquoted
    )

    if unquoted.startswith("file://"):
        return _token_to_path(unquoted)

    windows_path = _normalize_windows_pasted_path(unquoted)
    if windows_path is not None:
        return windows_path

    posix_path = _normalize_posix_pasted_path(unquoted)
    if posix_path is not None:
        return posix_path

    parts = _split_paste_line(payload)
    if len(parts) != 1:
        return None
    token = parts[0]
    path = _token_to_path(token)
    if path is None:
        return None
    windows_token_path = _normalize_windows_pasted_path(str(path))
    if windows_token_path is not None:
        return windows_token_path
    return path


def _split_paste_line(line: str) -> list[str]:
    """Split a single pasted line into path-like tokens."""
    try:
        return shlex.split(line, posix=True)
    except ValueError:
        return []


def _token_to_path(token: str) -> Path | None:
    """Convert a pasted token into a path candidate."""
    value = token.strip()
    if not value:
        return None

    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
        if not value:
            return None

    if value.startswith("file://"):
        parsed = urlparse(value)
        path_text = unquote(parsed.path or "")
        if parsed.netloc and parsed.netloc != "localhost":
            path_text = f"//{parsed.netloc}{path_text}"
        if (
            path_text.startswith("/")
            and len(path_text) > 2  # noqa: PLR2004
            and path_text[2] == ":"
            and path_text[1].isalpha()
        ):
            path_text = path_text[1:]
        if not path_text:
            return None
        return Path(path_text)

    return Path(value)


def _leading_token_end(text: str) -> int | None:
    """Return the end index of the first shell-like token."""
    if not text:
        return None

    if text[0] in {'"', "'"}:
        quote = text[0]
        escaped = False
        for index in range(1, len(text)):
            char = text[index]
            if char == "\\" and not escaped:
                escaped = True
                continue
            if char == quote and not escaped:
                return index + 1
            escaped = False
        return None

    escaped = False
    for index, char in enumerate(text):
        if char == "\\" and not escaped:
            escaped = True
            continue
        if char.isspace() and not escaped:
            return index
        escaped = False
    return len(text)


def _extract_unquoted_leading_path_with_spaces(text: str) -> tuple[Path, int] | None:
    """Extract a leading unquoted path that may contain spaces."""
    if not text or ("\n" in text or "\r" in text):
        return None
    if not text.startswith(("/", "~/")):
        return None
    if " " not in text and "\u00a0" not in text and "\u202f" not in text:
        return None

    boundaries = [index for index, char in enumerate(text) if char.isspace()]
    boundaries.append(len(text))
    for end in reversed(boundaries):
        candidate = text[:end].rstrip()
        if not candidate:  # pragma: no cover
            continue
        path = parse_single_pasted_file_path(candidate)
        if path is not None:
            return path, len(candidate)
    return None


def _normalize_windows_pasted_path(text: str) -> Path | None:
    """Return a path for unquoted Windows drive/UNC path inputs."""
    if _WINDOWS_DRIVE_PATH_PATTERN.match(text) or text.startswith("\\\\"):
        return Path(text)
    return None


def _normalize_posix_pasted_path(text: str) -> Path | None:
    """Return a path for likely POSIX absolute/home path payloads."""
    if "\n" in text or "\r" in text:
        return None
    if text.startswith("~/"):
        return Path(text)
    if text.startswith("/") and "/" in text[1:]:
        return Path(text)
    return None


def _resolve_existing_pasted_path(path: Path) -> Path | None:
    """Resolve a pasted path candidate to an existing file."""
    try:
        resolved = path.expanduser().resolve()
    except (OSError, RuntimeError) as e:
        logger.debug("Path resolution failed for %r: %s", path, e)
        return None
    if resolved.exists() and resolved.is_file():
        return resolved

    from invincat_cli.io import input as _input

    fuzzy = _input._resolve_with_unicode_space_variants(path)
    if fuzzy is None:
        return None
    try:
        resolved_fuzzy = fuzzy.resolve()
    except (OSError, RuntimeError) as e:
        logger.debug("Unicode-space resolution failed for %r: %s", fuzzy, e)
        return None
    if resolved_fuzzy.exists() and resolved_fuzzy.is_file():
        return resolved_fuzzy
    return None


def _normalize_unicode_spaces(text: str) -> str:
    """Normalize Unicode lookalike spaces to ASCII spaces."""
    return text.translate(_UNICODE_SPACE_EQUIVALENTS)


def _resolve_with_unicode_space_variants(path: Path) -> Path | None:
    """Resolve path by matching filename segments with Unicode space variants."""
    expanded = path.expanduser()
    if expanded.is_absolute():
        current = Path(expanded.anchor)
        parts = expanded.parts[1:]
    else:
        current = Path.cwd()
        parts = expanded.parts

    for index, part in enumerate(parts):
        candidate = current / part
        if candidate.exists():
            current = candidate
            continue

        if not current.exists() or not current.is_dir():
            return None
        if " " not in part and "\u00a0" not in part and "\u202f" not in part:
            return None

        normalized_part = _normalize_unicode_spaces(part)
        try:
            matches = [
                entry
                for entry in current.iterdir()
                if _normalize_unicode_spaces(entry.name) == normalized_part
            ]
        except OSError as e:
            logger.debug("Failed listing %s for Unicode-space lookup: %s", current, e)
            return None

        if not matches:
            return None

        is_last = index == len(parts) - 1
        if is_last:
            file_matches = [entry for entry in matches if entry.is_file()]
            if file_matches:
                matches = file_matches
        else:
            dir_matches = [entry for entry in matches if entry.is_dir()]
            if dir_matches:
                matches = dir_matches

        matches.sort(key=lambda entry: entry.name)
        current = matches[0]

    return current
