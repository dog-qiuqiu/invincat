"""Main streamed execution loop for the Textual UI adapter."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from invincat_cli import textual_adapter as adapter_mod
from invincat_cli.config import build_stream_config
from invincat_cli.core.ask_user_types import AskUserRequest
from invincat_cli.core.cli_context import CLIContext
from invincat_cli.core.session_stats import SessionStats
from invincat_cli.i18n import t
from invincat_cli.io.input import MediaTracker
from invincat_cli.middleware.plan_agent import PLANNER_ALLOWED_TOOLS
from invincat_cli.textual_adapter.input import build_message_content
from invincat_cli.textual_adapter.ui_adapter import TextualUIAdapter
from invincat_cli.textual_adapter.utils import (
    is_transient_stream_error as _is_transient_stream_error,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_PLANNER_ALLOWED_TOOL_SET: frozenset[str] = frozenset(PLANNER_ALLOWED_TOOLS)

async def execute_task_textual(
    user_input: str,
    agent: Any,  # noqa: ANN401  # Dynamic agent graph type
    assistant_id: str | None,
    session_state: Any,  # noqa: ANN401  # Dynamic session state type
    adapter: TextualUIAdapter,
    backend: Any = None,  # noqa: ANN401  # Dynamic backend type
    image_tracker: MediaTracker | None = None,
    context: CLIContext | None = None,
    *,
    sandbox_type: str | None = None,
    is_planner_turn: bool = False,
    message_kwargs: dict[str, Any] | None = None,
    turn_stats: SessionStats | None = None,
    on_text_delta: Callable[[str, str], Awaitable[None]] | None = None,
    on_wecom_file_request: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_schedule_payload: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> SessionStats:
    """Execute a task with output directed to Textual UI.

    This is the Textual-compatible version of execute_task() that uses
    the TextualUIAdapter for all UI operations.

    Args:
        user_input: The user's input message
        agent: The LangGraph agent to execute
        assistant_id: The agent identifier
        session_state: Session state with auto_approve flag
        adapter: The TextualUIAdapter for UI operations
        backend: Optional backend for file operations
        image_tracker: Optional tracker for images
        context: Optional `CLIContext` with model override and params, passed
            to the graph via `context=`.
        sandbox_type: Sandbox provider name for trace metadata, or `None`
            if no sandbox is active.
        is_planner_turn: Whether this streamed turn is running on the planner
            peer-agent in `/plan` mode.
        message_kwargs: Extra fields merged into the stream input message
            dict (e.g., `additional_kwargs` for persisting skill metadata
            in the checkpoint).
        turn_stats: Pre-created `SessionStats` to accumulate into.

            When the caller holds a reference to the same object, stats are
            available even if this coroutine is cancelled before it can return.

            If `None`, a new instance is created internally.
        on_text_delta: Optional callback invoked for each real assistant text
            chunk with `(delta_text, accumulated_text)`. Used by external
            transports that need model-provider streaming rather than polling
            the rendered message store.
        on_wecom_file_request: Optional callback invoked when the WeCom-only
            `send_wecom_file` tool requests sending a file through the active
            WeCom bridge.
        on_schedule_payload: Optional callback invoked when a schedule management
            tool (create/update/cancel/run_now) produces a structured payload.

    Returns:
        Stats accumulated over this turn (request count, token counts,
            wall-clock time).

    Raises:
        ValidationError: If HITL request validation fails (re-raised).
    """
    from langchain.agents.middleware.human_in_the_loop import (
        ApproveDecision,
        HITLRequest,
        RejectDecision,
    )
    from langgraph.types import Command

    hitl_request_adapter = adapter_mod._get_hitl_request_adapter(HITLRequest)
    ask_user_adapter = adapter_mod._get_ask_user_adapter()

    message_content = await build_message_content(
        user_input,
        image_tracker,
        parse_file_mentions_func=adapter_mod.parse_file_mentions,
        read_mentioned_file_func=adapter_mod._read_mentioned_file,
        create_multimodal_content_func=adapter_mod.create_multimodal_content,
    )

    thread_id = session_state.thread_id
    planner_mode_enforced = bool(is_planner_turn and session_state.plan_mode)
    config = build_stream_config(thread_id, assistant_id, sandbox_type=sandbox_type)

    await adapter_mod.dispatch_hook("session.start", {"thread_id": thread_id})

    captured_input_tokens = 0
    captured_output_tokens = 0
    if turn_stats is None:
        turn_stats = SessionStats()
    start_time = time.monotonic()

    # Warn if token display callbacks are only partially wired — all three
    # should be set together to avoid inconsistent status-bar behavior.
    token_cbs = (
        adapter._on_tokens_update,
        adapter._on_tokens_hide,
        adapter._on_tokens_show,
    )
    if any(token_cbs) and not all(token_cbs):
        logger.warning(
            "Token callbacks partially wired (update=%s, hide=%s, show=%s); "
            "token display may behave inconsistently",
            adapter._on_tokens_update is not None,
            adapter._on_tokens_hide is not None,
            adapter._on_tokens_show is not None,
        )

    # Show spinner
    if adapter._set_spinner:
        await adapter._set_spinner(t("status.thinking"))

    # Hide token display during streaming (will be shown with accurate count at end)
    if adapter._on_tokens_hide:
        adapter._on_tokens_hide()

    file_op_tracker = adapter_mod.FileOpTracker(assistant_id=assistant_id, backend=backend)
    displayed_tool_ids: set[str] = set()
    processed_wecom_file_tool_ids: set[str] = set()
    tool_call_buffers: dict[str | int, dict] = {}

    # Clear any zombie tool widgets left over from previous turns.
    # _current_tool_messages is an instance variable that persists across turns.
    # In normal operation each widget is popped when its ToolMessage arrives, but
    # if a turn created duplicate widgets (index-based key + real-UUID key) only
    # the UUID-keyed widget is popped, leaving the index-keyed one as a zombie.
    # Those zombies interfere with Strategy 3 (name-match fallback) in future turns,
    # potentially stealing results from the correct current widget.
    adapter._current_tool_messages.clear()
    adapter._subagent_activity.clear()

    # Track pending text and assistant messages PER NAMESPACE to avoid interleaving
    # when multiple subagents stream in parallel
    pending_text_by_namespace: dict[tuple, str] = {}
    assistant_message_by_namespace: dict[tuple, Any] = {}

    user_msg: dict[str, Any] = {"role": "user", "content": message_content}
    if message_kwargs:
        user_msg.update(message_kwargs)
    stream_input: dict | Command = {"messages": [user_msg]}

    # Track internal middleware lifecycle so spinner status stays in sync.
    summarization_in_progress = False
    memory_update_in_progress = False

    _MAX_RESUME_ITERATIONS = 50
    _resume_iteration = 0

    try:
        while True:
            _resume_iteration += 1
            if _resume_iteration > _MAX_RESUME_ITERATIONS:
                logger.error(
                    "HITL resume loop exceeded %d iterations — breaking to prevent "
                    "infinite loop. This likely indicates a bug in the agent or "
                    "interrupt handler.",
                    _MAX_RESUME_ITERATIONS,
                )
                await adapter._mount_message(
                    adapter_mod.AppMessage(
                        "Too many consecutive interrupts — breaking loop. "
                        "Please start a new message."
                    )
                )
                break

            interrupt_occurred = False
            suppress_resumed_output = False
            pending_interrupts: dict[str, HITLRequest] = {}
            pending_ask_user: dict[str, AskUserRequest] = {}
            pending_approve_plan: dict[str, Any] = {}
            error_ask_user_ids: dict[str, str] = {}

            _astream_attempt = 0
            while True:  # stream-retry loop; only retries on transient errors with zero chunks received
                _astream_chunks = 0
                _astream_exc: BaseException | None = None
                try:
                    async for chunk in agent.astream(
                        stream_input,
                        stream_mode=["messages", "updates", "custom"],
                        subgraphs=True,
                        config=config,
                        context=context,
                        durability="exit",
                    ):
                        _astream_chunks += 1
                        if not isinstance(chunk, tuple) or len(chunk) != 3:  # noqa: PLR2004  # stream chunk is a 3-tuple (namespace, mode, data)
                            logger.debug(
                                "Skipping non-3-tuple chunk: %s", type(chunk).__name__
                            )
                            continue

                        namespace, current_stream_mode, data = chunk

                        # Convert namespace to hashable tuple for dict keys
                        ns_key = tuple(namespace) if namespace else ()

                        # Filter out subagent outputs - only show main agent (empty
                        # namespace). Subagents run via Task tool and should only
                        # report back to the main agent
                        is_main_agent = ns_key == ()
                        if not is_main_agent:
                            adapter._subagent_activity.observe_chunk(
                                ns_key=ns_key,
                                stream_mode=current_stream_mode,
                                data=data,
                            )

                        # Handle CUSTOM stream - middleware lifecycle events
                        if current_stream_mode == "custom":
                            if (
                                isinstance(data, dict)
                                and data.get("event") == "memory_agent"
                            ):
                                status = data.get("status")
                                if status == "running":
                                    if not memory_update_in_progress:
                                        memory_update_in_progress = True
                                        if adapter._set_spinner:
                                            await adapter._set_spinner(
                                                t("status.memory_updating")
                                            )
                                elif status == "done":
                                    if memory_update_in_progress:
                                        memory_update_in_progress = False
                                        if (
                                            adapter._set_spinner
                                            and not adapter._current_tool_messages
                                        ):
                                            await adapter._set_spinner(
                                                t("status.thinking")
                                            )
                            continue

                        # Handle UPDATES stream - for interrupts and todos
                        if current_stream_mode == "updates":
                            from invincat_cli.textual_adapter.update_stream import (
                                handle_update_stream_chunk,
                            )

                            (
                                should_abort_turn,
                                interrupt_occurred,
                            ) = await handle_update_stream_chunk(
                                adapter=adapter,
                                data=data,
                                interrupt_occurred=interrupt_occurred,
                                hitl_request_adapter=hitl_request_adapter,
                                ask_user_adapter=ask_user_adapter,
                                pending_interrupts=pending_interrupts,
                                pending_ask_user=pending_ask_user,
                                pending_approve_plan=pending_approve_plan,
                                error_ask_user_ids=error_ask_user_ids,
                            )
                            if should_abort_turn:
                                return turn_stats

                        # Handle MESSAGES stream - for content and tool calls
                        elif current_stream_mode == "messages":
                            from invincat_cli.textual_adapter.message_stream import (
                                handle_message_stream_chunk,
                            )

                            (
                                captured_input_tokens,
                                summarization_in_progress,
                                memory_update_in_progress,
                            ) = await handle_message_stream_chunk(
                                adapter=adapter,
                                data=data,
                                ns_key=ns_key,
                                is_main_agent=is_main_agent,
                                file_op_tracker=file_op_tracker,
                                processed_wecom_file_tool_ids=processed_wecom_file_tool_ids,
                                pending_text_by_namespace=pending_text_by_namespace,
                                assistant_message_by_namespace=assistant_message_by_namespace,
                                displayed_tool_ids=displayed_tool_ids,
                                tool_call_buffers=tool_call_buffers,
                                planner_mode_enforced=planner_mode_enforced,
                                turn_stats=turn_stats,
                                captured_input_tokens=captured_input_tokens,
                                summarization_in_progress=summarization_in_progress,
                                memory_update_in_progress=memory_update_in_progress,
                                on_text_delta=on_text_delta,
                                on_wecom_file_request=on_wecom_file_request,
                                on_schedule_payload=on_schedule_payload,
                            )
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception as _exc:
                    _astream_exc = _exc

                if _astream_exc is None:
                    break  # stream completed successfully — exit retry loop

                _astream_attempt += 1
                if (
                    _astream_chunks > 0
                    or _astream_attempt > 3
                    or not _is_transient_stream_error(_astream_exc)
                ):
                    raise _astream_exc

                _retry_delay = 2.0 * (2 ** (_astream_attempt - 1))  # 2s, 4s, 8s
                logger.warning(
                    "astream transient error (attempt %d/4); retrying in %.1fs: %s",
                    _astream_attempt,
                    _retry_delay,
                    _astream_exc,
                )
                await adapter._mount_message(
                    adapter_mod.AppMessage(
                        f"Connection error \u2014 retrying ({_astream_attempt}/3)\u2026"
                    )
                )
                await asyncio.sleep(_retry_delay)

            # Reset summarization state if stream ended mid-summarization
            # (e.g. middleware error, stream exhausted before regular chunks).
            if summarization_in_progress:
                summarization_in_progress = False
                try:
                    await adapter._mount_message(adapter_mod.SummarizationMessage())
                except Exception:
                    logger.debug(
                        "Failed to mount summarization notification",
                        exc_info=True,
                    )
                if adapter._set_spinner and not adapter._current_tool_messages:
                    await adapter._set_spinner(t("status.thinking"))

            # Reset memory status if stream ended while memory middleware was running.
            if memory_update_in_progress:
                memory_update_in_progress = False
                if adapter._set_spinner and not adapter._current_tool_messages:
                    await adapter._set_spinner(t("status.thinking"))

            # Flush any remaining text from all namespaces
            for ns_key, pending_text in list(pending_text_by_namespace.items()):
                if pending_text:
                    await adapter_mod._flush_assistant_text_ns(
                        adapter, pending_text, ns_key, assistant_message_by_namespace
                    )
            pending_text_by_namespace.clear()
            assistant_message_by_namespace.clear()

            # Unconditionally clear the active message after the stream loop exits.
            # _flush_assistant_text_ns clears it per-namespace, but if a turn
            # produces only tool calls (no assistant text), that function is never
            # called and _active_message_id stays set from the previous turn,
            # causing get_messages_to_prune() to break early on the stale active
            # message and leave the DOM window unbounded.
            if adapter._set_active_message:
                adapter._set_active_message(None)

            # Handle HITL after stream completes
            if interrupt_occurred:
                from invincat_cli.textual_adapter.interrupt_flow import (
                    build_interrupt_resume_payload,
                )

                (
                    resume_payload,
                    any_rejected,
                    suppress_resumed_output,
                ) = await build_interrupt_resume_payload(
                    adapter=adapter,
                    session_state=session_state,
                    assistant_id=assistant_id,
                    file_op_tracker=file_op_tracker,
                    pending_ask_user=pending_ask_user,
                    pending_approve_plan=pending_approve_plan,
                    pending_interrupts=pending_interrupts,
                    error_ask_user_ids=error_ask_user_ids,
                    approve_decision_cls=ApproveDecision,
                    reject_decision_cls=RejectDecision,
                )
            if interrupt_occurred and resume_payload:
                if suppress_resumed_output and not pending_ask_user:
                    await adapter._mount_message(
                        adapter_mod.AppMessage(
                            "Command rejected. Tell the agent what you'd like instead."
                        )
                    )
                    turn_stats.wall_time_seconds = time.monotonic() - start_time
                    return turn_stats

                stream_input = Command(resume=resume_payload)
            else:
                await adapter_mod.dispatch_hook("task.complete", {"thread_id": thread_id})
                break

    except (asyncio.CancelledError, KeyboardInterrupt):
        # Use shield to protect cleanup from being cancelled.
        # Without this, if the user presses ESC again quickly or sends a new
        # message during cleanup, the cleanup await itself gets cancelled,
        # leaving state inconsistent (e.g., _agent_running still True).
        try:
            await asyncio.shield(
                adapter_mod._handle_interrupt_cleanup(
                    adapter=adapter,
                    agent=agent,
                    config=config,
                    pending_text_by_namespace=pending_text_by_namespace,
                    captured_input_tokens=captured_input_tokens,
                    captured_output_tokens=captured_output_tokens,
                    turn_stats=turn_stats,
                    start_time=start_time,
                )
            )
        except asyncio.CancelledError:
            # Shield protects the cleanup, but if we're cancelled again,
            # we still need to ensure cleanup completes. Log and continue.
            logger.debug("Interrupt cleanup shielded from cancellation")
        return turn_stats

    except Exception:
        # Unexpected exception (e.g. network error, API error): clear transient
        # UI state so the app remains usable for the next message.
        # Without this, _active_message_id stays set from the failed turn,
        # causing get_messages_to_prune() to stop at the stale active message
        # and leave the DOM window unbounded.
        logger.exception("Unexpected error in execute_task_textual")
        if adapter._set_active_message:
            adapter._set_active_message(None)
        if adapter._set_spinner:
            await adapter._set_spinner(None)
        # Mark any in-flight tool widgets as error so the UI isn't stuck
        # showing them as "pending" forever.
        for tool_msg in list(adapter._current_tool_messages.values()):
            try:
                tool_msg.set_error(t("tool.interrupted_by_error"))
            except Exception:  # noqa: BLE001
                pass
        adapter._current_tool_messages.clear()
        raise

    # Update token count and return stats
    turn_stats.wall_time_seconds = time.monotonic() - start_time
    await adapter_mod._report_and_persist_tokens(
        adapter,
        agent,
        config,
        captured_input_tokens,
        captured_output_tokens,
    )
    return turn_stats
