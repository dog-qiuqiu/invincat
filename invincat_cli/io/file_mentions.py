"""Parsing for @file mentions in user input."""

from __future__ import annotations

import re
from pathlib import Path

from rich.markup import escape as escape_markup

from invincat_cli.config import console

PATH_CHAR_CLASS = r"A-Za-z0-9._~/\\:-"

FILE_MENTION_PATTERN = re.compile(r"@(?P<path>(?:\\.|[" + PATH_CHAR_CLASS + r"])+)")

EMAIL_PREFIX_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]$")

INPUT_HIGHLIGHT_PATTERN = re.compile(
    r"(^\/[a-zA-Z0-9_-]+|@(?:\\.|[" + PATH_CHAR_CLASS + r"])+)"
)


def parse_file_mentions(text: str) -> tuple[str, list[Path]]:
    r"""Extract `@file` mentions and return text with resolved file paths."""
    matches = FILE_MENTION_PATTERN.finditer(text)

    files = []
    for match in matches:
        text_before = text[: match.start()]
        if text_before and EMAIL_PREFIX_PATTERN.search(text_before):
            continue

        raw_path = match.group("path")
        clean_path = raw_path.replace("\\ ", " ")

        try:
            path = Path(clean_path).expanduser()
            if not path.is_absolute():
                path = Path.cwd() / path

            resolved = path.resolve()
            if resolved.exists() and resolved.is_file():
                files.append(resolved)
            else:
                console.print(
                    f"[yellow]Warning: File not found: "
                    f"{escape_markup(raw_path)}[/yellow]"
                )
        except (OSError, RuntimeError) as e:
            console.print(
                f"[yellow]Warning: Invalid path "
                f"{escape_markup(raw_path)}: "
                f"{escape_markup(str(e))}[/yellow]"
            )

    return text, files
