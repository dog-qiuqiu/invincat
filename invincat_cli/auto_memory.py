"""Middleware for automatic memory updates.

Periodically injects a hint into the system prompt asking the model to
evaluate whether important information from the conversation should be
saved to persistent memory (AGENTS.md). The model then uses its existing
file tools (edit_file / write_file) to update memory files if needed.

Configuration (in ``~/.invincat/config.toml``):

    [auto_memory]
    enabled = true
    interval = 10
    on_exit = true

- **enabled**: Enable or disable automatic memory updates (default: true)
- **interval**: Number of user-model turns between memory checks (default: 10)
- **on_exit**: Trigger a final memory check when the session ends (default: true)
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, NotRequired, cast

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
)

logger = logging.getLogger(__name__)

_AUTO_MEMORY_HINT = """\

### Auto Memory Check

You have had {turns} exchanges with the user. Review the recent conversation \
and if you have learned any of the following, proactively update the memory \
file using edit_file:

- User preferences or coding conventions
- Project architecture decisions
- Best practices or anti-patterns discovered
- Important context the user expects you to remember

Memory files (project takes precedence over global):
{memory_paths}

Only update if there is genuinely valuable information worth preserving. \
Do NOT update memory on every check — only when something meaningful was learned. \
If nothing new is worth saving, simply continue the conversation normally."""

_EXIT_MEMORY_HINT = """\

### Auto Memory Check (Session Start)

This is a new session. The previous session may have contained important \
information worth preserving. If the current conversation reveals any of the \
following, proactively update the memory file using edit_file:

- User preferences or coding conventions
- Project architecture decisions
- Best practices or anti-patterns discovered
- Important context the user expects you to remember

Memory files (project takes precedence over global):
{memory_paths}

Only update if there is genuinely valuable information worth preserving. \
If nothing new is worth saving, simply continue the conversation normally."""

_MARKER_DIR = Path.home() / ".invincat" / "agent"
_MARKER_FILE = _MARKER_DIR / ".auto_memory_pending"

EXIT_MARKER_EXPIRY_SECONDS = 24 * 60 * 60

# Protects the check-then-consume sequence so that multiple
# AutoMemoryMiddleware instances in the same process cannot both
# read the marker as present and both attempt to consume it.
_marker_lock = threading.Lock()


class AutoMemoryState(AgentState):
    """State for auto-memory middleware."""

    _auto_memory_user_turn_count: Annotated[NotRequired[int], PrivateStateAttr]
    _auto_memory_hint_injected: Annotated[NotRequired[bool], PrivateStateAttr]


_DEFAULT_INTERVAL = 10
_DEFAULT_ENABLED = True
_DEFAULT_ON_EXIT = True


def _read_auto_memory_config() -> dict[str, Any]:
    """Read ``[auto_memory]`` section from ``~/.invincat/config.toml``.

    Returns:
        Dictionary with keys ``enabled``, ``interval``, ``on_exit``.
    """
    import tomllib

    config_path = Path.home() / ".invincat" / "config.toml"
    defaults: dict[str, Any] = {
        "enabled": _DEFAULT_ENABLED,
        "interval": _DEFAULT_INTERVAL,
        "on_exit": _DEFAULT_ON_EXIT,
    }

    try:
        with config_path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return defaults
    except (PermissionError, OSError, tomllib.TOMLDecodeError):
        logger.warning(
            "Could not read auto_memory config from %s",
            config_path,
            exc_info=True,
        )
        return defaults

    section = data.get("auto_memory", {})
    if not isinstance(section, dict):
        return defaults

    if "enabled" in section:
        defaults["enabled"] = bool(section["enabled"])
    if "interval" in section:
        interval = int(section["interval"])
        if interval < 1:
            logger.warning(
                "auto_memory.interval must be >= 1, got %d; using default",
                interval,
            )
        else:
            defaults["interval"] = interval
    if "on_exit" in section:
        defaults["on_exit"] = bool(section["on_exit"])

    return defaults


def save_auto_memory_config(
    *,
    enabled: bool,
    interval: int,
    on_exit: bool,
    config_path: Path | None = None,
) -> bool:
    """Save auto-memory configuration to ``~/.invincat/config.toml``.

    Args:
        enabled: Whether auto-memory is enabled.
        interval: Number of turns between memory checks.
        on_exit: Whether to write exit marker.
        config_path: Path to config file. Defaults to ~/.invincat/config.toml.

    Returns:
        True if save succeeded, False otherwise.
    """
    import tomllib

    import tomli_w

    if config_path is None:
        config_path = Path.home() / ".invincat" / "config.toml"

    config_path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {}
    if config_path.exists():
        try:
            with config_path.open("rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            logger.warning("Failed to read existing config, will overwrite: %s", exc)
            data = {}

    data["auto_memory"] = {
        "enabled": enabled,
        "interval": max(1, interval),
        "on_exit": on_exit,
    }

    try:
        fd, tmp_path = tempfile.mkstemp(dir=config_path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(data, f)
            Path(tmp_path).replace(config_path)
        except BaseException:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()
            raise
        logger.debug("Saved auto_memory config to %s", config_path)
        return True
    except OSError as exc:
        logger.error("Failed to save auto_memory config: %s", exc)
        return False


def _has_exit_marker() -> bool:
    """Check if a previous session left an auto-memory exit marker.

    Also checks if the marker has expired (older than 24 hours).

    Returns:
        True if the marker file exists and is not expired.
    """
    try:
        if not _MARKER_FILE.exists():
            return False

        content = _MARKER_FILE.read_text().strip()
        for line in content.split("\n"):
            if line.startswith("timestamp="):
                try:
                    timestamp_str = line.split("=", 1)[1]
                    timestamp = float(timestamp_str)
                    if time.time() - timestamp > EXIT_MARKER_EXPIRY_SECONDS:
                        logger.debug("Exit marker expired, removing")
                        _consume_exit_marker()
                        return False
                except (ValueError, IndexError):
                    pass
                break

        return True
    except OSError:
        return False


def _consume_exit_marker() -> None:
    """Remove the exit marker file after it has been processed."""
    try:
        _MARKER_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def write_exit_marker(thread_id: str = "") -> None:
    """Write an exit marker file for the next session to pick up.

    Args:
        thread_id: The thread ID of the session that is ending.
    """
    try:
        _MARKER_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = time.time()
        iso_timestamp = datetime.fromtimestamp(timestamp).isoformat()
        content = f"thread_id={thread_id}\ntimestamp={timestamp}\niso_time={iso_timestamp}\n"
        _MARKER_FILE.write_text(content)
        logger.debug("Auto-memory exit marker written to %s", _MARKER_FILE)
    except OSError:
        logger.debug("Failed to write auto-memory exit marker", exc_info=True)


def cleanup_expired_exit_markers() -> int:
    """Clean up expired exit marker files.

    Returns:
        Number of markers removed.
    """
    removed = 0
    try:
        if _MARKER_FILE.exists():
            content = _MARKER_FILE.read_text().strip()
            for line in content.split("\n"):
                if line.startswith("timestamp="):
                    try:
                        timestamp_str = line.split("=", 1)[1]
                        timestamp = float(timestamp_str)
                        if time.time() - timestamp > EXIT_MARKER_EXPIRY_SECONDS:
                            _consume_exit_marker()
                            removed = 1
                            logger.debug("Cleaned up expired exit marker")
                    except (ValueError, IndexError):
                        pass
                    break
    except OSError:
        pass
    return removed


def _check_memory_file_updated(state: AutoMemoryState, memory_paths: list[str]) -> bool:
    """Check if any memory file was updated in the last tool calls.

    Args:
        state: Current agent state containing messages.
        memory_paths: List of memory file paths to check.

    Returns:
        True if a memory file was updated.
    """
    messages = state.get("messages", [])
    if not messages:
        return False

    # Iterate the last 10 messages in reverse (most-recent first) so we can
    # return True as soon as a match is found without scanning the full history.
    for msg in messages[-10:][::-1]:
        msg_type = getattr(msg, "type", None)
        if msg_type == "tool":
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                for path in memory_paths:
                    if path in content:
                        logger.debug("Detected memory file update: %s", path)
                        return True
        elif msg_type == "ai":
            tool_calls = getattr(msg, "tool_calls", [])
            for tool_call in tool_calls:
                tool_name = tool_call.get("name", "") if isinstance(tool_call, dict) else getattr(tool_call, "name", "")
                if tool_name in ("edit_file", "write_file"):
                    args = tool_call.get("args", {}) if isinstance(tool_call, dict) else getattr(tool_call, "args", {})
                    # Use getattr fallback consistent with how tool_name is
                    # extracted above, so object-style tool calls are handled.
                    file_path = args.get("file_path", "") if isinstance(args, dict) else getattr(args, "file_path", "")
                    for path in memory_paths:
                        if path in file_path:
                            logger.debug("Detected memory file update via tool call: %s", path)
                            return True
    return False


class AutoMemoryMiddleware(AgentMiddleware):
    """Periodically inject a memory-check hint into the system prompt.

    After every ``interval`` user-model turns, appends a brief section to
    the system prompt asking the model to evaluate whether anything from
    the conversation should be persisted to AGENTS.md.  The model uses
    its existing file tools to perform the update — no extra LLM calls
    are needed.

    On session start, if an exit marker from a previous session exists,
    the hint is injected on the first few exchanges to encourage the
    model to preserve any important information early on.
    """

    state_schema = AutoMemoryState

    def __init__(
        self,
        *,
        memory_paths: list[str] | None = None,
        interval: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Initialize auto-memory middleware.

        Args:
            memory_paths: Paths to AGENTS.md files to include in the hint.
            interval: Number of turns between memory checks.
            enabled: Whether auto-memory is active.
        """
        config = _read_auto_memory_config()
        self._enabled = enabled if enabled is not None else config["enabled"]
        self._interval = interval if interval is not None else config["interval"]
        self._memory_paths = memory_paths or []
        self._exit_marker_consumed = False

        cleanup_expired_exit_markers()

    @property
    def enabled(self) -> bool:
        """Whether auto-memory is currently active."""
        return self._enabled

    def _should_show_hint(self, state: AutoMemoryState) -> bool:
        """Determine whether the memory hint should be shown for this turn.

        Args:
            state: Current agent state.

        Returns:
            True if the hint should be injected.
        """
        if not self._enabled:
            return False

        hint_injected = state.get("_auto_memory_hint_injected", False)
        if hint_injected:
            return False

        user_turn_count = state.get("_auto_memory_user_turn_count", 0) or 0

        if user_turn_count >= self._interval:
            return True

        if _has_exit_marker() and not self._exit_marker_consumed:
            if user_turn_count >= 3:
                return True

        return False

    def _build_hint(self, turns: int, *, is_exit_followup: bool = False) -> str:
        """Build the memory hint string.

        Args:
            turns: Current turn count.
            is_exit_followup: Whether this hint is triggered by an exit marker.

        Returns:
            Formatted hint string.
        """
        if self._memory_paths:
            paths_lines: list[str] = []
            project_paths: list[str] = []
            global_paths: list[str] = []

            for p in self._memory_paths:
                if ".invincat/agent/AGENTS.md" in p or p.endswith("/agent/AGENTS.md"):
                    global_paths.append(p)
                elif ".invincat/AGENTS.md" in p or p.endswith("/AGENTS.md"):
                    project_paths.append(p)
                else:
                    global_paths.append(p)

            if project_paths:
                for p in project_paths:
                    paths_lines.append(f"- `{p}` (project - OVERRIDES global for this project)")
            if global_paths:
                for p in global_paths:
                    paths_lines.append(f"- `{p}` (global - for cross-project preferences)")

            paths_str = "\n".join(paths_lines)
        else:
            paths_str = "- `~/.invincat/agent/AGENTS.md` (global - for cross-project preferences)"

        template = _EXIT_MEMORY_HINT if is_exit_followup else _AUTO_MEMORY_HINT
        return template.format(turns=turns, memory_paths=paths_str)

    def before_agent(
        self,
        state: AutoMemoryState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        """Increment user turn counter on each agent invocation.

        Only counts user turns (HumanMessage), not tool call iterations.

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update with incremented user turn count.
        """
        if not self._enabled:
            return None

        messages = state.get("messages", [])
        user_turn_count = state.get("_auto_memory_user_turn_count", 0) or 0

        if messages:
            last_msg = messages[-1]
            msg_type = getattr(last_msg, "type", None)
            if msg_type == "human":
                user_turn_count += 1
                logger.debug("User turn count incremented to %d", user_turn_count)

        # If a fresh exit marker has been written after this instance already
        # consumed one (e.g. a new conversation thread started in the same
        # process), reset the flag so the new marker will be picked up.
        if self._exit_marker_consumed and _has_exit_marker():
            logger.debug(
                "New exit marker detected after previous was consumed; "
                "resetting _exit_marker_consumed flag"
            )
            self._exit_marker_consumed = False

        return {"_auto_memory_user_turn_count": user_turn_count}

    def _check_and_mark_exit_followup(self) -> bool:
        """Check whether an exit marker is present and mark it as consumed.

        Acquires the module-level lock to prevent two middleware instances from
        both seeing the marker as present. Does NOT delete the marker file —
        callers are responsible for deleting it (sync vs async matters here).

        Returns:
            True if an unconsumed exit marker was found.
        """
        with _marker_lock:
            is_exit_followup = _has_exit_marker() and not self._exit_marker_consumed
            if is_exit_followup:
                self._exit_marker_consumed = True
        return is_exit_followup

    def _inject_hint(
        self, request: ModelRequest, *, is_exit_followup: bool = False
    ) -> ModelRequest | None:
        """Build and inject the memory hint into the request's system prompt.

        Args:
            request: The model request to potentially modify.
            is_exit_followup: Whether an exit marker was detected for this call.
                Callers are responsible for passing the correct value and for
                deleting the marker file (sync vs async).

        Returns:
            Modified request with hint appended, or None if no injection needed.
        """
        state = cast("AutoMemoryState", request.state)

        if not self._should_show_hint(state):
            return None

        turns = state.get("_auto_memory_user_turn_count", 0) or 0

        hint = self._build_hint(turns, is_exit_followup=is_exit_followup)
        system_prompt = request.system_prompt or ""
        new_prompt = system_prompt + "\n" + hint

        modified = request.override(system_prompt=new_prompt)
        modified.state["_auto_memory_hint_injected"] = True
        return modified

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Any,
    ) -> ModelResponse:
        """Inject memory hint into system prompt when threshold is reached.

        Args:
            request: The model request being processed.
            handler: The handler function to call.

        Returns:
            The model response from the handler.
        """
        is_exit_followup = self._check_and_mark_exit_followup()
        if is_exit_followup:
            _consume_exit_marker()
        modified_request = self._inject_hint(request, is_exit_followup=is_exit_followup)
        return handler(modified_request or request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Any,
    ) -> ModelResponse:
        """Async variant of wrap_model_call.

        Runs all marker file I/O (stat, read, unlink) via ``asyncio.to_thread``
        so that ``blockbuster`` does not raise ``BlockingError`` from the async
        event loop.

        Args:
            request: The model request being processed.
            handler: The async handler function to call.

        Returns:
            The model response from the handler.
        """
        import asyncio

        def _check_and_consume() -> bool:
            """Check, mark, and delete the exit marker — all blocking I/O in one thread call."""
            is_followup = self._check_and_mark_exit_followup()
            if is_followup:
                _consume_exit_marker()
            return is_followup

        is_exit_followup = await asyncio.to_thread(_check_and_consume)
        modified_request = self._inject_hint(request, is_exit_followup=is_exit_followup)
        return await handler(modified_request or request)

    def after_agent(
        self,
        state: AutoMemoryState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        """Reset turn counter after a hint was injected and check for memory updates.

        If a hint was injected during this agent run (indicated by
        ``_auto_memory_hint_injected`` being True), resets the turn counter
        so the next cycle starts fresh.

        Also checks if any memory file was updated and clears the
        ``memory_contents`` state to trigger a reload.

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update resetting the turn counter and optionally clearing memory.
        """
        if not self._enabled:
            return None

        updates: dict[str, Any] = {}

        hint_injected = state.get("_auto_memory_hint_injected", False)
        if hint_injected:
            updates["_auto_memory_user_turn_count"] = 0
            updates["_auto_memory_hint_injected"] = False

        if self._memory_paths and _check_memory_file_updated(state, self._memory_paths):
            updates["memory_contents"] = None
            logger.debug("Memory file updated, cleared memory_contents for reload")

        return updates if updates else None


class RefreshableMemoryMiddleware(AgentMiddleware):
    """Wrapper around MemoryMiddleware that supports memory refresh.

    This class wraps the standard MemoryMiddleware and adds support for
    refreshing memory contents when they are set to None. This allows
    memory to be reloaded during a session after memory files are updated.

    Usage:
        middleware = RefreshableMemoryMiddleware(
            backend=FilesystemBackend(),
            sources=["~/.invincat/agent/AGENTS.md"],
        )
    """

    def __init__(self, *, backend: Any, sources: list[str]) -> None:
        """Initialize the refreshable memory middleware.

        Args:
            backend: Backend instance for file operations.
            sources: List of memory file paths to load.
        """
        from deepagents.middleware.memory import MemoryMiddleware

        self._memory_middleware = MemoryMiddleware(backend=backend, sources=sources)
        self.sources = sources

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to the wrapped MemoryMiddleware.

        Args:
            name: Attribute name to access.

        Returns:
            The attribute value from the wrapped middleware.
        """
        return getattr(self._memory_middleware, name)

    def before_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        """Load memory content before agent execution.

        Reloads memory if memory_contents is None or not present.

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update with memory_contents populated.
        """
        memory_contents = state.get("memory_contents")

        if memory_contents is None:
            logger.debug("Refreshing memory contents")
            return self._memory_middleware.before_agent(state, runtime, None)

        if "memory_contents" not in state:
            return self._memory_middleware.before_agent(state, runtime, None)

        return None

    async def abefore_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        """Async load memory content before agent execution.

        Reloads memory if memory_contents is None or not present.

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update with memory_contents populated.
        """
        memory_contents = state.get("memory_contents")

        if memory_contents is None:
            logger.debug("Refreshing memory contents (async)")
            return await self._memory_middleware.abefore_agent(state, runtime, None)

        if "memory_contents" not in state:
            return await self._memory_middleware.abefore_agent(state, runtime, None)

        return None

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        """Delegate to wrapped MemoryMiddleware.

        Args:
            request: The model request being processed.
            handler: The handler function to call.

        Returns:
            The model response from the handler.
        """
        return self._memory_middleware.wrap_model_call(request, handler)

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        """Delegate to wrapped MemoryMiddleware (async).

        Args:
            request: The model request being processed.
            handler: The async handler function to call.

        Returns:
            The model response from the handler.
        """
        return await self._memory_middleware.awrap_model_call(request, handler)


__all__ = [
    "AutoMemoryMiddleware",
    "AutoMemoryState",
    "write_exit_marker",
    "save_auto_memory_config",
    "_read_auto_memory_config",
    "cleanup_expired_exit_markers",
    "EXIT_MARKER_EXPIRY_SECONDS",
    "RefreshableMemoryMiddleware",
]
