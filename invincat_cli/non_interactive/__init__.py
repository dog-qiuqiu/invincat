"""Non-interactive execution mode for Invincat CLI.

Provides `run_non_interactive` which runs a single user task against the
agent graph, streams results to stdout, and exits with an appropriate code.

The agent runs inside a `langgraph dev` server subprocess, connected via
the `RemoteAgent` client (see `server.manager.server_session`).

Shell commands are gated by an optional allow-list (`--shell-allow-list`):

- Not set → shell disabled, all other tool calls auto-approved.
- `recommended` or explicit list → shell enabled, commands validated
    against the list; non-shell tools approved unconditionally.
- `all` → shell enabled, any command allowed, all tools auto-approved.

An optional quiet mode (`--quiet` / `-q`) redirects all console output to
stderr, leaving stdout exclusively for the agent's response text.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from langgraph.types import Command
from rich.console import Console
from rich.markup import escape as escape_markup
from rich.style import Style
from rich.text import Text

from invincat_cli.agent import DEFAULT_AGENT_NAME
from invincat_cli.config import (
    SHELL_ALLOW_ALL,
    build_langsmith_thread_url,
    create_model,
    settings,
)
from invincat_cli.core.version import __version__
from invincat_cli.hooks import dispatch_hook
from invincat_cli.io.file_ops import FileOpTracker
from invincat_cli.model_config import ModelConfigError
from invincat_cli.non_interactive.state import (  # noqa: F401
    StreamState,
    ThreadUrlLookupState,
    _ConsoleSpinner,
    _start_langsmith_thread_url_lookup,
    _write_newline,
    _write_text,
)
from invincat_cli.non_interactive.stream import (  # noqa: F401
    _HITL_REQUEST_ADAPTER,
    _MESSAGE_DATA_LENGTH,
    _STREAM_CHUNK_LENGTH,
    _collect_action_request_warnings,
    _make_hitl_decision,
    _process_ai_message,
    _process_hitl_interrupts,
    _process_interrupts,
    _process_message_chunk,
    _process_stream_chunk,
    _stream_agent,
)
from invincat_cli.sessions import generate_thread_id
from invincat_cli.textual_adapter import print_usage_table

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)


class HITLIterationLimitError(RuntimeError):
    """Raised when the HITL interrupt loop exceeds `_MAX_HITL_ITERATIONS` rounds."""


_MAX_HITL_ITERATIONS = 50
"""Safety cap on the number of HITL interrupt round-trips to prevent infinite
loops (e.g. when the agent keeps retrying rejected commands)."""


async def _run_agent_loop(
    agent: Any,  # noqa: ANN401
    message: str,
    config: RunnableConfig,
    console: Console,
    file_op_tracker: FileOpTracker,
    *,
    quiet: bool = False,
    stream: bool = True,
    thread_url_lookup: ThreadUrlLookupState | None = None,
) -> None:
    """Run the agent and handle HITL interrupts until the task completes.

    The loop processes at most `_MAX_HITL_ITERATIONS` rounds to prevent
    runaway retries (e.g. the agent repeatedly attempting rejected commands).

    Args:
        agent: The agent (Pregel or RemoteAgent).
        message: The user's task message.
        config: LangGraph runnable config.
        console: Rich console for formatted output.
        file_op_tracker: Tracker for file-operation diffs.
        quiet: Suppress diagnostic formatting on stdout.
        stream: When `True`, text is written to stdout as it arrives.

            When `False`, the full response is buffered and flushed at
            the end.
        thread_url_lookup: Optional non-blocking lookup state for rendering
            a fast-follow LangSmith thread link.

    Raises:
        HITLIterationLimitError: If the HITL iteration limit is exceeded.
    """
    spinner = None if quiet else _ConsoleSpinner(console)
    state = StreamState(quiet=quiet, stream=stream, spinner=spinner)
    stream_input: dict[str, Any] | Command = {
        "messages": [{"role": "user", "content": message}]
    }

    thread_id = config.get("configurable", {}).get("thread_id", "")
    await dispatch_hook("session.start", {"thread_id": thread_id})

    start_time = time.monotonic()

    # Initial stream
    await _stream_agent(agent, stream_input, config, state, console, file_op_tracker)

    # Handle HITL interrupts
    iterations = 0
    while state.interrupt_occurred:
        iterations += 1
        if iterations > _MAX_HITL_ITERATIONS:
            msg = (
                f"Exceeded {_MAX_HITL_ITERATIONS} HITL interrupt rounds. "
                "The agent may be stuck retrying rejected commands."
            )
            raise HITLIterationLimitError(msg)
        state.interrupt_occurred = False
        state.hitl_response.clear()
        _process_hitl_interrupts(state, console)
        stream_input = Command(resume=state.hitl_response)
        await _stream_agent(
            agent, stream_input, config, state, console, file_op_tracker
        )

    wall_time = time.monotonic() - start_time

    if state.full_response:
        if not state.stream:
            _write_text("".join(state.full_response))
        _write_newline()

    if not quiet:
        console.print()
        if (
            thread_url_lookup is not None
            and thread_url_lookup.done.is_set()
            and thread_url_lookup.url
        ):
            link_text = Text("View in LangSmith: ", style="dim")
            link_text.append(
                thread_url_lookup.url,
                style=Style(dim=True, link=thread_url_lookup.url),
            )
            console.print(link_text)
        console.print("[green]✓ Task completed[/green]")
        print_usage_table(state.stats, wall_time, console)

    await dispatch_hook("task.complete", {"thread_id": thread_id})
    await dispatch_hook("session.end", {"thread_id": thread_id})


def _build_non_interactive_header(
    assistant_id: str,
    thread_id: str,
    *,
    include_thread_link: bool = False,
) -> Text:
    """Build the non-interactive mode header with model, agent, and thread info.

    By default, this function avoids LangSmith network lookups and renders the
    thread ID as plain text. Callers can opt in to hyperlink resolution.

    Args:
        assistant_id: Agent identifier.
        thread_id: Thread identifier.
        include_thread_link: Whether to resolve and render a LangSmith link for
            the thread ID.

    Returns:
        Rich Text object with the formatted header line.
    """
    default_label = " (default)" if assistant_id == DEFAULT_AGENT_NAME else ""
    parts: list[tuple[str, str | Style]] = [
        (f"CLI: v{__version__}", "dim"),
        (" | ", "dim"),
        (f"Agent: {assistant_id}{default_label}", "dim"),
    ]

    if settings.model_name:
        parts.extend([(" | ", "dim"), (f"Model: {settings.model_name}", "dim")])

    parts.append((" | ", "dim"))

    thread_url = build_langsmith_thread_url(thread_id) if include_thread_link else None
    if thread_url:
        parts.extend(
            [
                ("Thread: ", "dim"),
                (thread_id, Style(dim=True, link=thread_url)),
            ]
        )
    else:
        parts.append((f"Thread: {thread_id}", "dim"))

    return Text.assemble(*parts)


async def run_non_interactive(
    message: str,
    assistant_id: str = "agent",
    thread_id: str | None = None,
    model_name: str | None = None,
    model_params: dict[str, Any] | None = None,
    sandbox_type: str = "none",  # str (not None) to match argparse choices
    sandbox_id: str | None = None,
    sandbox_setup: str | None = None,
    *,
    profile_override: dict[str, Any] | None = None,
    quiet: bool = False,
    stream: bool = True,
    mcp_config_path: str | None = None,
    no_mcp: bool = False,
    trust_project_mcp: bool = False,
) -> int:
    """Run a single task non-interactively and exit.

    The agent is created with `interactive=False`, which tailors the system
    prompt for autonomous headless execution (no clarification questions,
    reasonable assumptions).

    Shell access and auto-approval are controlled by `--shell-allow-list`:

    - Not set → shell disabled, all other tools auto-approved.
    - `recommended` or explicit list → shell enabled, commands gated by
        allow-list; non-shell tools approved unconditionally.
    - `all` → shell enabled, any command allowed, all tools auto-approved.

    Note: startup header rendering avoids synchronous LangSmith URL lookups.
    A background thread resolves the thread URL concurrently and the result is
    displayed after task completion if available.

    Args:
        message: The task/message to execute.
        assistant_id: Agent identifier for memory storage.
        thread_id: Optional existing thread ID to continue.
            When omitted, a new thread ID is generated.
        model_name: Optional model name to use.
        model_params: Extra kwargs from `--model-params` to pass to the model.

            These override config file values.
        sandbox_type: Type of sandbox (`'none'`, `'agentcore'`,
            `'daytona'`, `'langsmith'`, `'modal'`, `'runloop'`).
        sandbox_id: Optional existing sandbox ID to reuse.
        sandbox_setup: Optional path to setup script to run in the sandbox
            after creation.
        profile_override: Extra profile fields from `--profile-override`.

            Merged on top of config file profile overrides.
        quiet: When `True`, all console output (headers, status messages,
            tool notifications, HITL decisions, errors) is redirected to
            stderr so that only the agent's response text appears on stdout.
        stream: When `True` (default), text chunks are written to stdout
            as they arrive.

            When `False`, the full response is buffered and written to stdout in
            one shot after the agent finishes.
        mcp_config_path: Optional path to MCP servers JSON configuration file.
            Merged on top of auto-discovered configs (highest precedence).
        no_mcp: Disable all MCP tool loading.
        trust_project_mcp: When `True`, allow project-level MCP
            servers. When `False` (default), project MCP servers are
            silently skipped.

    Returns:
        Exit code: 0 for success, 1 for error, 130 for keyboard interrupt.
    """
    # stderr=True routes all console.print() to stderr; agent response text
    # uses _write_text() -> sys.stdout directly.
    console = Console(stderr=True) if quiet else Console()
    try:
        result = create_model(
            model_name,
            extra_kwargs=model_params,
            profile_overrides=profile_override,
        )
    except ModelConfigError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        return 1

    result.apply_to_settings()
    thread_id = thread_id or generate_thread_id()

    from invincat_cli.config import build_stream_config

    config: RunnableConfig = build_stream_config(
        thread_id, assistant_id, sandbox_type=sandbox_type
    )

    thread_url_lookup: ThreadUrlLookupState | None = None
    if not quiet:
        thread_url_lookup = _start_langsmith_thread_url_lookup(thread_id)
        console.print(Text("Running task non-interactively...", style="dim"))
        header = _build_non_interactive_header(assistant_id, thread_id)
        console.print(header)

    import asyncio

    from invincat_cli.server.manager import server_session

    # Launch MCP preload concurrently with server startup
    mcp_task: asyncio.Task[Any] | None = None
    if not no_mcp and not quiet:
        try:
            from invincat_cli.main import _preload_session_mcp_server_info

            mcp_task = asyncio.create_task(
                _preload_session_mcp_server_info(
                    mcp_config_path=mcp_config_path,
                    no_mcp=no_mcp,
                    trust_project_mcp=trust_project_mcp,
                )
            )
        except Exception:
            logger.warning("MCP metadata preload task creation failed", exc_info=True)

    try:
        enable_shell = bool(settings.shell_allow_list)
        shell_is_unrestricted = isinstance(
            settings.shell_allow_list, type(SHELL_ALLOW_ALL)
        )
        # Currently, non-shell tools have no HITL handler in non-interactive
        # mode, so interrupting on them just fragments LangSmith traces
        # without adding value. Gate only shell execution via middleware.
        use_auto_approve = not enable_shell or shell_is_unrestricted
        use_interrupt_shell_only = enable_shell and not shell_is_unrestricted
        # Extract the concrete allow-list to forward to the server subprocess.
        # settings.shell_allow_list is already validated at this point.
        restrictive_allow_list: list[str] | None = (
            list(settings.shell_allow_list)
            if use_interrupt_shell_only and settings.shell_allow_list
            else None
        )

        if not quiet:
            console.print(Text("Starting LangGraph server...", style="dim"))

        async with server_session(
            assistant_id=assistant_id,
            model_name=model_name,
            model_params=model_params,
            auto_approve=use_auto_approve,
            interrupt_shell_only=use_interrupt_shell_only,
            shell_allow_list=restrictive_allow_list,
            sandbox_type=sandbox_type,
            sandbox_id=sandbox_id,
            sandbox_setup=sandbox_setup,
            enable_shell=enable_shell,
            enable_ask_user=False,
            mcp_config_path=mcp_config_path,
            no_mcp=no_mcp,
            trust_project_mcp=trust_project_mcp,
            interactive=False,
        ) as (agent, _server_proc):
            # Collect MCP preload result (ran concurrently with server startup)
            if mcp_task is not None:
                try:
                    mcp_info = await mcp_task
                    if mcp_info:
                        tool_count = sum(len(s.tools) for s in mcp_info)
                        if tool_count:
                            label = "MCP tool" if tool_count == 1 else "MCP tools"
                            console.print(
                                f"[green]✓ Loaded {tool_count} {label}[/green]"
                            )
                except Exception:
                    logger.warning("MCP metadata preload failed", exc_info=True)

            if not quiet:
                console.print("[green]✓ Server ready[/green]")

            file_op_tracker = FileOpTracker(assistant_id=assistant_id, backend=None)

            await _run_agent_loop(
                agent,
                message,
                config,
                console,
                file_op_tracker,
                quiet=quiet,
                stream=stream,
                thread_url_lookup=thread_url_lookup,
            )

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
        return 130
    except HITLIterationLimitError as e:
        console.print(f"\n[red]{escape_markup(str(e))}[/red]")
        console.print(
            "[yellow]Hint: The agent may be repeatedly attempting commands "
            "that are not in the allow-list. Consider expanding the "
            "--shell-allow-list or adjusting the task.[/yellow]"
        )
        return 1
    except (ValueError, OSError) as e:
        logger.exception("Error during non-interactive execution")
        console.print(f"\n[red]Error: {escape_markup(str(e))}[/red]")
        return 1
    except Exception as e:
        logger.exception("Unexpected error during non-interactive execution")
        console.print(
            f"\n[red]Unexpected error ({type(e).__name__}): "
            f"{escape_markup(str(e))}[/red]"
        )
        return 1
    else:
        return 0
