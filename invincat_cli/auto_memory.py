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
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import datetime
from functools import lru_cache
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
_GLOBAL_MEMORY_DIR = Path.home() / ".invincat"

EXIT_MARKER_EXPIRY_SECONDS = 24 * 60 * 60

# Protects the check-then-consume sequence so that multiple
# AutoMemoryMiddleware instances in the same process cannot both
# read the marker as present and both attempt to consume it.
_marker_lock = threading.Lock()

_MAX_MEMORY_SIZE_CHARS = 8000
"""Warn in hint when AGENTS.md exceeds this size to prompt consolidation."""

_MAX_PER_FILE_CHARS = 4000
"""Maximum characters per memory file included in the hint."""

_MEMORY_SIGNAL_PATTERNS = re.compile(
    r"\b(always|never|prefer(?:ence)?|convention|decided?|policy|"
    r"best\s+practice|should\s+use|don't\s+use|avoid|make\s+sure|"
    r"remember|important|rule|standard|guideline)\b",
    re.IGNORECASE,
)
"""Patterns in user messages that indicate memory-worthy content."""


class AutoMemoryState(AgentState):
    """State for auto-memory middleware."""

    _auto_memory_user_turn_count: Annotated[NotRequired[int], PrivateStateAttr]
    _auto_memory_hint_injected: Annotated[NotRequired[bool], PrivateStateAttr]
    _auto_memory_updated_paths: Annotated[NotRequired[list[str]], PrivateStateAttr]
    """Paths of memory files written during the last agent turn. Empty when no update occurred."""


_DEFAULT_INTERVAL = 10
_DEFAULT_ENABLED = True
_DEFAULT_ON_EXIT = True


@lru_cache(maxsize=1)
def _read_auto_memory_config() -> dict[str, Any]:
    """Read ``[auto_memory]`` section from ``~/.invincat/config.toml``.

    Result is cached for the lifetime of the process. Call
    ``_read_auto_memory_config.cache_clear()`` (or use
    ``save_auto_memory_config``, which does this automatically) after
    writing a new config to pick up the change.

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
        _read_auto_memory_config.cache_clear()
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

        data = json.loads(_MARKER_FILE.read_text())
        timestamp = float(data.get("timestamp", 0))
        if time.time() - timestamp > EXIT_MARKER_EXPIRY_SECONDS:
            logger.debug("Exit marker expired, removing")
            _consume_exit_marker()
            return False
        return True
    except (OSError, json.JSONDecodeError, ValueError, KeyError):
        logger.debug("Could not parse exit marker, treating as absent", exc_info=True)
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
        data = {
            "thread_id": thread_id,
            "timestamp": timestamp,
            "iso_time": datetime.fromtimestamp(timestamp).isoformat(),
        }
        _MARKER_FILE.write_text(json.dumps(data))
        logger.debug("Auto-memory exit marker written to %s", _MARKER_FILE)
    except OSError:
        logger.debug("Failed to write auto-memory exit marker", exc_info=True)


def cleanup_expired_exit_markers() -> int:
    """Clean up expired exit marker files.

    Returns:
        Number of markers removed.
    """
    try:
        if not _MARKER_FILE.exists():
            return 0
        data = json.loads(_MARKER_FILE.read_text())
        timestamp = float(data.get("timestamp", 0))
        if time.time() - timestamp > EXIT_MARKER_EXPIRY_SECONDS:
            _consume_exit_marker()
            logger.debug("Cleaned up expired exit marker")
            return 1
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return 0


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

    The hint also includes the current memory file contents so the model
    can perform a diff-style update rather than blindly appending.
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
        self._pre_agent_hashes: dict[str, str] = {}

        cleanup_expired_exit_markers()

    @property
    def enabled(self) -> bool:
        """Whether auto-memory is currently active."""
        return self._enabled

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _snapshot_hashes(self) -> dict[str, str]:
        """Return a path → content-hash mapping for all tracked memory files."""
        hashes: dict[str, str] = {}
        for path in self._memory_paths:
            try:
                content = Path(path).read_bytes()
                hashes[path] = hashlib.md5(content, usedforsecurity=False).hexdigest()
            except OSError:
                hashes[path] = ""
        return hashes

    def _memory_files_changed(self, before: dict[str, str]) -> list[str]:
        """Return paths of memory files whose content changed since *before* snapshot."""
        changed: list[str] = []
        for path, old_hash in before.items():
            try:
                content = Path(path).read_bytes()
                new_hash = hashlib.md5(content, usedforsecurity=False).hexdigest()
            except OSError:
                new_hash = ""
            if new_hash != old_hash:
                logger.debug("Memory file content changed: %s", path)
                changed.append(path)
        return changed

    def _has_memory_signals(self, state: AutoMemoryState) -> bool:
        """Return True if recent user messages contain memory-worthy keywords.

        Scans the last 5 messages for patterns that suggest the user is
        expressing preferences, decisions, or conventions worth persisting.
        When detected, ``_should_show_hint`` can fire earlier than the
        configured interval.
        """
        messages = state.get("messages", [])
        for msg in messages[-5:]:
            if getattr(msg, "type", None) == "human":
                content = getattr(msg, "content", "")
                if isinstance(content, str) and _MEMORY_SIGNAL_PATTERNS.search(content):
                    return True
        return False

    def _get_current_memory_contents(self) -> str:
        """Read all memory files and return their contents for diff-style updates."""
        parts: list[str] = []
        for path in self._memory_paths:
            try:
                content = Path(path).read_text(encoding="utf-8").strip()
                if not content:
                    continue
                if len(content) > _MAX_PER_FILE_CHARS:
                    omitted = len(content) - _MAX_PER_FILE_CHARS
                    content = (
                        content[:_MAX_PER_FILE_CHARS]
                        + f"\n\u2026 [{omitted} chars omitted]"
                    )
                parts.append(f"[{path}]\n{content}")
            except OSError:
                pass
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _should_show_hint(self, state: AutoMemoryState) -> bool:
        """Return True if the memory hint should be injected this turn.

        Three triggers (in priority order):
        1. Regular interval — ``user_turn_count >= interval``.
        2. Exit marker — previous session ended; fire after 3 exchanges.
        3. Content-aware — memory-worthy signals detected; fire after
           ``interval // 3`` exchanges (min 1).
        """
        if not self._enabled:
            return False

        if state.get("_auto_memory_hint_injected", False):
            return False

        user_turn_count = state.get("_auto_memory_user_turn_count", 0) or 0

        if user_turn_count >= self._interval:
            return True

        if _has_exit_marker() and not self._exit_marker_consumed:
            if user_turn_count >= 3:
                return True

        if self._has_memory_signals(state) and user_turn_count >= max(1, self._interval // 3):
            return True

        return False

    def _build_hint(
        self,
        turns: int,
        *,
        is_exit_followup: bool = False,
        current_memory: str = "",
    ) -> str:
        """Build the memory hint string.

        Args:
            turns: Current user-turn count for context.
            is_exit_followup: Use the session-start variant of the template.
            current_memory: Existing memory file contents to include so the
                model can perform a diff-style update instead of blind append.

        Returns:
            Formatted hint string ready to append to the system prompt.
        """
        if self._memory_paths:
            paths_lines: list[str] = []
            project_paths: list[str] = []
            global_paths: list[str] = []

            _global_dir = _GLOBAL_MEMORY_DIR.resolve()
            for p in self._memory_paths:
                try:
                    resolved = Path(p).expanduser().resolve()
                    if resolved.is_relative_to(_global_dir):
                        global_paths.append(p)
                    else:
                        project_paths.append(p)
                except (ValueError, OSError):
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
        hint = template.format(turns=turns, memory_paths=paths_str)

        if current_memory:
            size_note = ""
            if len(current_memory) > _MAX_MEMORY_SIZE_CHARS:
                size_note = (
                    f"\n\n\u26a0 Memory file is large ({len(current_memory):,} chars). "
                    "Remove outdated entries before adding new ones."
                )
            hint += (
                f"\n\nCurrent memory contents"
                f" (do NOT re-save what is already there):\n"
                f"{current_memory}{size_note}"
            )

        return hint

    def before_agent(
        self,
        state: AutoMemoryState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        """Increment user turn counter and snapshot memory file mtimes.

        Only counts user turns (HumanMessage), not tool call iterations.
        The mtime snapshot is used by ``after_agent`` to detect writes
        without scanning message content.

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

        self._pre_agent_hashes = self._snapshot_hashes()

        return {
            "_auto_memory_user_turn_count": user_turn_count,
            "_auto_memory_updated_paths": [],
        }

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
        self,
        request: ModelRequest,
        *,
        is_exit_followup: bool = False,
        current_memory: str = "",
    ) -> ModelRequest | None:
        """Build and inject the memory hint into the request's system prompt.

        Args:
            request: The model request to potentially modify.
            is_exit_followup: Whether an exit marker was detected for this call.
                Callers are responsible for passing the correct value and for
                deleting the marker file (sync vs async).
            current_memory: Pre-read memory file contents to embed in the hint.

        Returns:
            Modified request with hint appended, or None if no injection needed.
        """
        state = cast("AutoMemoryState", request.state)

        if not self._should_show_hint(state):
            return None

        turns = state.get("_auto_memory_user_turn_count", 0) or 0

        hint = self._build_hint(
            turns,
            is_exit_followup=is_exit_followup,
            current_memory=current_memory,
        )
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
        current_memory = self._get_current_memory_contents()
        modified_request = self._inject_hint(
            request,
            is_exit_followup=is_exit_followup,
            current_memory=current_memory,
        )
        return handler(modified_request or request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Any,
    ) -> ModelResponse:
        """Async variant of wrap_model_call.

        Runs all blocking file I/O (exit-marker stat/read/unlink and memory
        file reads) via ``asyncio.to_thread`` so that ``blockbuster`` does not
        raise ``BlockingError`` from the async event loop.

        Args:
            request: The model request being processed.
            handler: The async handler function to call.

        Returns:
            The model response from the handler.
        """
        import asyncio

        def _io_in_thread() -> tuple[bool, str]:
            """Check/consume exit marker and read memory files in one thread."""
            is_followup = self._check_and_mark_exit_followup()
            if is_followup:
                _consume_exit_marker()
            current_memory = self._get_current_memory_contents()
            return is_followup, current_memory

        is_exit_followup, current_memory = await asyncio.to_thread(_io_in_thread)
        modified_request = self._inject_hint(
            request,
            is_exit_followup=is_exit_followup,
            current_memory=current_memory,
        )
        return await handler(modified_request or request)

    def after_agent(
        self,
        state: AutoMemoryState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        """Reset turn counter after a hint was injected and detect memory writes.

        If a hint was injected during this agent run (indicated by
        ``_auto_memory_hint_injected`` being True), resets the turn counter
        so the next cycle starts fresh.

        Detects memory file writes by comparing current mtimes against the
        snapshot taken in ``before_agent``, which is more reliable than
        scanning message content for path strings.

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

        changed_paths = self._memory_files_changed(self._pre_agent_hashes) if self._memory_paths else []
        if changed_paths:
            updates["memory_contents"] = None
            updates["_auto_memory_updated_paths"] = changed_paths
            logger.debug("Memory file content changed: %s", changed_paths)

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

        Reloads when ``memory_contents`` is absent or ``None`` (the sentinel
        set by ``AutoMemoryMiddleware.after_agent`` to trigger a refresh).

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update with memory_contents populated, or None to skip.
        """
        if state.get("memory_contents") is None:
            logger.debug("Refreshing memory contents")
            return self._memory_middleware.before_agent(state, runtime, None)
        return None

    async def abefore_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        """Async load memory content before agent execution.

        Reloads when ``memory_contents`` is absent or ``None`` (the sentinel
        set by ``AutoMemoryMiddleware.after_agent`` to trigger a refresh).

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update with memory_contents populated, or None to skip.
        """
        if state.get("memory_contents") is None:
            logger.debug("Refreshing memory contents (async)")
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
