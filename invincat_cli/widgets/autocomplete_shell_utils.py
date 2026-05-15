"""Shell command discovery and token helpers for autocomplete."""

from __future__ import annotations

import os
import re
from pathlib import Path


def _get_system_commands() -> list[str]:
    """Get all executable commands from PATH.

    Returns:
        List of command names available in PATH.
    """
    commands: set[str] = set()
    path_env = os.environ.get("PATH", "")

    for path_dir in path_env.split(os.pathsep):
        if not path_dir:
            continue
        try:
            path_obj = Path(path_dir)
            if not path_obj.is_dir():
                continue
            for entry in path_obj.iterdir():
                if entry.is_file() and os.access(entry, os.X_OK):
                    commands.add(entry.name)
        except OSError:
            continue

    return sorted(commands)


def _get_common_commands() -> list[str]:
    """Get a list of common shell commands for faster matching.

    Returns:
        List of commonly used command names.
    """
    return [
        "ls",
        "cd",
        "pwd",
        "cat",
        "echo",
        "mkdir",
        "rm",
        "rmdir",
        "cp",
        "mv",
        "touch",
        "find",
        "grep",
        "sed",
        "awk",
        "sort",
        "head",
        "tail",
        "wc",
        "diff",
        "chmod",
        "chown",
        "ln",
        "ps",
        "kill",
        "top",
        "htop",
        "df",
        "du",
        "free",
        "uname",
        "date",
        "cal",
        "which",
        "whereis",
        "whoami",
        "id",
        "tar",
        "gzip",
        "gunzip",
        "zip",
        "unzip",
        "curl",
        "wget",
        "ssh",
        "scp",
        "rsync",
        "ftp",
        "git",
        "svn",
        "hg",
        "python",
        "python3",
        "pip",
        "pip3",
        "node",
        "npm",
        "yarn",
        "docker",
        "docker-compose",
        "kubectl",
        "make",
        "cmake",
        "gcc",
        "g++",
        "clang",
        "clang++",
        "vi",
        "vim",
        "nvim",
        "nano",
        "emacs",
        "code",
        "man",
        "less",
        "more",
        "clear",
        "history",
        "alias",
        "export",
        "source",
        "env",
        "printenv",
        "xargs",
        "tee",
        "tr",
        "cut",
        "paste",
        "join",
        "nohup",
        "bg",
        "fg",
        "jobs",
    ]


def _escape_path(path: str) -> str:
    """Escape special characters in a path for shell.

    Args:
        path: Path string to escape.

    Returns:
        Escaped path safe for shell use.
    """
    if not path:
        return path

    needs_escape = bool(re.search(r'[ \t\n!$`&*()\\|;"\'<>?{}]', path))
    if needs_escape:
        escaped = path.replace("'", "'\"'\"'")
        return f"'{escaped}'"
    return path


def _unescape_token(token: str) -> str:
    """Unescape a shell token to get the raw string.

    Args:
        token: Escaped token from shell command line.

    Returns:
        Unescaped raw string.
    """
    if not token:
        return token

    if len(token) >= 2 and token[0] == "'" and token[-1] == "'":
        return token[1:-1].replace("'\"'\"'", "'")

    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        result = token[1:-1]
        result = result.replace("\\$", "$")
        result = result.replace("\\`", "`")
        result = result.replace('\\"', '"')
        result = result.replace("\\\\", "\\")
        return result

    result = token.replace("\\ ", " ")
    result = result.replace("\\$", "$")
    result = result.replace("\\`", "`")
    result = result.replace('\\"', '"')
    result = result.replace("\\'", "'")
    result = result.replace("\\\\", "\\")
    return result


def _parse_shell_tokens(text: str) -> list[str]:
    """Parse shell command line into tokens.

    Handles quoted strings and escaped characters.

    Args:
        text: Shell command line text.

    Returns:
        List of parsed tokens.
    """
    tokens: list[str] = []
    current = ""
    in_single_quote = False
    in_double_quote = False
    i = 0

    while i < len(text):
        char = text[i]

        if char == "\\" and i + 1 < len(text):
            if in_single_quote:
                current += char
            else:
                current += char + text[i + 1]
                i += 1
        elif char == "'" and not in_double_quote:
            if in_single_quote:
                in_single_quote = False
            else:
                in_single_quote = True
            current += char
        elif char == '"' and not in_single_quote:
            if in_double_quote:
                in_double_quote = False
            else:
                in_double_quote = True
            current += char
        elif char in " \t" and not in_single_quote and not in_double_quote:
            if current:
                tokens.append(current)
                current = ""
        else:
            current += char

        i += 1

    if current:
        tokens.append(current)

    return tokens


def _get_longest_common_prefix(strings: list[str]) -> str:
    """Get the longest common prefix of a list of strings.

    Args:
        strings: List of strings to find common prefix.

    Returns:
        Longest common prefix string.
    """
    if not strings:
        return ""
    if len(strings) == 1:
        return strings[0]

    first = strings[0]
    for i, char in enumerate(first):
        for s in strings[1:]:
            if i >= len(s) or s[i] != char:
                return first[:i]
    return first
