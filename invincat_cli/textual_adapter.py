"""Textual UI adapter for agent execution."""
# This module has complex streaming logic ported from execution.py

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path
    from typing import Protocol

    from langchain.agents.middleware.human_in_the_loop import (
        ApproveDecision,
        EditDecision,
        HITLRequest,
        RejectDecision,
    )
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableConfig
    from langgraph.types import Command, Interrupt
    from pydantic import TypeAdapter
    from rich.console import Console

    from invincat_cli._ask_user_types import AskUserWidgetResult, Question

    # Type alias matching HITLResponse["decisions"] element type
    HITLDecision = ApproveDecision | EditDecision | RejectDecision

    class _TokensUpdateCallback(Protocol):
        """Callback signature for `_on_tokens_update`."""

        def __call__(self, count: int, *, approximate: bool = False) -> None: ...

    class _TokensShowCallback(Protocol):
        """Callback signature for `_on_tokens_show`."""

        def __call__(self, *, approximate: bool = False) -> None: ...


from invincat_cli._ask_user_types import AskUserRequest
from invincat_cli._cli_context import CLIContext  # noqa: TC001
from invincat_cli._debug import configure_debug_logging
from invincat_cli._session_stats import (
    ModelStats as ModelStats,
    SessionStats as SessionStats,
    SpinnerStatus as SpinnerStatus,
    format_token_count as format_token_count,
)
from invincat_cli.config import build_stream_config
from invincat_cli.file_ops import FileOpTracker
from invincat_cli.formatting import format_duration
from invincat_cli.hooks import dispatch_hook
from invincat_cli.input import MediaTracker, parse_file_mentions
from invincat_cli.media_utils import create_multimodal_content
from invincat_cli.tool_display import format_tool_message_content
from invincat_cli.widgets.messages import (
    AppMessage,
    AssistantMessage,
    DiffMessage,
    SummarizationMessage,
    ToolCallMessage,
)

logger = logging.getLogger(__name__)
configure_debug_logging(logger)

_hitl_adapter_cache: TypeAdapter | None = None
"""Lazy singleton for the HITL request validator."""


def _get_hitl_request_adapter(hitl_request_type: type) -> TypeAdapter:
    """Return a cached `TypeAdapter(HITLRequest)`.

    Avoids re-compiling the pydantic schema on every `execute_task_textual` call.

    Args:
        hitl_request_type: The `HITLRequest` class (passed in because
            it is imported locally by the caller).

    Returns:
        Shared `TypeAdapter` instance.
    """
    global _hitl_adapter_cache  # noqa: PLW0603
    if _hitl_adapter_cache is None:
        from pydantic import TypeAdapter

        _hitl_adapter_cache = TypeAdapter(hitl_request_type)
    return _hitl_adapter_cache


def print_usage_table(
    stats: SessionStats,
    wall_time: float,
    console: Console,
) -> None:
    """Print a model-usage stats table to a Rich console.

    When the session spans multiple models each gets its own row with a
    totals row appended; single-model sessions show one row.

    Args:
        stats: Cumulative session stats.
        wall_time: Total wall-clock time in seconds.
        console: Rich console for output.
    """
    from rich.table import Table

    has_time = wall_time >= 0.1  # noqa: PLR2004
    if not (stats.request_count or stats.input_tokens or has_time):
        return

    if stats.per_model:
        multi_model = len(stats.per_model) > 1

        table = Table(
            show_header=True,
            header_style="bold",
            box=None,
            padding=(0, 2, 0, 0),
            show_edge=False,
        )
        table.add_column("Model", style="dim")
        table.add_column("Reqs", justify="right", style="dim")
        table.add_column("InputTok", justify="right", style="dim")
        table.add_column("OutputTok", justify="right", style="dim")

        if multi_model:
            for model_name, ms in stats.per_model.items():
                table.add_row(
                    model_name,
                    str(ms.request_count),
                    format_token_count(ms.input_tokens),
                    format_token_count(ms.output_tokens),
                )
            table.add_row(
                "Total",
                str(stats.request_count),
                format_token_count(stats.input_tokens),
                format_token_count(stats.output_tokens),
            )
        else:
            model_label = next(iter(stats.per_model))
            table.add_row(
                model_label,
                str(stats.request_count),
                format_token_count(stats.input_tokens),
                format_token_count(stats.output_tokens),
            )

        console.print()
        console.print("[bold]Usage Stats[/bold]")
        console.print(table)
    if has_time:
        console.print()
        console.print(
            f"Agent active  {format_duration(wall_time)}",
            style="dim",
            highlight=False,
        )


_ask_user_adapter_cache: TypeAdapter | None = None
"""Lazy singleton for the `ask_user` interrupt validator."""


def _get_ask_user_adapter() -> TypeAdapter:
    """Return a cached `TypeAdapter(AskUserRequest)`.

    Returns:
        Shared `TypeAdapter` instance.
    """
    global _ask_user_adapter_cache  # noqa: PLW0603
    if _ask_user_adapter_cache is None:
        from pydantic import TypeAdapter

        _ask_user_adapter_cache = TypeAdapter(AskUserRequest)
    return _ask_user_adapter_cache


def _is_summarization_chunk(metadata: dict | None) -> bool:
    """Check if a message chunk is from summarization middleware.

    The summarization model is invoked with
    `config={"metadata": {"lc_source": "summarization"}}`
    (see `langchain.agents.middleware.summarization`), which
    LangChain's callback system merges into the stream metadata dict.

    Args:
        metadata: The metadata dict from the stream chunk.

    Returns:
        Whether the chunk is from summarization and should be filtered.
    """
    if metadata is None:
        return False
    return metadata.get("lc_source") == "summarization"


def _normalize_tool_id(tool_id: Any) -> str | None:
    """Normalize a tool call ID to a string for consistent comparison.

    Tool call IDs may arrive as strings or integers depending on the model
    and streaming chunk order. Normalizing to string ensures dict lookups
    and equality checks work correctly regardless of source type.

    Args:
        tool_id: Raw tool call ID (str, int, or None).

    Returns:
        String representation, or None if tool_id is None.
    """
    if tool_id is None:
        return None
    return str(tool_id)


class TextualUIAdapter:
    """Adapter for rendering agent output to Textual widgets.

    This adapter provides an abstraction layer between the agent execution and the
    Textual UI, allowing streaming output to be rendered as widgets.
    """

    def __init__(
        self,
        mount_message: Callable[..., Awaitable[None]],
        update_status: Callable[[str], None],
        request_approval: Callable[..., Awaitable[Any]],
        on_auto_approve_enabled: Callable[[], None] | None = None,
        set_spinner: Callable[[SpinnerStatus], Awaitable[None]] | None = None,
        set_active_message: Callable[[str | None], None] | None = None,
        sync_message_content: Callable[[str, str], None] | None = None,
        request_ask_user: (
            Callable[
                [list[Question]],
                Awaitable[asyncio.Future[AskUserWidgetResult] | None],
            ]
            | None
        ) = None,
    ) -> None:
        """Initialize the adapter."""
        self._mount_message = mount_message
        """Async callback to mount a message widget to the chat."""

        self._update_status = update_status
        """Callback to update the status bar text."""

        self._request_approval = request_approval
        """Async callback that returns a Future for HITL approval."""

        self._on_auto_approve_enabled = on_auto_approve_enabled
        """Callback invoked when auto-approve is enabled via the HITL approval
        menu.

        Fired when the user selects "Auto-approve all" from an approval dialog,
        allowing the app to sync its status bar and session state.
        """

        self._set_spinner = set_spinner
        """Callback to show/hide loading spinner."""

        self._set_active_message = set_active_message
        """Callback to set the active streaming message ID (pass `None` to clear)."""

        self._sync_message_content = sync_message_content
        """Callback to sync final message content back to the store after streaming."""

        self._request_ask_user = request_ask_user
        """Async callback for `ask_user` interrupts.

        When awaited, returns a `Future` that resolves to user answers.
        """

        # State tracking
        # FIX: keys are always normalized str via _normalize_tool_id to avoid
        # int/str mismatches when chunk ordering delivers id after name.
        self._current_tool_messages: dict[str, ToolCallMessage] = {}
        """Map of tool call IDs (normalized str) to their message widgets."""

        # Token display callbacks (set by the app after construction)
        self._on_tokens_update: _TokensUpdateCallback | None = None
        """Called with total context tokens after each LLM response."""

        self._on_tokens_hide: Callable[[], None] | None = None
        """Called to hide the token display during streaming."""

        self._on_tokens_show: _TokensShowCallback | None = None
        """Called to restore the token display with the cached value."""

        self._message_store: Any = None
        """Reference to MessageStore for updating tool messages after pruning."""

    def set_message_store(self, message_store: Any) -> None:
        """Set the message store reference.

        Args:
            message_store: The MessageStore instance from the app.
        """
        self._message_store = message_store

    def _update_tool_message_in_store(
        self, tool_call_id: str | int, status: str, output: str
    ) -> bool:
        """Update tool message data in the store when widget is pruned.

        Args:
            tool_call_id: The tool call ID to find (str or int, normalized internally).
            status: The tool status (success/error).
            output: The tool output.

        Returns:
            True if the message was found and updated.
        """
        if self._message_store is None:
            return False

        from invincat_cli.widgets.message_store import ToolStatus

        # MessageStore.get_message_by_tool_call_id normalizes to str internally,
        # so a single call handles both int and str IDs.
        msg_data = self._message_store.get_message_by_tool_call_id(tool_call_id)
        if msg_data is None:
            return False

        try:
            tool_status = ToolStatus(status)
        except ValueError:
            tool_status = None

        if tool_status:
            self._message_store.update_message(
                msg_data.id, tool_status=tool_status, tool_output=output
            )
            return True
        return False

    def finalize_pending_tools_with_error(self, error: str) -> None:
        """Mark all pending/running tool widgets as error and clear tracking.

        This is used as a safety net when an unexpected exception aborts
        streaming before matching `ToolMessage` results are received.

        Args:
            error: Error text to display in each pending tool widget.
        """
        for tool_msg in list(self._current_tool_messages.values()):
            tool_msg.set_error(error)
        self._current_tool_messages.clear()

        # Clear active streaming message to avoid stale "active" state in the store.
        if self._set_active_message:
            self._set_active_message(None)


def _build_interrupted_ai_message(
    pending_text_by_namespace: dict[tuple, str],
    current_tool_messages: dict[str, Any],
) -> AIMessage | None:
    """Build an AIMessage capturing interrupted state (text + tool calls).

    Args:
        pending_text_by_namespace: Dict of accumulated text by namespace
        current_tool_messages: Dict of tool_id -> ToolCallMessage widget

    Returns:
        AIMessage with accumulated content and tool calls, or None if empty.
    """
    from langchain_core.messages import AIMessage

    main_ns_key = ()
    accumulated_text = pending_text_by_namespace.get(main_ns_key, "").strip()

    # Reconstruct tool_calls from displayed tool messages
    tool_calls = []
    for tool_id, tool_widget in list(current_tool_messages.items()):
        tool_calls.append(
            {
                "id": tool_id,
                "name": tool_widget._tool_name,
                "args": tool_widget._args,
            }
        )

    if not accumulated_text and not tool_calls:
        return None

    return AIMessage(
        content=accumulated_text,
        tool_calls=tool_calls or [],
    )


def _read_mentioned_file(file_path: Path, max_embed_bytes: int) -> str:
    """Read a mentioned file for inline embedding (sync, for use with to_thread).

    Args:
        file_path: Resolved path to the file.
        max_embed_bytes: Size threshold; larger files get a reference only.

    Returns:
        Markdown snippet with the file content or a size-exceeded reference.
    """
    file_size = file_path.stat().st_size
    if file_size > max_embed_bytes:
        size_kb = file_size // 1024
        return (
            f"\n### {file_path.name}\n"
            f"Path: `{file_path}`\n"
            f"Size: {size_kb}KB (too large to embed, "
            "use read_file tool to view)"
        )
    content = file_path.read_text(encoding="utf-8")
    return f"\n### {file_path.name}\nPath: `{file_path}`\n```\n{content}\n```"


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
    message_kwargs: dict[str, Any] | None = None,
    turn_stats: SessionStats | None = None,
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
        message_kwargs: Extra fields merged into the stream input message
            dict (e.g., `additional_kwargs` for persisting skill metadata
            in the checkpoint).
        turn_stats: Pre-created `SessionStats` to accumulate into.

            When the caller holds a reference to the same object, stats are
            available even if this coroutine is cancelled before it can return.

            If `None`, a new instance is created internally.

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
    from langchain_core.messages import HumanMessage, ToolMessage
    from langgraph.types import Command
    from pydantic import ValidationError

    hitl_request_adapter = _get_hitl_request_adapter(HITLRequest)
    ask_user_adapter = _get_ask_user_adapter()

    # Parse file mentions and inject content if any — offload blocking I/O
    prompt_text, mentioned_files = await asyncio.to_thread(
        parse_file_mentions, user_input
    )

    # Max file size to embed inline (256KB, matching mistral-vibe)
    # Larger files get a reference instead - use read_file tool to view them
    max_embed_bytes = 256 * 1024

    if mentioned_files:
        context_parts = [prompt_text, "\n\n## Referenced Files\n"]
        for file_path in mentioned_files:
            try:
                part = await asyncio.to_thread(
                    _read_mentioned_file, file_path, max_embed_bytes
                )
                context_parts.append(part)
            except Exception as e:  # noqa: BLE001  # Resilient adapter error handling
                context_parts.append(
                    f"\n### {file_path.name}\n[Error reading file: {e}]"
                )
        final_input = "\n".join(context_parts)
    else:
        final_input = prompt_text

    # Include images and videos in the message content
    images_to_send = []
    videos_to_send = []
    if image_tracker:
        images_to_send = image_tracker.get_images()
        videos_to_send = image_tracker.get_videos()
    if images_to_send or videos_to_send:
        message_content = create_multimodal_content(
            final_input, images_to_send, videos_to_send
        )
    else:
        message_content = final_input

    thread_id = session_state.thread_id
    config = build_stream_config(thread_id, assistant_id, sandbox_type=sandbox_type)

    await dispatch_hook("session.start", {"thread_id": thread_id})

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
        await adapter._set_spinner("Thinking")

    # Hide token display during streaming (will be shown with accurate count at end)
    if adapter._on_tokens_hide:
        adapter._on_tokens_hide()

    file_op_tracker = FileOpTracker(assistant_id=assistant_id, backend=backend)
    displayed_tool_ids: set[str] = set()
    tool_call_buffers: dict[str | int, dict] = {}

    # Clear any zombie tool widgets left over from previous turns.
    # _current_tool_messages is an instance variable that persists across turns.
    # In normal operation each widget is popped when its ToolMessage arrives, but
    # if a turn created duplicate widgets (index-based key + real-UUID key) only
    # the UUID-keyed widget is popped, leaving the index-keyed one as a zombie.
    # Those zombies interfere with Strategy 3 (name-match fallback) in future turns,
    # potentially stealing results from the correct current widget.
    adapter._current_tool_messages.clear()

    # Track pending text and assistant messages PER NAMESPACE to avoid interleaving
    # when multiple subagents stream in parallel
    pending_text_by_namespace: dict[tuple, str] = {}
    assistant_message_by_namespace: dict[tuple, Any] = {}

    # Clear media from tracker after creating the message
    if image_tracker:
        image_tracker.clear()

    user_msg: dict[str, Any] = {"role": "user", "content": message_content}
    if message_kwargs:
        user_msg.update(message_kwargs)
    stream_input: dict | Command = {"messages": [user_msg]}

    # Track summarization lifecycle so spinner status and notification stay in sync.
    summarization_in_progress = False

    try:
        while True:
            interrupt_occurred = False
            suppress_resumed_output = False
            pending_interrupts: dict[str, HITLRequest] = {}
            pending_ask_user: dict[str, AskUserRequest] = {}
            error_ask_user_ids: dict[str, str] = {}

            async for chunk in agent.astream(
                stream_input,
                stream_mode=["messages", "updates"],
                subgraphs=True,
                config=config,
                context=context,
                durability="exit",
            ):
                if not isinstance(chunk, tuple) or len(chunk) != 3:  # noqa: PLR2004  # stream chunk is a 3-tuple (namespace, mode, data)
                    logger.debug("Skipping non-3-tuple chunk: %s", type(chunk).__name__)
                    continue

                namespace, current_stream_mode, data = chunk

                # Convert namespace to hashable tuple for dict keys
                ns_key = tuple(namespace) if namespace else ()

                # Filter out subagent outputs - only show main agent (empty
                # namespace). Subagents run via Task tool and should only
                # report back to the main agent
                is_main_agent = ns_key == ()

                # Handle UPDATES stream - for interrupts and todos
                if current_stream_mode == "updates":
                    if not isinstance(data, dict):
                        continue

                    # Check for interrupts
                    if "__interrupt__" in data:
                        interrupts: list[Interrupt] = data["__interrupt__"]
                        if interrupts:
                            for interrupt_obj in interrupts:
                                iv = interrupt_obj.value
                                if (
                                    isinstance(iv, dict)
                                    and iv.get("type") == "ask_user"
                                ):
                                    try:
                                        validated_ask_user = (
                                            ask_user_adapter.validate_python(iv)
                                        )
                                        pending_ask_user[interrupt_obj.id] = (
                                            validated_ask_user
                                        )
                                        interrupt_occurred = True
                                        await dispatch_hook("input.required", {})
                                    except ValidationError:
                                        logger.exception(
                                            "Invalid ask_user interrupt payload; "
                                            "resuming with error so the agent can recover"
                                        )
                                        error_ask_user_ids[interrupt_obj.id] = (
                                            "invalid ask_user payload"
                                        )
                                        interrupt_occurred = True
                                else:
                                    try:
                                        validated_request = (
                                            hitl_request_adapter.validate_python(iv)
                                        )
                                        pending_interrupts[interrupt_obj.id] = (
                                            validated_request
                                        )
                                        interrupt_occurred = True
                                        await dispatch_hook("input.required", {})
                                    except ValidationError:
                                        logger.exception(
                                            "Invalid HITL interrupt payload; "
                                            "aborting turn cleanly"
                                        )
                                        if adapter._set_spinner:
                                            await adapter._set_spinner(None)
                                        await adapter._mount_message(
                                            AppMessage(
                                                "Internal error: could not parse tool approval request. "
                                                "Please try again."
                                            )
                                        )
                                        return turn_stats

                    # Check for todo updates (not yet implemented in Textual UI)
                    chunk_data = next(iter(data.values())) if data else None
                    if (
                        chunk_data
                        and isinstance(chunk_data, dict)
                        and "todos" in chunk_data
                    ):
                        pass  # Future: render todo list widget

                # Handle MESSAGES stream - for content and tool calls
                elif current_stream_mode == "messages":
                    # Skip subagent outputs - only render main agent content in chat
                    if not is_main_agent:
                        logger.debug("Skipping subagent message ns=%s", ns_key)
                        continue

                    if not isinstance(data, tuple) or len(data) != 2:  # noqa: PLR2004  # message stream data is a 2-tuple (message, metadata)
                        logger.debug(
                            "Skipping non-2-tuple message data: type=%s",
                            type(data).__name__,
                        )
                        continue

                    message, metadata = data
                    logger.debug(
                        "Processing message: type=%s id=%s has_content_blocks=%s",
                        type(message).__name__,
                        getattr(message, "id", None),
                        hasattr(message, "content_blocks"),
                    )

                    # Filter out summarization model output, but keep UI feedback.
                    # The summarization model streams AIMessage chunks tagged
                    # with lc_source="summarization" in the callback metadata.
                    # These are hidden from the user; only the spinner and a
                    # notification widget provide feedback.
                    if _is_summarization_chunk(metadata):
                        if not summarization_in_progress:
                            summarization_in_progress = True
                            if adapter._set_spinner:
                                await adapter._set_spinner("Offloading")
                        continue

                    # Regular (non-summarization) chunks resumed — summarization
                    # has finished. Mount the notification and reset the spinner.
                    if summarization_in_progress:
                        summarization_in_progress = False
                        try:
                            await adapter._mount_message(SummarizationMessage())
                        except Exception:
                            logger.debug(
                                "Failed to mount summarization notification",
                                exc_info=True,
                            )
                        if adapter._set_spinner and not adapter._current_tool_messages:
                            await adapter._set_spinner("Thinking")

                    if isinstance(message, HumanMessage):
                        content = message.text
                        # Flush pending text for this namespace
                        pending_text = pending_text_by_namespace.get(ns_key, "")
                        if content and pending_text:
                            await _flush_assistant_text_ns(
                                adapter,
                                pending_text,
                                ns_key,
                                assistant_message_by_namespace,
                            )
                            pending_text_by_namespace[ns_key] = ""
                        continue

                    if isinstance(message, ToolMessage):
                        tool_name = getattr(message, "name", "")
                        tool_status = getattr(message, "status", "success")
                        tool_content = format_tool_message_content(message.content)
                        
                        raw_tool_id = getattr(message, "tool_call_id", None)
                        logger.debug(
                            "ToolMessage received: name=%s, status=%s, raw_tool_id=%s (type=%s), active_keys=%s",
                            tool_name,
                            tool_status,
                            raw_tool_id,
                            type(raw_tool_id).__name__,
                            list(file_op_tracker.active.keys()),
                        )
                        
                        tool_id = _normalize_tool_id(raw_tool_id)
                        tool_msg = None
                        tool_args_for_match: dict[str, Any] | None = None

                        # Strategy 1: Direct key match on normalized str ID.
                        # Use pop(key, None) defensively even though the preceding
                        # `in` check makes KeyError impossible in normal asyncio
                        # execution — the guard protects against future refactors
                        # that might introduce an await between the check and pop.
                        if tool_id and tool_id in adapter._current_tool_messages:
                            tool_msg = adapter._current_tool_messages.pop(tool_id, None)
                            if tool_msg is None:
                                logger.warning(
                                    "Strategy 1: key disappeared between check and pop "
                                    "for tool_id=%s; will retry with Strategy 2",
                                    tool_id,
                                )
                            else:
                                logger.debug(
                                    "Matched ToolMessage by direct key tool_id=%s", tool_id
                                )

                        # Strategy 2: Match by widget's _tool_call_id attribute.
                        # When the widget was stored under a different (e.g. index-based)
                        # key, pop it by that key, then sync:
                        #   1. The widget's _tool_call_id attribute → real tool_id
                        #   2. The MessageStore entry's tool_call_id → real tool_id
                        # Without (2) the store's index still maps the OLD key, so
                        # any future prune/hydrate lookup by tool_id would miss it.
                        if not tool_msg and tool_id:
                            for key, msg in list(adapter._current_tool_messages.items()):
                                widget_id = _normalize_tool_id(
                                    getattr(msg, "_tool_call_id", None)
                                )
                                if widget_id == tool_id:
                                    tool_msg = adapter._current_tool_messages.pop(key)
                                    logger.debug(
                                        "Matched ToolMessage by _tool_call_id=%s to key=%s",
                                        tool_id,
                                        key,
                                    )
                                    # Sync widget attribute to the canonical tool_id
                                    # so any caller inspecting _tool_call_id sees the
                                    # correct value after this point.
                                    if key != tool_id:
                                        tool_msg._tool_call_id = tool_id
                                        # Sync the MessageStore index so that
                                        # get_message_by_tool_call_id(tool_id) finds
                                        # this record after pruning/hydration.
                                        if tool_msg.id and adapter._message_store:
                                            try:
                                                adapter._message_store.update_message(
                                                    tool_msg.id,
                                                    tool_call_id=tool_id,
                                                )
                                            except Exception:  # noqa: BLE001
                                                logger.warning(
                                                    "Strategy 2: failed to sync "
                                                    "tool_call_id in store for "
                                                    "msg id=%s tool_id=%s",
                                                    tool_msg.id,
                                                    tool_id,
                                                    exc_info=True,
                                                )
                                    break

                        # Strategy 3: Match by tool_name (fallback for missing IDs)
                        if not tool_msg and tool_name and adapter._current_tool_messages:
                            candidates = [
                                (key, msg)
                                for key, msg in list(adapter._current_tool_messages.items())
                                if getattr(msg, "_tool_name", None) == tool_name
                                and getattr(msg, "is_attached", True)
                            ]
                            if len(candidates) == 1:
                                key, msg = candidates[0]
                                tool_msg = adapter._current_tool_messages.pop(key)
                                logger.debug(
                                    "Matched ToolMessage by tool_name=%s to key=%s",
                                    tool_name,
                                    key,
                                )
                            elif len(candidates) > 1:
                                for key, msg in candidates:
                                    status = getattr(msg, "_status", "pending")
                                    if status in ("pending", "running"):
                                        tool_msg = adapter._current_tool_messages.pop(key)
                                        logger.debug(
                                            "Matched ToolMessage by tool_name=%s status=%s to key=%s",
                                            tool_name,
                                            status,
                                            key,
                                        )
                                        break
                                if not tool_msg and candidates:
                                    key, msg = candidates[0]
                                    tool_msg = adapter._current_tool_messages.pop(key)
                                    logger.debug(
                                        "Matched ToolMessage by tool_name=%s (first candidate) to key=%s",
                                        tool_name,
                                        key,
                                    )

                        # Extract args from matched widget for file_op_tracker matching
                        if tool_msg:
                            tool_args_for_match = getattr(tool_msg, "_args", None)

                        # Now call complete_with_message with args for better matching
                        record = file_op_tracker.complete_with_message(
                            message, tool_args_for_match
                        )
                        
                        logger.debug(
                            "File op record lookup result: %s (diff=%s)",
                            "found" if record else "not found",
                            record.diff[:100] + "..." if record and record.diff else "empty/N/A",
                        )

                        if not tool_msg and tool_id:
                            logger.warning(
                                "ToolMessage unmatched: tool_id=%s name=%s "
                                "remaining keys=%s; widget may be pruned or ID mismatch",
                                tool_id,
                                tool_name,
                                [(k, type(k).__name__) for k in adapter._current_tool_messages.keys()],
                            )

                        # FIX: normalize output_str — never pass empty string to
                        # set_success/set_error; use a placeholder so the result
                        # frame is never silently blank.
                        output_str = str(tool_content) if tool_content else "(no output)"

                        # If the file-op tracker recorded an internal error
                        # (e.g. couldn't read back the file after writing) but
                        # the ToolMessage status is still "success", surface the
                        # internal error in the output so the user isn't silently
                        # left without a diff and no indication of why.
                        if (
                            record is not None
                            and record.status == "error"
                            and record.error
                            and tool_status == "success"
                        ):
                            logger.warning(
                                "File op internal error for tool=%s: %s",
                                tool_name,
                                record.error,
                            )
                            output_str = f"{output_str}\n\n[diff unavailable: {record.error}]"
                        elif (
                            record is None
                            and tool_name in ("write_file", "edit_file")
                            and tool_status == "success"
                        ):
                            logger.warning(
                                "DiffMessage skipped: no file-op record for "
                                "tool=%s tool_call_id=%s (operation not tracked)",
                                tool_name,
                                tool_id,
                            )
                            output_str = f"{output_str}\n\n[diff unavailable: operation was not tracked]"

                        if tool_msg:
                            if tool_status == "success":
                                tool_msg.set_success(output_str)
                            else:
                                tool_msg.set_error(output_str)
                                await dispatch_hook(
                                    "tool.error",
                                    {"tool_names": [tool_msg._tool_name]},
                                )
                        elif tool_id:
                            # Widget not in current tracking map — it was either
                            # pruned before the ToolMessage arrived, or the index
                            # was already cleared by mark_pruned() (see #2.3 fix).
                            # Either way, look up the stored data and recreate a
                            # widget so the result is always visible to the user.
                            msg_data = None
                            if adapter._message_store:
                                # get_message_by_tool_call_id normalises to str
                                # internally — a single call is sufficient.
                                msg_data = adapter._message_store.get_message_by_tool_call_id(
                                    tool_id
                                )

                            if msg_data:
                                logger.debug(
                                    "ToolMessage tool_call_id=%s widget pruned "
                                    "(found in store); recreating widget",
                                    tool_id,
                                )
                                tool_msg = ToolCallMessage(
                                    msg_data.tool_name or tool_name,
                                    msg_data.tool_args or {},
                                    tool_call_id=tool_id,
                                )
                                await adapter._mount_message(tool_msg)

                                if tool_status == "success":
                                    tool_msg.set_success(output_str)
                                    # Any associated DiffMessage is shown via the
                                    # `if record:` block further below — no extra
                                    # work is needed here.
                                else:
                                    tool_msg.set_error(output_str)
                                    await dispatch_hook(
                                        "tool.error",
                                        {"tool_names": [tool_msg._tool_name]},
                                    )
                            else:
                                # Last-resort: neither the live tracking map nor
                                # the message store has a record.  This can happen
                                # when there is a race between pruning and the store
                                # write, or when the ID was never registered (e.g.
                                # a ToolMessage for a tool that was never streamed).
                                # Mount a fallback widget so the result is never
                                # silently swallowed.
                                logger.warning(
                                    "ToolMessage tool_call_id=%s name=%s not found in "
                                    "tracking map or store; mounting fallback widget",
                                    tool_id,
                                    tool_name,
                                )
                                # Use args from the file-op record if available,
                                # so the fallback widget isn't completely empty.
                                fallback_args = (
                                    record.args
                                    if record is not None and record.args
                                    else {}
                                )
                                fallback_msg = ToolCallMessage(
                                    tool_name or "unknown",
                                    fallback_args,
                                    tool_call_id=tool_id,
                                )
                                await adapter._mount_message(fallback_msg)
                                if tool_status == "success":
                                    fallback_msg.set_success(output_str)
                                else:
                                    fallback_msg.set_error(output_str)
                                    await dispatch_hook(
                                        "tool.error",
                                        {"tool_names": [tool_name or "unknown"]},
                                    )
                                # Also persist to store so future lookups work
                                adapter._update_tool_message_in_store(
                                    tool_id, tool_status, output_str
                                )

                        # Reshow spinner only when all in-flight tools have
                        # completed (avoids premature "Thinking..." when
                        # parallel tool calls are active).
                        if adapter._set_spinner and not adapter._current_tool_messages:
                            await adapter._set_spinner("Thinking")

                        # Show file operation results - always show diffs in chat
                        if record:
                            pending_text = pending_text_by_namespace.get(ns_key, "")
                            if pending_text:
                                await _flush_assistant_text_ns(
                                    adapter,
                                    pending_text,
                                    ns_key,
                                    assistant_message_by_namespace,
                                )
                                pending_text_by_namespace[ns_key] = ""
                            if record.diff:
                                diff_msg = DiffMessage(record.diff, record.display_path)
                                await adapter._mount_message(diff_msg)
                            else:
                                logger.debug(
                                    "No diff for tool=%s tool_call_id=%s "
                                    "(content unchanged or after-content unreadable)",
                                    tool_name,
                                    tool_id,
                                )
                        else:
                            # record is None — file_op_tracker has no matching entry.
                            # For non-file tools this is expected; for write_file /
                            # edit_file the warning and output_str annotation were
                            # already applied in the elif branch above.
                            if tool_name not in ("write_file", "edit_file"):
                                logger.debug(
                                    "No file-op record for tool=%s tool_call_id=%s "
                                    "(non-file tool, expected)",
                                    tool_name,
                                    tool_id,
                                )
                        continue

                    # Extract token usage (before content_blocks check
                    # - usage may be on any chunk)
                    if hasattr(message, "usage_metadata"):
                        usage = message.usage_metadata
                        if usage:
                            input_toks = usage.get("input_tokens", 0)
                            output_toks = usage.get("output_tokens", 0)
                            total_toks = usage.get("total_tokens", 0)
                            from invincat_cli.config import settings

                            active_model = settings.model_name or ""
                            if input_toks or output_toks:
                                # Model gives split counts — preferred path
                                turn_stats.record_request(
                                    active_model, input_toks, output_toks
                                )
                                captured_input_tokens = max(
                                    captured_input_tokens, input_toks + output_toks
                                )
                            elif total_toks:
                                # Fallback: model gives only total (no split)
                                turn_stats.record_request(active_model, total_toks, 0)
                                captured_input_tokens = max(
                                    captured_input_tokens, total_toks
                                )

                            # Immediately update UI with current token count
                            if adapter._on_tokens_update:
                                adapter._on_tokens_update(captured_input_tokens)

                    # Check if this is an AIMessageChunk with content
                    if not hasattr(message, "content_blocks"):
                        logger.debug(
                            "Message has no content_blocks: type=%s",
                            type(message).__name__,
                        )
                        continue

                    # Process content blocks
                    blocks = message.content_blocks
                    logger.debug(
                        "content_blocks count=%d blocks=%s",
                        len(blocks),
                        repr(blocks)[:500],
                    )
                    for block in blocks:
                        block_type = block.get("type")

                        if block_type == "text":
                            text = block.get("text", "")
                            if text:
                                # Track accumulated text for reference
                                pending_text = pending_text_by_namespace.get(ns_key, "")
                                pending_text += text
                                pending_text_by_namespace[ns_key] = pending_text

                                # Get or create assistant message for this namespace
                                current_msg = assistant_message_by_namespace.get(ns_key)
                                if current_msg is None:
                                    # Hide spinner when assistant starts responding
                                    if adapter._set_spinner:
                                        await adapter._set_spinner(None)
                                    msg_id = f"asst-{uuid.uuid4().hex[:8]}"
                                    # Mark active BEFORE mounting so pruning
                                    # (triggered by mount) won't remove it
                                    # (_mount_message can trigger
                                    # _prune_old_messages if the window exceeds
                                    # WINDOW_SIZE.)
                                    if adapter._set_active_message:
                                        adapter._set_active_message(msg_id)
                                    current_msg = AssistantMessage(id=msg_id)
                                    await adapter._mount_message(current_msg)
                                    assistant_message_by_namespace[ns_key] = current_msg

                                # Append just the new text chunk for smoother
                                # streaming (uses MarkdownStream internally for
                                # better performance)
                                await current_msg.append_content(text)

                        elif block_type in {"tool_call_chunk", "tool_call"}:
                            chunk_name = block.get("name")
                            chunk_args = block.get("args")
                            chunk_id = block.get("id")
                            chunk_index = block.get("index")

                            # Normalize buffer key — always str for consistent
                            # lookup in _current_tool_messages.
                            raw_buffer_key: str | int
                            if chunk_index is not None:
                                raw_buffer_key = chunk_index
                            elif chunk_id is not None:
                                raw_buffer_key = chunk_id
                            else:
                                # Use a UUID so parallel chunks without id/index
                                # don't collide — f"unknown-{len(buffers)}" would
                                # repeat when a previous buffer was already popped.
                                raw_buffer_key = f"unknown-{uuid.uuid4().hex[:8]}"

                            buffer = tool_call_buffers.setdefault(
                                raw_buffer_key,
                                {
                                    "name": None,
                                    "id": None,
                                    "args": None,
                                    "args_parts": [],
                                    "args_finalized": False,
                                },
                            )

                            if chunk_name:
                                buffer["name"] = chunk_name
                            if chunk_id:
                                buffer["id"] = chunk_id

                            if isinstance(chunk_args, dict):
                                buffer["args"] = chunk_args
                                buffer["args_parts"] = []
                            elif isinstance(chunk_args, str):
                                if chunk_args:
                                    parts: list[str] = buffer.setdefault(
                                        "args_parts", []
                                    )
                                    if not parts or chunk_args != parts[-1]:
                                        parts.append(chunk_args)
                                    buffer["args"] = "".join(parts)
                            elif chunk_args is not None:
                                buffer["args"] = chunk_args

                            buffer_name = buffer.get("name")
                            buffer_id = buffer.get("id")

                            # Need at least a name before doing anything
                            if buffer_name is None:
                                continue

                            # Normalize display_key to str for consistent map keys
                            raw_display_key = buffer_id if buffer_id is not None else raw_buffer_key
                            display_key = _normalize_tool_id(raw_display_key) or str(raw_display_key)

                            # --- EARLY MOUNT: show widget as soon as name is known,
                            # before args have finished streaming.  This eliminates
                            # the blank gap between the assistant text message and
                            # the tool call widget appearing in the UI.
                            if display_key not in displayed_tool_ids:
                                # Check if this buffer was previously mounted under the
                                # index-based key (when buffer_id was not yet available).
                                # If so, re-key the existing widget instead of creating a
                                # new one — this prevents zombie widget accumulation.
                                index_key = _normalize_tool_id(raw_buffer_key) or str(raw_buffer_key)
                                if display_key != index_key and index_key in adapter._current_tool_messages:
                                    # Re-key: move existing widget to the real ID key
                                    existing = adapter._current_tool_messages.pop(index_key)
                                    existing._tool_call_id = display_key
                                    adapter._current_tool_messages[display_key] = existing
                                    displayed_tool_ids.add(display_key)
                                    # Also update the store so that if this widget is
                                    # later pruned, the fallback hydration path uses
                                    # the correct tool_call_id for result matching.
                                    if existing.id and adapter._message_store:
                                        try:
                                            adapter._message_store.update_message(
                                                existing.id, tool_call_id=display_key
                                            )
                                        except Exception:  # noqa: BLE001
                                            logger.warning(
                                                "Failed to update tool_call_id in store "
                                                "for message id=%s display_key=%s",
                                                existing.id,
                                                display_key,
                                                exc_info=True,
                                            )
                                    # Also re-key the file op tracker so that
                                    # complete_with_message() can match by UUID
                                    # when the ToolMessage arrives.  Without this,
                                    # the tracker still holds the index-based key
                                    # (e.g. "0") and the UUID lookup fails —
                                    # causing DiffMessage to never be shown when
                                    # multiple concurrent file ops are in flight.
                                    old_record = file_op_tracker.active.pop(
                                        index_key, None
                                    )
                                    if old_record is not None:
                                        old_record.tool_call_id = display_key
                                        file_op_tracker.active[display_key] = (
                                            old_record
                                        )
                                        logger.debug(
                                            "Re-keyed file_op_tracker record "
                                            "from index key=%s to id=%s",
                                            index_key,
                                            display_key,
                                        )
                                    logger.debug(
                                        "Re-keyed ToolCallMessage from index key=%s to id=%s",
                                        index_key,
                                        display_key,
                                    )
                                else:
                                    displayed_tool_ids.add(display_key)

                                    # Flush any pending assistant text first
                                    pending_text = pending_text_by_namespace.get(ns_key, "")
                                    if pending_text:
                                        await _flush_assistant_text_ns(
                                            adapter,
                                            pending_text,
                                            ns_key,
                                            assistant_message_by_namespace,
                                        )
                                        pending_text_by_namespace[ns_key] = ""
                                        assistant_message_by_namespace.pop(ns_key, None)

                                    # Hide spinner before showing tool call widget
                                    if adapter._set_spinner:
                                        await adapter._set_spinner(None)

                                    # Mount immediately with empty args — args will be
                                    # filled in via update_args() once fully parsed.
                                    logger.debug(
                                        "Early-mounting ToolCallMessage: name=%s key=%s",
                                        buffer_name,
                                        display_key,
                                    )
                                    tool_msg = ToolCallMessage(
                                        buffer_name,
                                        {},
                                        tool_call_id=display_key,
                                        args_finalized=False,
                                    )
                                    await adapter._mount_message(tool_msg)
                                    adapter._current_tool_messages[display_key] = tool_msg

                            # --- ARGS UPDATE: once args are fully parseable, update
                            # the already-visible widget and register the file op.
                            if not buffer.get("args_finalized"):
                                raw_args = buffer.get("args")
                                parsed_args = None

                                if isinstance(raw_args, dict):
                                    parsed_args = raw_args
                                elif isinstance(raw_args, str) and raw_args:
                                    try:
                                        parsed_args = json.loads(raw_args)
                                    except json.JSONDecodeError:
                                        pass  # Still streaming — will retry next chunk

                                if parsed_args is not None:
                                    if not isinstance(parsed_args, dict):
                                        parsed_args = {"value": parsed_args}

                                    buffer["args_finalized"] = True

                                    logger.debug(
                                        "Args finalized for tool key=%s args=%s",
                                        display_key,
                                        repr(parsed_args)[:200],
                                    )

                                    # Update the widget with real args now that
                                    # they have fully streamed in.
                                    tool_msg = adapter._current_tool_messages.get(display_key)
                                    if tool_msg is not None:
                                        tool_msg.update_args(parsed_args)

                                    # Register file op only once args are final.
                                    # Use display_key (always a str) rather than
                                    # buffer_id (which may still be None when the
                                    # streaming chunk carries args but not yet an
                                    # id).  complete_with_message reconciles the
                                    # key via tool-name fallback when the ToolMessage
                                    # arrives with its canonical UUID.
                                    logger.debug(
                                        "Starting file op: name=%s, display_key=%s, active_keys=%s",
                                        buffer_name,
                                        display_key,
                                        list(file_op_tracker.active.keys()),
                                    )
                                    file_op_tracker.start_operation(
                                        buffer_name, parsed_args, display_key
                                    )

                                    tool_call_buffers.pop(raw_buffer_key, None)

                    if getattr(message, "chunk_position", None) == "last":
                        pending_text = pending_text_by_namespace.get(ns_key, "")
                        if pending_text:
                            await _flush_assistant_text_ns(
                                adapter,
                                pending_text,
                                ns_key,
                                assistant_message_by_namespace,
                            )
                            pending_text_by_namespace[ns_key] = ""
                            assistant_message_by_namespace.pop(ns_key, None)

            # Reset summarization state if stream ended mid-summarization
            # (e.g. middleware error, stream exhausted before regular chunks).
            if summarization_in_progress:
                summarization_in_progress = False
                try:
                    await adapter._mount_message(SummarizationMessage())
                except Exception:
                    logger.debug(
                        "Failed to mount summarization notification",
                        exc_info=True,
                    )
                if adapter._set_spinner and not adapter._current_tool_messages:
                    await adapter._set_spinner("Thinking")

            # Flush any remaining text from all namespaces
            for ns_key, pending_text in list(pending_text_by_namespace.items()):
                if pending_text:
                    await _flush_assistant_text_ns(
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
                any_rejected = False
                resume_payload: dict[str, Any] = {}

                # Inject error resumes for ask_user interrupts that failed validation.
                # This unblocks the graph so the agent can handle the error gracefully
                # rather than leaving the interrupt unresolved.
                for interrupt_id, error_msg in error_ask_user_ids.items():
                    resume_payload[interrupt_id] = {
                        "status": "error",
                        "error": error_msg,
                    }

                for interrupt_id, ask_req in list(pending_ask_user.items()):
                    questions = ask_req["questions"]

                    if adapter._request_ask_user:
                        if adapter._set_spinner:
                            await adapter._set_spinner(None)
                        result: dict[str, Any] = {
                            "type": "error",
                            "error": "ask_user callback returned no response",
                        }
                        try:
                            future = await adapter._request_ask_user(questions)
                        except Exception:
                            logger.exception("Failed to mount ask_user widget")
                            result = {
                                "type": "error",
                                "error": "failed to display ask_user prompt",
                            }
                            future = None

                        if future is None:
                            logger.error(
                                "ask_user callback returned no Future; "
                                "reporting as error"
                            )
                        else:
                            try:
                                future_result = await future
                                if isinstance(future_result, dict):
                                    result = future_result
                                else:
                                    logger.error(
                                        "ask_user future returned non-dict result: %s",
                                        type(future_result).__name__,
                                    )
                                    result = {
                                        "type": "error",
                                        "error": "invalid ask_user widget result",
                                    }
                            except Exception:
                                logger.exception(
                                    "ask_user future resolution failed; "
                                    "reporting as error"
                                )
                                result = {
                                    "type": "error",
                                    "error": "failed to receive ask_user response",
                                }

                        result_type = result.get("type")
                        if result_type == "answered":
                            answers = result.get("answers", [])
                            if isinstance(answers, list):
                                resume_payload[interrupt_id] = {"answers": answers}
                                tool_id = ask_req["tool_call_id"]
                                norm_tool_id = _normalize_tool_id(tool_id)
                                if norm_tool_id and norm_tool_id in adapter._current_tool_messages:
                                    tool_msg = adapter._current_tool_messages[norm_tool_id]
                                    tool_msg.set_success("User answered")
                                    adapter._current_tool_messages.pop(norm_tool_id, None)
                            else:
                                logger.error(
                                    "ask_user answered payload had non-list "
                                    "answers: %s",
                                    type(answers).__name__,
                                )
                                resume_payload[interrupt_id] = {
                                    "status": "error",
                                    "error": "invalid ask_user answers payload",
                                    "answers": ["" for _ in questions],
                                }
                                any_rejected = True
                        elif result_type == "cancelled":
                            resume_payload[interrupt_id] = {
                                "status": "cancelled",
                                "answers": ["" for _ in questions],
                            }
                            any_rejected = True
                        else:
                            error_text = result.get("error")
                            if not isinstance(error_text, str) or not error_text:
                                error_text = "ask_user interaction failed"
                            resume_payload[interrupt_id] = {
                                "status": "error",
                                "error": error_text,
                                "answers": ["" for _ in questions],
                            }
                            any_rejected = True
                    else:
                        logger.warning(
                            "ask_user interrupt received but no UI callback is "
                            "registered; reporting as error"
                        )
                        resume_payload[interrupt_id] = {
                            "status": "error",
                            "error": "ask_user not supported by this UI",
                            "answers": ["" for _ in questions],
                        }

                for interrupt_id, hitl_request in list(pending_interrupts.items()):
                    action_requests = hitl_request["action_requests"]

                    if session_state.auto_approve:
                        decisions: list[HITLDecision] = [
                            ApproveDecision(type="approve") for _ in action_requests
                        ]
                        resume_payload[interrupt_id] = {"decisions": decisions}
                        for tool_msg in list(adapter._current_tool_messages.values()):
                            tool_msg.set_running()
                    else:
                        # Batch approval - one dialog for all parallel tool calls
                        await dispatch_hook(
                            "permission.request",
                            {
                                "tool_names": [
                                    r.get("name", "") for r in action_requests
                                ]
                            },
                        )
                        future = await adapter._request_approval(
                            action_requests, assistant_id
                        )
                        decision = await future

                        if isinstance(decision, dict):
                            decision_type = decision.get("type")

                            if decision_type == "auto_approve_all":
                                session_state.auto_approve = True
                                if adapter._on_auto_approve_enabled:
                                    adapter._on_auto_approve_enabled()
                                decisions = [
                                    ApproveDecision(type="approve")
                                    for _ in action_requests
                                ]
                                tool_msgs = list(
                                    adapter._current_tool_messages.values()
                                )
                                for tool_msg in tool_msgs:
                                    tool_msg.set_running()
                                for action_request in action_requests:
                                    tool_name = action_request.get("name")
                                    if tool_name in {
                                        "write_file",
                                        "edit_file",
                                    }:
                                        args = action_request.get("args", {})
                                        if isinstance(args, dict):
                                            file_op_tracker.mark_hitl_approved(
                                                tool_name, args
                                            )

                            elif decision_type == "approve":
                                decisions = [
                                    ApproveDecision(type="approve")
                                    for _ in action_requests
                                ]
                                tool_msgs = list(
                                    adapter._current_tool_messages.values()
                                )
                                for tool_msg in tool_msgs:
                                    tool_msg.set_running()
                                for action_request in action_requests:
                                    tool_name = action_request.get("name")
                                    if tool_name in {
                                        "write_file",
                                        "edit_file",
                                    }:
                                        args = action_request.get("args", {})
                                        if isinstance(args, dict):
                                            file_op_tracker.mark_hitl_approved(
                                                tool_name, args
                                            )

                            elif decision_type == "reject":
                                decisions = [
                                    RejectDecision(type="reject")
                                    for _ in action_requests
                                ]
                                tool_msgs = list(
                                    adapter._current_tool_messages.values()
                                )
                                for tool_msg in tool_msgs:
                                    tool_msg.set_rejected()
                                adapter._current_tool_messages.clear()
                                any_rejected = True
                            else:
                                logger.warning(
                                    "Unexpected HITL decision type: %s",
                                    decision_type,
                                )
                                decisions = [
                                    RejectDecision(type="reject")
                                    for _ in action_requests
                                ]
                                for tool_msg in list(
                                    adapter._current_tool_messages.values()
                                ):
                                    tool_msg.set_rejected()
                                adapter._current_tool_messages.clear()
                                any_rejected = True
                        else:
                            logger.warning(
                                "HITL decision was not a dict: %s",
                                type(decision).__name__,
                            )
                            decisions = [
                                RejectDecision(type="reject") for _ in action_requests
                            ]
                            for tool_msg in list(
                                adapter._current_tool_messages.values()
                            ):
                                tool_msg.set_rejected()
                            adapter._current_tool_messages.clear()
                            any_rejected = True

                        resume_payload[interrupt_id] = {"decisions": decisions}

                        if any_rejected:
                            break

                suppress_resumed_output = any_rejected

            if interrupt_occurred and resume_payload:
                if suppress_resumed_output and not pending_ask_user:
                    await adapter._mount_message(
                        AppMessage(
                            "Command rejected. Tell the agent what you'd like instead."
                        )
                    )
                    turn_stats.wall_time_seconds = time.monotonic() - start_time
                    return turn_stats

                stream_input = Command(resume=resume_payload)
            else:
                await dispatch_hook("task.complete", {"thread_id": thread_id})
                break

    except (asyncio.CancelledError, KeyboardInterrupt):
        # Use shield to protect cleanup from being cancelled.
        # Without this, if the user presses ESC again quickly or sends a new
        # message during cleanup, the cleanup await itself gets cancelled,
        # leaving state inconsistent (e.g., _agent_running still True).
        try:
            await asyncio.shield(
                _handle_interrupt_cleanup(
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
                tool_msg.set_error("Interrupted by error")
            except Exception:  # noqa: BLE001
                pass
        adapter._current_tool_messages.clear()
        raise

    # Update token count and return stats
    turn_stats.wall_time_seconds = time.monotonic() - start_time
    await _report_and_persist_tokens(
        adapter,
        agent,
        config,
        captured_input_tokens,
        captured_output_tokens,
    )
    return turn_stats


async def _handle_interrupt_cleanup(
    *,
    adapter: TextualUIAdapter,
    agent: Any,  # noqa: ANN401  # Dynamic agent graph type
    config: RunnableConfig,
    pending_text_by_namespace: dict[tuple, str],
    captured_input_tokens: int,
    captured_output_tokens: int,
    turn_stats: SessionStats,
    start_time: float,
) -> None:
    """Shared cleanup for CancelledError and KeyboardInterrupt.

    Args:
        adapter: UI adapter with display callbacks.
        agent: The LangGraph agent.
        config: Runnable config with `thread_id`.
        pending_text_by_namespace: Accumulated text per namespace.
        captured_input_tokens: Input tokens captured before interrupt.
        captured_output_tokens: Output tokens captured before interrupt.
        turn_stats: Stats for the current turn.
        start_time: Monotonic timestamp when the turn began.
    """
    from langchain_core.messages import HumanMessage

    # Clear active message immediately so it won't block pruning.
    # If we don't do this, the store still thinks it's active and protects
    # from pruning, which breaks get_messages_to_prune(), potentially
    # blocking all future pruning.
    if adapter._set_active_message:
        adapter._set_active_message(None)

    # Hide spinner (may still show "Offloading" if interrupted mid-offload)
    if adapter._set_spinner:
        await adapter._set_spinner(None)

    await adapter._mount_message(AppMessage("Interrupted by user"))

    interrupted_msg = _build_interrupted_ai_message(
        pending_text_by_namespace,
        adapter._current_tool_messages,
    )

    # Save accumulated state before marking tools as rejected (best-effort).
    # State update failures shouldn't prevent cleanup.
    try:
        if interrupted_msg:
            await agent.aupdate_state(config, {"messages": [interrupted_msg]})

        cancellation_msg = HumanMessage(
            content="[SYSTEM] Task interrupted by user. "
            "Previous operation was cancelled."
        )
        await agent.aupdate_state(config, {"messages": [cancellation_msg]})
    except Exception:
        logger.warning("Failed to save interrupted state", exc_info=True)

    # Mark tools as rejected AFTER saving state
    for tool_msg in list(adapter._current_tool_messages.values()):
        tool_msg.set_rejected()
    adapter._current_tool_messages.clear()

    # Keep the token count marked stale whenever interrupted state was captured,
    # including tool-only turns after assistant text was already flushed.
    approximate = interrupted_msg is not None

    turn_stats.wall_time_seconds = time.monotonic() - start_time
    await _report_and_persist_tokens(
        adapter,
        agent,
        config,
        captured_input_tokens,
        captured_output_tokens,
        shield=True,
        approximate=approximate,
    )


async def _persist_context_tokens(
    agent: Any,  # noqa: ANN401  # Dynamic agent graph type
    config: RunnableConfig,
    tokens: int,
) -> None:
    """Best-effort persist of the context token count into graph state.

    Args:
        agent: The LangGraph agent (must support `aupdate_state`).
        config: Runnable config with `thread_id`.
        tokens: Total context tokens to persist.
    """
    try:
        await agent.aupdate_state(config, {"_context_tokens": tokens})
    except Exception:  # non-critical; stale count on resume is acceptable
        logger.warning(
            "Failed to persist _context_tokens=%d; token count may be stale on resume",
            tokens,
            exc_info=True,
        )


async def _report_and_persist_tokens(
    adapter: TextualUIAdapter,
    agent: Any,  # noqa: ANN401  # Dynamic agent graph type
    config: RunnableConfig,
    captured_input_tokens: int,
    captured_output_tokens: int,
    *,
    shield: bool = False,
    approximate: bool = False,
) -> None:
    """Update the token display and best-effort persist to graph state.

    Args:
        adapter: UI adapter with token callbacks.
        agent: The LangGraph agent.
        config: Runnable config with `thread_id` in its configurable dict.
        captured_input_tokens: Total input tokens captured during the turn.
        captured_output_tokens: Total output tokens captured during the turn.
        shield: When `True`, suppress exceptions and `CancelledError` from the
            persist call so that interrupt handlers can safely await this.
        approximate: When `True`, signal to the UI that the count is stale
            (e.g. after an interrupted generation) by appending "+".
    """
    if captured_input_tokens or captured_output_tokens:
        if adapter._on_tokens_update:
            adapter._on_tokens_update(captured_input_tokens, approximate=approximate)
        if shield:
            try:
                await _persist_context_tokens(agent, config, captured_input_tokens)
            except (Exception, asyncio.CancelledError):
                logger.debug(
                    "Token persist suppressed during interrupt cleanup",
                    exc_info=True,
                )
        else:
            await _persist_context_tokens(agent, config, captured_input_tokens)
    elif adapter._on_tokens_show:
        adapter._on_tokens_show(approximate=approximate)


async def _flush_assistant_text_ns(
    adapter: TextualUIAdapter,
    text: str,
    ns_key: tuple,
    assistant_message_by_namespace: dict[tuple, Any],
) -> None:
    """Flush accumulated assistant text for a specific namespace.

    Finalizes the streaming by stopping the MarkdownStream.
    If no message exists yet, creates one with the full content.
    """
    if not text.strip():
        return

    current_msg = assistant_message_by_namespace.get(ns_key)
    if current_msg is None:
        # No message was created during streaming - create one with full content
        msg_id = f"asst-{uuid.uuid4().hex[:8]}"
        current_msg = AssistantMessage(text, id=msg_id)
        await adapter._mount_message(current_msg)
        await current_msg.write_initial_content()
        assistant_message_by_namespace[ns_key] = current_msg
    else:
        # Stop the stream to finalize the content
        await current_msg.stop_stream()

    # When the AssistantMessage was first mounted and recorded in the
    # MessageStore, it had empty content (streaming hadn't started yet).
    # Now that streaming is done, the widget holds the full text in
    # `_content`, but the store's MessageData still has `content=""`.
    # If the message is later pruned and re-hydrated, `to_widget()` would
    # recreate it from that stale empty string. This call copies the
    # widget's final content back into the store so re-hydration works.
    if adapter._sync_message_content and current_msg.id:
        adapter._sync_message_content(current_msg.id, current_msg._content)

    # Clear active message since streaming is done
    if adapter._set_active_message:
        adapter._set_active_message(None)