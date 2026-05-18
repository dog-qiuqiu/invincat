"""Main entry point and CLI loop for Invincat."""

# ruff: noqa: E402
# Imports placed after warning filters to suppress deprecation warnings

# Suppress deprecation warnings from langchain_core (e.g., Pydantic V1 on Python 3.14+)
import warnings

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")

import argparse
import asyncio
import importlib as importlib
import importlib.util as _importlib_util  # noqa: F401
import json as json  # noqa: F401
import logging
import os
import shutil as shutil
import sys
import traceback
from pathlib import Path as Path  # noqa: F401

# Suppress Pydantic v1 compatibility warnings from langchain on Python 3.14+
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from invincat_cli.core.version import CLI_COMMAND, __version__

logger = logging.getLogger(__name__)

# Duplicated from agent.DEFAULT_AGENT_NAME to avoid importing the heavy agent
# module at startup. Keep in sync with agent.py. Tested.
_DEFAULT_AGENT_NAME = "agent"


from invincat_cli.cli.dependencies import (  # noqa: E402, F401
    _RIPGREP_SUPPRESS_HINT,
    _RIPGREP_URL,
    _ripgrep_install_hint,
    check_cli_dependencies,
    check_optional_tools,
)
from invincat_cli.cli.mcp import (  # noqa: E402, F401
    _check_mcp_project_trust,
    _preload_session_mcp_server_info,
)


def format_tool_warning_tui(tool: str) -> str:
    """Format a missing-tool warning for the TUI toast."""
    if tool == "ripgrep":
        hint = _ripgrep_install_hint()
        return (
            "ripgrep is not installed; the grep tool will use a slower fallback.\n"
            f"\nInstall: {hint}\n\n"
            f"{_RIPGREP_SUPPRESS_HINT}"
        )
    return f"{tool} is not installed."


def format_tool_warning_cli(tool: str) -> str:
    """Format a missing-tool warning for non-interactive console output."""
    if tool == "ripgrep":
        hint = _ripgrep_install_hint()
        if hint.startswith("http"):
            hint = f"[link={hint}]{hint}[/link]"
        return (
            "ripgrep is not installed; the grep tool will use a slower fallback.\n"
            f"Install: {hint}\n\n"
            f"{_RIPGREP_SUPPRESS_HINT}\n"
        )
    return f"{tool} is not installed."


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    from invincat_cli.cli.args import parse_args as _parse_args

    return _parse_args(default_agent_name=_DEFAULT_AGENT_NAME)


from invincat_cli.cli.acp import _run_acp_cli_async  # noqa: E402, F401
from invincat_cli.cli.runtime import (  # noqa: E402, F401
    _ensure_utf8_locale,
    _handle_default_model_command,
    _handle_update_command,
    _load_json_object_arg,
    _print_session_stats,
    _run_wecombot_foreground,
)
from invincat_cli.cli.stdin import apply_stdin_pipe  # noqa: E402, F401
from invincat_cli.cli.textual import run_textual_cli_async  # noqa: E402, F401


def cli_main() -> None:
    """Entry point for console script."""
    # Fix for gRPC fork issue on macOS
    # https://github.com/grpc/grpc/issues/37642
    if sys.platform == "darwin":
        os.environ["GRPC_ENABLE_FORK_SUPPORT"] = "0"

    # Textual's Linux input driver decodes terminal bytes as UTF-8. Enforce a
    # UTF-8 locale early so non-UTF-8 terminals (e.g. GBK on Chinese systems)
    # don't cause a UnicodeDecodeError in the input thread.
    if sys.platform != "darwin":
        _ensure_utf8_locale()

    # Note: LANGSMITH_PROJECT override is handled lazily by config.py's
    # _ensure_bootstrap() (triggered on first access of `settings`).
    # This ensures agent traces use DEEPAGENTS_CLI_LANGSMITH_PROJECT while
    # shell commands use the user's original LANGSMITH_PROJECT.

    # Fast path: print version without loading heavy dependencies
    if len(sys.argv) == 2 and sys.argv[1] in {"-v", "--version"}:  # noqa: PLR2004  # argv length check for fast-path
        try:
            from importlib.metadata import (
                PackageNotFoundError,
            )
            from importlib.metadata import (
                version as _pkg_version,
            )

            sdk_version = _pkg_version("deepagents")
        except PackageNotFoundError:
            sdk_version = "unknown"
        except Exception:  # Best-effort SDK version lookup
            logger.debug("Unexpected error looking up SDK version", exc_info=True)
            sdk_version = "unknown"
        print(f"invincat-cli {__version__}\ndeepagents (SDK) {sdk_version}")  # noqa: T201  # CLI version output
        sys.exit(0)

    # ACP mode does not require Textual, so skip UI dependency checks when
    # the flag is present in raw argv.
    if "--acp" not in sys.argv[1:]:
        check_cli_dependencies()

    try:
        args = parse_args()

        # Import console/settings AFTER arg parsing so --help (which exits
        # inside parse_args) never pays the settings bootstrap cost.
        from invincat_cli.config import console, settings

        model_params = _load_json_object_arg(
            raw_value=getattr(args, "model_params", None),
            flag_name="--model-params",
            console=console,
        )
        profile_override = _load_json_object_arg(
            raw_value=getattr(args, "profile_override", None),
            flag_name="--profile-override",
            console=console,
        )

        if getattr(args, "acp", False):
            try:
                from acp import run_agent as run_acp_agent
                from deepagents_acp.server import AgentServerACP
            except ImportError as exc:
                msg = (
                    f"ACP dependencies not available: {exc}\n"
                    "Install with: pip install deepagents-acp\n"
                )
                sys.stderr.write(msg)
                sys.stderr.flush()
                sys.exit(1)

            if getattr(args, "no_mcp", False) and getattr(args, "mcp_config", None):
                msg = (
                    "Error: --no-mcp and --mcp-config are mutually exclusive."
                    " Use one or the other.\n"
                    "  invincat-cli --mcp-config path/to/config.json\n"
                    "  invincat-cli --no-mcp\n"
                )
                sys.stderr.write(msg)
                sys.stderr.flush()
                sys.exit(2)

            exit_code = asyncio.run(
                _run_acp_cli_async(
                    assistant_id=args.agent,
                    run_acp_agent=run_acp_agent,
                    agent_server_cls=AgentServerACP,
                    model_name=getattr(args, "model", None),
                    model_params=model_params,
                    profile_override=profile_override,
                    mcp_config_path=getattr(args, "mcp_config", None),
                    no_mcp=getattr(args, "no_mcp", False),
                    trust_project_mcp=getattr(args, "trust_project_mcp", False),
                )
            )
            sys.exit(exit_code)

        # Apply shell-allow-list from command line if provided (overrides env var)
        if args.shell_allow_list:
            from invincat_cli.config import parse_shell_allow_list

            settings.shell_allow_list = parse_shell_allow_list(args.shell_allow_list)

        if args.command != "wecombot":
            apply_stdin_pipe(args)

        if getattr(args, "no_mcp", False) and getattr(args, "mcp_config", None):
            from rich.console import Console as _Console

            _Console(stderr=True).print(
                "[bold red]Error:[/bold red] --no-mcp and --mcp-config "
                "are mutually exclusive. Use one or the other.\n"
                "  invincat-cli --mcp-config path/to/config.json\n"
                "  invincat-cli --no-mcp"
            )
            sys.exit(2)

        if (args.quiet or args.no_stream) and not args.non_interactive_message:
            # Print to stderr (not the module-level stdout console) and exit
            # with code 2 to match the POSIX convention for usage errors, as
            # argparse's parser.error() would.
            from rich.console import Console as _Console

            flags = []
            if args.quiet:
                flags.append("--quiet")
            if args.no_stream:
                flags.append("--no-stream")
            flag = " and ".join(flags)
            _Console(stderr=True).print(
                f"[bold red]Error:[/bold red] {flag} requires "
                "--non-interactive (-n) or piped stdin\n"
                "  invincat-cli -n 'summarize README.md' --quiet"
            )
            sys.exit(2)

        # Handle --update flag or `update` subcommand (headless, no session)
        if args.update or args.command == "update":
            _handle_update_command(console)

        # Handle --default-model / --clear-default-model (headless, no session)
        _handle_default_model_command(args, console)

        output_format = getattr(args, "output_format", "text")

        if args.command == "help":
            from invincat_cli.presentation.help import show_help

            show_help()
        elif args.command == "agents":
            from invincat_cli.agent import list_agents
            from invincat_cli.presentation.help import show_agents_help

            # "ls" is an argparse alias for "list"
            if args.agents_command in {"list", "ls"}:
                list_agents(output_format=output_format)
            else:
                show_agents_help()
        elif args.command == "wecombot":
            _run_wecombot_foreground(console)
        elif args.command == "skills":
            from invincat_cli.skills import execute_skills_command

            execute_skills_command(args)
        elif args.command == "threads":
            from invincat_cli.presentation.help import show_threads_help
            from invincat_cli.sessions import (
                delete_thread_command,
                list_threads_command,
            )

            # "ls" is an argparse alias for "list" — argparse stores the
            # alias as-is in the namespace, so we must match both values.
            if args.threads_command in {"list", "ls"}:
                asyncio.run(
                    list_threads_command(
                        agent_name=getattr(args, "agent", None),
                        limit=getattr(args, "limit", None),
                        sort_by=getattr(args, "sort", None),
                        branch=getattr(args, "branch", None),
                        verbose=getattr(args, "verbose", False),
                        relative=getattr(args, "relative", None),
                        output_format=output_format,
                    )
                )
            elif args.threads_command == "delete":
                asyncio.run(
                    delete_thread_command(
                        args.thread_id,
                        dry_run=args.dry_run,
                        output_format=output_format,
                    )
                )
            else:
                # No subcommand provided, show threads help screen
                show_threads_help()
        elif args.non_interactive_message:
            # Check for optional tools before running agent (stderr so
            # --quiet piped output stays clean)
            try:
                from rich.console import Console as _Console
            except ImportError:
                logger.warning(
                    "Could not import rich.console; skipping tool warnings",
                    exc_info=True,
                )
            else:
                try:
                    warn_console = _Console(stderr=True)
                    for tool in check_optional_tools():
                        warn_console.print(
                            f"[yellow]Warning:[/yellow] {format_tool_warning_cli(tool)}"
                        )
                except Exception:
                    logger.debug("Failed to check for optional tools", exc_info=True)
            # Validate sandbox provider deps before spawning server subprocess
            if args.sandbox and args.sandbox not in {"none", "langsmith"}:
                from invincat_cli.integrations.sandbox_factory import (
                    verify_sandbox_deps,
                )

                try:
                    verify_sandbox_deps(args.sandbox)
                except ImportError as exc:
                    from rich.markup import escape

                    console.print(f"[bold red]Error:[/bold red] {escape(str(exc))}")
                    sys.exit(1)

            # Non-interactive mode - execute single task and exit
            from invincat_cli.non_interactive import run_non_interactive

            exit_code = asyncio.run(
                run_non_interactive(
                    message=args.non_interactive_message,
                    assistant_id=args.agent,
                    thread_id=getattr(args, "thread_id", None),
                    model_name=getattr(args, "model", None),
                    model_params=model_params,
                    profile_override=profile_override,
                    sandbox_type=args.sandbox,
                    sandbox_id=args.sandbox_id,
                    sandbox_setup=getattr(args, "sandbox_setup", None),
                    quiet=args.quiet,
                    stream=not args.no_stream,
                    mcp_config_path=getattr(args, "mcp_config", None),
                    no_mcp=getattr(args, "no_mcp", False),
                    trust_project_mcp=getattr(args, "trust_project_mcp", False),
                )
            )
            sys.exit(exit_code)
        else:
            # Interactive mode - handle thread resume
            from rich.style import Style
            from rich.text import Text

            from invincat_cli.config import (
                build_langsmith_thread_url,
            )
            from invincat_cli.sessions import (
                generate_thread_id,
                thread_exists,
            )

            # Instead of resolving thread_id here with synchronous asyncio.run()
            # DB calls, pass the raw resume request to the TUI and let it
            # resolve asynchronously during startup.
            resume_thread = args.resume_thread  # "__MOST_RECENT__", "<id>", or None
            thread_id = None if resume_thread else generate_thread_id()

            # Validate sandbox provider deps before spawning server subprocess
            if args.sandbox and args.sandbox not in {"none", "langsmith"}:
                from invincat_cli.integrations.sandbox_factory import (
                    verify_sandbox_deps,
                )

                try:
                    verify_sandbox_deps(args.sandbox)
                except ImportError as exc:
                    from rich.markup import escape

                    console.print(f"[bold red]Error:[/bold red] {escape(str(exc))}")
                    sys.exit(1)

            # Check project MCP trust before launching TUI
            mcp_trust_decision = _check_mcp_project_trust(
                trust_flag=getattr(args, "trust_project_mcp", False),
            )

            # Run Textual CLI
            return_code = 0
            try:
                result = asyncio.run(
                    run_textual_cli_async(
                        assistant_id=args.agent,
                        auto_approve=args.auto_approve,
                        sandbox_type=args.sandbox,
                        sandbox_id=args.sandbox_id,
                        sandbox_setup=getattr(args, "sandbox_setup", None),
                        model_name=getattr(args, "model", None),
                        model_params=model_params,
                        profile_override=profile_override,
                        thread_id=thread_id,
                        resume_thread=resume_thread,
                        initial_prompt=getattr(args, "initial_prompt", None),
                        mcp_config_path=getattr(args, "mcp_config", None),
                        no_mcp=getattr(args, "no_mcp", False),
                        trust_project_mcp=mcp_trust_decision,
                    )
                )
                return_code = result.return_code
                # The user may have switched threads via /threads during the
                # session; use the final thread ID for teardown messages.
                thread_id = result.thread_id or thread_id
                _print_session_stats(result.session_stats, console)
            except Exception as e:  # noqa: BLE001  # Top-level error handler for the application
                error_msg = Text("\nApplication error: ", style="red")
                error_msg.append(str(e))
                console.print(error_msg)
                console.print(Text(traceback.format_exc(), style="dim"))
                sys.exit(1)

            # Show LangSmith thread link for threads with checkpointed
            # content (same table that backs the `/threads` listing).
            if thread_id:
                try:
                    thread_url = build_langsmith_thread_url(thread_id)
                    if thread_url and asyncio.run(thread_exists(thread_id)):
                        console.print()
                        ls_hint = Text("View this thread in LangSmith: ", style="dim")
                        ls_hint.append(
                            thread_url,
                            style=Style(dim=True, link=thread_url),
                        )
                        console.print(ls_hint)
                except Exception:
                    logger.debug(
                        "Could not display LangSmith thread URL on teardown",
                        exc_info=True,
                    )

            # Show resume hint on exit for threads with checkpointed content.
            try:
                if (
                    thread_id
                    and return_code == 0
                    and asyncio.run(thread_exists(thread_id))
                ):
                    console.print()
                    console.print("[dim]Resume this thread with:[/dim]")
                    hint = Text(f"{CLI_COMMAND} -r ", style="cyan")
                    hint.append(str(thread_id), style="cyan")
                    console.print(hint)
            except Exception:
                logger.debug(
                    "Could not display resume hint on teardown",
                    exc_info=True,
                )

            # Warn about available update on exit
            try:
                if result.update_available[0]:
                    from invincat_cli.update_check import (
                        is_auto_update_enabled,
                        upgrade_command,
                    )

                    latest = result.update_available[1]
                    console.print()
                    update_msg = Text("Update available: ", style="yellow bold")
                    update_msg.append(f"v{latest}", style="yellow")
                    console.print(update_msg)
                    cmd_hint = Text("Run: ", style="dim")
                    cmd_hint.append(upgrade_command(), style="cyan")
                    console.print(cmd_hint)
                    if not is_auto_update_enabled():
                        auto_hint = Text("Enable auto-updates: ", style="dim")
                        auto_hint.append("/auto-update", style="cyan")
                        console.print(auto_hint)
            except Exception:
                logger.debug("Failed to display exit update banner", exc_info=True)
    except KeyboardInterrupt:
        # Clean exit on Ctrl+C — suppress ugly traceback.
        # `console` may not be bound if Ctrl+C arrives during config import.
        try:
            console.print("\n\n[yellow]Interrupted[/yellow]")
        except NameError:
            sys.stderr.write("\n\nInterrupted\n")
        sys.exit(0)


if __name__ == "__main__":
    cli_main()
