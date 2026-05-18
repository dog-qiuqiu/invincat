"""Runtime helper commands for the CLI entrypoint."""

from __future__ import annotations

import argparse
from typing import Any


def _print_session_stats(stats: Any, console: Any) -> None:  # noqa: ANN401
    """Print a session-level usage stats table to the console on TUI exit."""
    from invincat_cli.textual_adapter import SessionStats, print_usage_table

    if not isinstance(stats, SessionStats):
        return
    print_usage_table(stats, stats.wall_time_seconds, console)


def _ensure_utf8_locale() -> None:
    """Force a UTF-8 locale for Textual terminal input."""
    import locale

    from invincat_cli import main as _main

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass

    preferred = locale.getpreferredencoding(False)
    if preferred.upper().replace("-", "") == "UTF8":
        return

    for utf8_locale in ("C.UTF-8", "en_US.UTF-8", "POSIX"):
        try:
            locale.setlocale(locale.LC_ALL, utf8_locale)
            _main.os.environ["LANG"] = utf8_locale
            _main.os.environ["LC_ALL"] = utf8_locale
            return
        except locale.Error:
            continue

    _main.os.environ.setdefault("LANG", "en_US.UTF-8")
    _main.os.environ.setdefault("LC_ALL", "en_US.UTF-8")
    _main.sys.stderr.write(
        "Warning: terminal locale is not UTF-8; non-ASCII input may cause errors.\n"
        "Set LANG=en_US.UTF-8 to suppress this message.\n"
    )


def _load_json_object_arg(
    *,
    raw_value: str | None,
    flag_name: str,
    console: Any,
) -> dict[str, Any] | None:
    """Parse an optional JSON-object CLI argument."""
    from invincat_cli import main as _main

    if not raw_value:
        return None
    try:
        parsed = _main.json.loads(raw_value)
    except _main.json.JSONDecodeError as e:
        console.print(f"[bold red]Error:[/bold red] {flag_name} is not valid JSON: {e}")
        _main.sys.exit(1)
    if not isinstance(parsed, dict):
        console.print(f"[bold red]Error:[/bold red] {flag_name} must be a JSON object")
        _main.sys.exit(1)
    return parsed


def _handle_update_command(console: Any) -> None:
    """Run the headless update flow and exit."""
    from invincat_cli import main as _main

    try:
        from rich.markup import escape

        from invincat_cli.core.version import __version__ as cli_version
        from invincat_cli.update_check import (
            is_update_available,
            perform_upgrade,
            upgrade_command,
        )

        console.print("Checking for updates...", style="dim")
        available, latest = is_update_available(bypass_cache=True)
        if latest is None:
            console.print(
                "[bold yellow]Warning:[/bold yellow] Could not "
                "reach PyPI. Check your network and try again."
            )
            _main.sys.exit(1)
        if not available:
            console.print(f"Already on the latest version (v{cli_version}).")
            _main.sys.exit(0)

        console.print(
            f"Update available: v{latest} (current: v{cli_version}). Upgrading..."
        )
        success, output = _main.asyncio.run(perform_upgrade())
        if success:
            console.print(f"[green]Updated to v{latest}.[/green]")
        else:
            cmd = upgrade_command()
            detail = f": {escape(output[:200])}" if output else ""
            console.print(
                f"[bold red]Auto-update failed{detail}[/bold red]\n"
                f"Run manually: [cyan]{cmd}[/cyan]"
            )
            _main.sys.exit(1)
        _main.sys.exit(0)
    except Exception:
        _main.logger.warning("--update failed", exc_info=True)
        console.print(
            "[bold red]Error:[/bold red] Update failed.\n"
            "Run manually: [cyan]uv tool upgrade invincat-cli[/cyan]"
        )
        _main.sys.exit(1)


def _handle_default_model_command(args: argparse.Namespace, console: Any) -> None:
    """Handle default-model show, set, and clear commands, then exit."""
    from invincat_cli import main as _main

    if args.clear_default_model:
        from invincat_cli.model_config import clear_default_model

        if clear_default_model():
            console.print("Default model cleared.")
        else:
            console.print(
                "[bold red]Error:[/bold red] Could not clear default model. "
                "Check permissions for ~/.invincat/"
            )
            _main.sys.exit(1)
        _main.sys.exit(0)

    if args.default_model is None:
        return

    from invincat_cli.model_config import ModelConfig, save_default_model

    if args.default_model == "__SHOW__":
        config = ModelConfig.load()
        if config.default_model:
            console.print(f"Default model: {config.default_model}")
        else:
            console.print("No default model set.")
        _main.sys.exit(0)

    model_spec = args.default_model
    from invincat_cli.config import detect_provider
    from invincat_cli.model_config import ModelSpec

    parsed = ModelSpec.try_parse(model_spec)
    if not parsed:
        provider = detect_provider(model_spec)
        if provider:
            model_spec = f"{provider}:{model_spec}"

    if save_default_model(model_spec):
        console.print(f"Default model set to {model_spec}")
    else:
        console.print(
            "[bold red]Error:[/bold red] Could not save default model. "
            "Check permissions for ~/.invincat/"
        )
        _main.sys.exit(1)
    _main.sys.exit(0)


def _run_wecombot_foreground(console: Any) -> None:
    """Run the WeCom daemon in the foreground for debugging."""
    from invincat_cli import main as _main
    from invincat_cli.wecom.daemon import WeComDaemonConfig, run_daemon_foreground

    cwd = _main.Path.cwd()
    try:
        config = WeComDaemonConfig.from_env(cwd)
    except ValueError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        _main.sys.exit(1)
    console.print(f"Running WeCom daemon in foreground (cwd={cwd})...")
    run_daemon_foreground(config)
    _main.sys.exit(0)
