"""Shell allow-list parsing and command safety checks."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

SHELL_TOOL_NAMES: frozenset[str] = frozenset({"bash", "shell", "execute"})
"""Tool names recognized as shell/command-execution tools.

Only ``execute`` is registered by the SDK and CLI backends in practice.
``bash`` and ``shell`` are legacy names carried over and kept as
backwards-compatible aliases.
"""


class _ShellAllowAll(list):  # noqa: FURB189  # sentinel type, not a general-purpose list subclass
    """Sentinel subclass for unrestricted shell access.

    Using a dedicated type instead of a plain list lets consumers use
    ``isinstance`` checks, which survive serialization/copy unlike identity
    checks (``is``).
    """


SHELL_ALLOW_ALL: list[str] = _ShellAllowAll(["__ALL__"])
"""Sentinel value returned by ``parse_shell_allow_list`` for unrestricted shell."""

DANGEROUS_SHELL_PATTERNS = (
    "$(",  # Command substitution
    "`",  # Backtick command substitution
    "$'",  # ANSI-C quoting (can encode dangerous chars via escape sequences)
    "\n",  # Newline (command injection)
    "\r",  # Carriage return (command injection)
    "\t",  # Tab (can be used for injection in some shells)
    "<(",  # Process substitution (input)
    ">(",  # Process substitution (output)
    "<<<",  # Here-string
    "<<",  # Here-doc (can embed commands)
    ">>",  # Append redirect
    ">",  # Output redirect
    "<",  # Input redirect
    "${",  # Variable expansion with braces (can run commands via ${var:-$(cmd)})
)
"""Literal substrings that indicate shell injection risk."""

RECOMMENDED_SAFE_SHELL_COMMANDS = (
    # Directory listing
    "ls",
    "dir",
    # File content viewing (read-only)
    "cat",
    "head",
    "tail",
    # Text searching (read-only)
    "grep",
    "wc",
    "strings",
    # Text processing (read-only, no shell execution)
    "cut",
    "tr",
    "diff",
    "md5sum",
    "sha256sum",
    # Path utilities
    "pwd",
    "which",
    # System info (read-only)
    "uname",
    "hostname",
    "whoami",
    "id",
    "groups",
    "uptime",
    "nproc",
    "lscpu",
    "lsmem",
    # Process viewing (read-only)
    "ps",
)
"""Read-only commands auto-approved in non-interactive mode."""

PATH_SCOPED_READ_COMMANDS = frozenset(
    {
        "cat",
        "head",
        "tail",
        "grep",
        "wc",
        "strings",
        "md5sum",
        "sha256sum",
    }
)
"""Allow-listed commands whose file path arguments must stay under cwd."""


def parse_shell_allow_list(allow_list_str: str | None) -> list[str] | None:
    """Parse shell allow-list from a comma-separated string."""
    if not allow_list_str:
        return None

    stripped = allow_list_str.strip().lower()
    if stripped == "all":
        return SHELL_ALLOW_ALL
    if stripped == "recommended":
        return list(RECOMMENDED_SAFE_SHELL_COMMANDS)

    commands = [cmd.strip() for cmd in allow_list_str.split(",") if cmd.strip()]
    if any(cmd.lower() == "all" for cmd in commands):
        msg = (
            "Cannot combine 'all' with other commands in --shell-allow-list. "
            "Use '--shell-allow-list all' alone to allow any command."
        )
        raise ValueError(msg)

    result: list[str] = []
    for cmd in commands:
        if cmd.lower() == "recommended":
            result.extend(RECOMMENDED_SAFE_SHELL_COMMANDS)
        else:
            result.append(cmd)

    seen: set[str] = set()
    unique: list[str] = []
    for cmd in result:
        if cmd not in seen:
            seen.add(cmd)
            unique.append(cmd)
    return unique


def contains_dangerous_patterns(command: str) -> bool:
    """Return whether a shell command contains injection-prone syntax."""
    if any(pattern in command for pattern in DANGEROUS_SHELL_PATTERNS):
        return True

    # Bare variable expansion ($VAR without braces) can leak sensitive paths.
    if re.search(r"\$[A-Za-z_]", command):
        return True

    # Standalone & changes the execution model; && is handled as a command
    # separator by the allow-list parser.
    return bool(re.search(r"(?<![&])&(?![&])", command))


def _path_arg_stays_within_cwd(arg: str, cwd: Path) -> bool:
    if arg == "-" or arg.startswith("-"):
        return True
    # Treat glob/pattern-only tokens as non-paths unless they explicitly refer
    # to directories. Shell expansion happens later, so absolute, home, and
    # parent traversal are rejected before the shell can expand them.
    if arg.startswith(("~", "/")) or ".." in Path(arg).parts:
        return False
    try:
        candidate = (cwd / arg).expanduser().resolve()
        if "/" not in arg and not arg.startswith(".") and not candidate.exists():
            return True
        candidate.relative_to(cwd)
    except (OSError, ValueError):
        return False
    return True


def is_shell_command_allowed(
    command: str,
    allow_list: list[str] | None,
    *,
    cwd: str | Path | None = None,
) -> bool:
    """Return whether a shell command is covered by the restrictive allow-list."""
    if not allow_list or not command or not command.strip():
        return False

    if isinstance(allow_list, _ShellAllowAll):
        return True

    if contains_dangerous_patterns(command):
        return False

    allow_set = set(allow_list)
    segments = re.split(r"&&|\|\||[|;]", command)
    found_command = False

    for raw_segment in segments:
        segment = raw_segment.strip()
        if not segment:
            continue

        try:
            tokens = shlex.split(segment)
        except ValueError:
            return False
        if not tokens:
            continue

        found_command = True
        cmd_name = tokens[0]
        if cmd_name not in allow_set and Path(cmd_name).name not in allow_set:
            return False
        if Path(cmd_name).name in PATH_SCOPED_READ_COMMANDS:
            root = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
            if not all(_path_arg_stays_within_cwd(arg, root) for arg in tokens[1:]):
                return False

    return found_command
