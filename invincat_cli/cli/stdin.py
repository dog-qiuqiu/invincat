"""Piped stdin handling for CLI entry points."""

from __future__ import annotations

import argparse


def apply_stdin_pipe(args: argparse.Namespace) -> None:
    """Read piped stdin and merge it into the parsed CLI arguments."""
    from invincat_cli import main as _main
    from invincat_cli.config import console

    explicit_stdin = args.stdin

    if _main.sys.stdin is None:
        if explicit_stdin:
            console.print(
                "[bold red]Error:[/bold red] --stdin was passed but stdin "
                "is not available."
            )
            _main.sys.exit(1)
        return

    try:
        is_tty = _main.sys.stdin.isatty()
    except (ValueError, OSError):
        if explicit_stdin:
            console.print(
                "[bold red]Error:[/bold red] --stdin was passed but stdin "
                "state could not be determined."
            )
            _main.sys.exit(1)
        return

    if is_tty:
        if explicit_stdin:
            console.print(
                "[bold red]Error:[/bold red] --stdin was passed but stdin "
                "is a terminal. Pipe input or use -n instead.\n"
                "  cat prompt.txt | deepagents --stdin -q"
            )
            _main.sys.exit(1)
        return

    max_stdin_bytes = 10 * 1024 * 1024

    try:
        stdin_text = _main.sys.stdin.read(max_stdin_bytes + 1)
    except UnicodeDecodeError:
        msg = "Could not read piped input — ensure the input is valid text"
        console.print(f"[bold red]Error:[/bold red] {msg}")
        _main.sys.exit(1)
    except (OSError, ValueError) as exc:
        if not explicit_stdin:
            return
        from rich.markup import escape

        console.print(
            f"[bold red]Error:[/bold red] Failed to read piped input: "
            f"{escape(str(exc))}"
        )
        _main.sys.exit(1)

    if len(stdin_text) > max_stdin_bytes:
        msg = (
            f"Piped input exceeds {max_stdin_bytes // (1024 * 1024)} MiB limit. "
            "Consider writing the content to a file and referencing it instead."
        )
        console.print(f"[bold red]Error:[/bold red] {msg}")
        _main.sys.exit(1)

    stdin_text = stdin_text.strip()
    if not stdin_text:
        return

    if args.non_interactive_message:
        args.non_interactive_message = f"{stdin_text}\n\n{args.non_interactive_message}"
    elif args.initial_prompt:
        args.initial_prompt = f"{stdin_text}\n\n{args.initial_prompt}"
    else:
        args.non_interactive_message = stdin_text

    try:
        tty_fd = _main.os.open("/dev/tty", _main.os.O_RDONLY)
    except OSError:
        return

    try:
        _main.os.dup2(tty_fd, 0)
        _main.os.close(tty_fd)
        _main.sys.stdin = open(0, encoding="utf-8", closefd=False)  # noqa: SIM115
    except OSError:
        console.print(
            "[yellow]Warning:[/yellow] TTY restoration failed. "
            "Interactive mode (-m) may not work correctly."
        )
        _main.logger.warning(
            "TTY restoration failed after opening /dev/tty",
            exc_info=True,
        )
        try:
            _main.os.close(tty_fd)
        except OSError:
            _main.logger.warning(
                "Failed to close TTY fd %d during cleanup",
                tty_fd,
                exc_info=True,
            )
