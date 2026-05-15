"""Stream chunk processing for non-interactive execution."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from langchain.agents.middleware.human_in_the_loop import ActionRequest, HITLRequest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command, Interrupt
from pydantic import TypeAdapter, ValidationError
from rich.console import Console
from rich.markup import escape as escape_markup

from invincat_cli.config import (
    SHELL_TOOL_NAMES,
    is_shell_command_allowed,
    settings,
)
from invincat_cli.hooks import dispatch_hook_fire_and_forget
from invincat_cli.io.file_ops import FileOpTracker
from invincat_cli.non_interactive.state import StreamState, _write_newline, _write_text
from invincat_cli.unicode_security import (
    check_url_safety,
    detect_dangerous_unicode,
    format_warning_detail,
    iter_string_values,
    looks_like_url_key,
    summarize_issues,
)

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)

_HITL_REQUEST_ADAPTER = TypeAdapter(HITLRequest)

_STREAM_CHUNK_LENGTH = 3
"""Expected element counts for the tuples emitted by agent.astream.

Stream chunks are 3-tuples: (namespace, stream_mode, data).
"""

_MESSAGE_DATA_LENGTH = 2
"""Message-mode data is a 2-tuple: (message_obj, metadata)."""


def _process_interrupts(
    data: dict[str, list[Interrupt]],
    state: StreamState,
    console: Console,
) -> None:
    """Extract HITL interrupts from an `updates` chunk and record them.

    Args:
        data: The `updates` dict that contains an `__interrupt__` key.
        state: Stream state to update with new pending interrupts.
        console: Rich console for user-visible warnings.
    """
    interrupts = data["__interrupt__"]
    if interrupts:
        for interrupt_obj in interrupts:
            try:
                validated_request = _HITL_REQUEST_ADAPTER.validate_python(
                    interrupt_obj.value
                )
            except ValidationError:
                logger.warning(
                    "Rejecting malformed HITL interrupt %s (raw value: %r)",
                    interrupt_obj.id,
                    interrupt_obj.value,
                )
                console.print(
                    f"[yellow]Warning: Received malformed tool approval "
                    f"request (interrupt {interrupt_obj.id}). Rejecting.[/yellow]"
                )
                # Fail-closed: record a reject decision for malformed interrupts

                state.hitl_response[interrupt_obj.id] = {
                    "decisions": [{"type": "reject", "message": "Malformed interrupt"}]
                }
                continue
            state.pending_interrupts[interrupt_obj.id] = validated_request
            state.interrupt_occurred = True
            dispatch_hook_fire_and_forget("input.required", {})


def _process_ai_message(
    message_obj: AIMessage,
    state: StreamState,
    console: Console,
) -> None:
    """Extract text and tool-call blocks from an AI message and render them.

    When streaming is enabled, text blocks are written to stdout immediately;
    otherwise they are accumulated in `state.full_response` for deferred
    output. Tool-call blocks are buffered and their names are printed to the
    console.

    Args:
        message_obj: The `AIMessage` received from the stream.
        state: Stream state for accumulating response text and tool-call buffers.
        console: Rich console for formatted output.
    """
    # Extract token usage for stats accumulation
    usage = getattr(message_obj, "usage_metadata", None)
    if usage:
        input_toks = usage.get("input_tokens", 0)
        output_toks = usage.get("output_tokens", 0)
        total_toks = usage.get("total_tokens", 0)
        active_model = settings.model_name or ""
        if input_toks or output_toks:
            state.stats.record_request(active_model, input_toks, output_toks)
        elif total_toks:
            state.stats.record_request(active_model, total_toks, 0)

    if not hasattr(message_obj, "content_blocks"):
        logger.debug("AIMessage missing content_blocks attribute, skipping")
        return
    for block in message_obj.content_blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text", "")
            if text:
                if state.stream:
                    if state.spinner:
                        state.spinner.stop()
                    _write_text(text)
                state.full_response.append(text)
        elif block_type in {"tool_call_chunk", "tool_call"}:
            chunk_name = block.get("name")
            chunk_id = block.get("id")
            chunk_index = block.get("index")

            if chunk_index is not None:
                buffer_key: int | str = chunk_index
            elif chunk_id is not None:
                buffer_key = chunk_id
            else:
                buffer_key = f"unknown-{len(state.tool_call_buffers)}"

            if buffer_key not in state.tool_call_buffers:
                state.tool_call_buffers[buffer_key] = {"name": None, "id": None}
            if chunk_name:
                state.tool_call_buffers[buffer_key]["name"] = chunk_name
                if state.spinner:
                    state.spinner.stop()
                if state.full_response and not state.quiet:
                    _write_newline()
                console.print(
                    f"[dim]🔧 Calling tool: {escape_markup(chunk_name)}[/dim]",
                    highlight=False,
                )


def _process_message_chunk(
    data: tuple[AIMessage | ToolMessage, dict[str, str]],
    state: StreamState,
    console: Console,
    file_op_tracker: FileOpTracker,
) -> None:
    """Handle a `messages`-mode chunk from the stream.

    Dispatches to AI-message or tool-message processing depending on the
    message type.

    Args:
        data: A 2-tuple of `(message_obj, metadata)` from the messages
            stream mode.
        state: Shared stream state.
        console: Rich console for formatted output.
        file_op_tracker: Tracker for file-operation diffs.
    """
    if not isinstance(data, tuple) or len(data) != _MESSAGE_DATA_LENGTH:
        logger.debug(
            "Unexpected message-mode data (type=%s), skipping", type(data).__name__
        )
        return

    message_obj, metadata = data

    # Internal middleware model output is bookkeeping and should not be shown.
    if metadata and metadata.get("lc_source") in {"summarization", "memory_agent"}:
        return

    if isinstance(message_obj, AIMessage):
        _process_ai_message(message_obj, state, console)
    elif isinstance(message_obj, ToolMessage):
        record = file_op_tracker.complete_with_message(message_obj)
        if record and record.diff:
            if state.spinner:
                state.spinner.stop()
            console.print(
                f"[dim]📝 {escape_markup(record.display_path)}[/dim]",
                highlight=False,
            )
        if state.spinner:
            state.spinner.start()


def _process_stream_chunk(
    chunk: object,
    state: StreamState,
    console: Console,
    file_op_tracker: FileOpTracker,
) -> None:
    """Route a single raw stream chunk to the appropriate handler.

    Only main-agent chunks are processed; sub-agent output is ignored so
    that only top-level content is rendered.

    Args:
        chunk: A raw element yielded by `agent.astream`.

            Expected to be a 3-tuple `(namespace, stream_mode, data)` for
            main-agent output.
        state: Shared stream state.
        console: Rich console for formatted output.
        file_op_tracker: Tracker for file-operation diffs.
    """
    if not isinstance(chunk, tuple) or len(chunk) != _STREAM_CHUNK_LENGTH:
        logger.debug(
            "Unexpected stream chunk (type=%s), skipping", type(chunk).__name__
        )
        return

    namespace, stream_mode, data = chunk
    is_main_agent = not namespace

    if not is_main_agent:
        return

    if stream_mode == "updates" and isinstance(data, dict) and "__interrupt__" in data:
        _process_interrupts(cast("dict[str, list[Interrupt]]", data), state, console)
    elif stream_mode == "messages":
        _process_message_chunk(
            cast("tuple[AIMessage | ToolMessage, dict[str, str]]", data),
            state,
            console,
            file_op_tracker,
        )


def _make_hitl_decision(
    action_request: ActionRequest, console: Console
) -> dict[str, str]:
    """Decide whether to approve or reject a single action request.

    This function is only invoked when a restrictive shell allow-list is
    configured (not `all`). When shell is disabled or unrestricted,
    `interrupt_on` is empty and this function is bypassed entirely.

    Shell tools are always gated: if an allow-list is configured, the command
    is validated against it; if no allow-list is configured, shell commands
    are rejected outright (defense-in-depth — the caller should disable
    shell tools when no allow-list is present, but this function fails
    closed regardless). Non-shell tools are approved unconditionally.

    Args:
        action_request: The action-request dict emitted by the HITL middleware.

            Must contain at least a `name` key.
        console: Rich console for status output.

    Returns:
        Decision dict with a `type` key (`"approve"` or `"reject"`)
            and an optional `message` key with a human-readable explanation.
    """
    for warning in _collect_action_request_warnings(action_request):
        console.print(f"[yellow]Warning:[/yellow] {warning}")

    action_name = action_request.get("name", "")

    if action_name in SHELL_TOOL_NAMES:
        if not settings.shell_allow_list:
            command = action_request.get("args", {}).get("command", "")
            console.print(
                f"\n[red]Shell command rejected (no allow-list configured): "
                f"{command}[/red]"
            )
            return {
                "type": "reject",
                "message": (
                    "Shell commands are not permitted in non-interactive mode "
                    "without a --shell-allow-list. Use --shell-allow-list to "
                    "specify allowed commands."
                ),
            }

        command = action_request.get("args", {}).get("command", "")

        if is_shell_command_allowed(command, settings.shell_allow_list, cwd=Path.cwd()):
            console.print(f"[dim]✓ Auto-approved: {escape_markup(command)}[/dim]")
            return {"type": "approve"}

        allowed_list_str = ", ".join(settings.shell_allow_list)
        console.print(f"\n[red]Shell command rejected:[/red] {escape_markup(command)}")
        console.print(
            f"[yellow]Allowed commands:[/yellow] {escape_markup(allowed_list_str)}"
        )
        return {
            "type": "reject",
            "message": (
                f"Command '{command}' is not in the allow-list. "
                f"Allowed commands: {allowed_list_str}. "
                f"Please use allowed commands or try another approach."
            ),
        }

    console.print(f"[dim]✓ Auto-approved action: {escape_markup(action_name)}[/dim]")
    return {"type": "approve"}


def _collect_action_request_warnings(action_request: ActionRequest) -> list[str]:
    """Collect Unicode/URL safety warnings for one action request.

    Recursively inspects all nested string values in action arguments.

    Returns:
        Warning messages for suspicious values in action arguments.
    """
    warnings: list[str] = []
    args = action_request.get("args", {})
    if not isinstance(args, dict):
        return warnings

    tool_name = str(action_request.get("name", "unknown"))

    for arg_path, text in iter_string_values(args):
        issues = detect_dangerous_unicode(text)
        if issues:
            warnings.append(
                f"{tool_name}.{arg_path} contains hidden Unicode "
                f"({summarize_issues(issues)})"
            )

        if looks_like_url_key(arg_path):
            safety = check_url_safety(text)
            if safety.safe:
                continue
            detail = format_warning_detail(safety.warnings)
            if safety.decoded_domain:
                detail = f"{detail}; decoded host: {safety.decoded_domain}"
            warnings.append(f"{tool_name}.{arg_path} URL warning: {detail}")

    return warnings


def _process_hitl_interrupts(state: StreamState, console: Console) -> None:
    """Iterate over pending HITL interrupts and build approval/rejection responses.

    After processing, `state.pending_interrupts` is cleared and decisions
    are written into `state.hitl_response` so the agent can be resumed.

    Args:
        state: Stream state containing the pending interrupts to process.
        console: Rich console for status output.
    """
    current_interrupts = dict(state.pending_interrupts)
    state.pending_interrupts.clear()

    for interrupt_id, hitl_request in current_interrupts.items():
        decisions = [
            _make_hitl_decision(action_request, console)
            for action_request in hitl_request["action_requests"]
        ]
        state.hitl_response[interrupt_id] = {"decisions": decisions}


async def _stream_agent(
    agent: Any,  # noqa: ANN401
    stream_input: dict[str, Any] | Command,
    config: RunnableConfig,
    state: StreamState,
    console: Console,
    file_op_tracker: FileOpTracker,
) -> None:
    """Consume the full agent stream and update *state* with results.

    Args:
        agent: The agent (Pregel or RemoteAgent).
        stream_input: Either the initial user message dict or a
            `Command(resume=...)` for HITL continuation.
        config: LangGraph runnable config (thread ID, metadata, etc.).
        state: Shared stream state.
        console: Rich console for formatted output.
        file_op_tracker: Tracker for file-operation diffs.
    """
    if state.spinner:
        state.spinner.start()
    try:
        async for chunk in agent.astream(
            stream_input,
            stream_mode=["messages", "updates"],
            subgraphs=True,
            config=config,
            durability="exit",
        ):
            _process_stream_chunk(chunk, state, console, file_op_tracker)
    finally:
        if state.spinner:
            state.spinner.stop()
