"""Middleware for injecting local context into system prompt.

Detects git state, project structure, package managers, runtimes, and
directory layout by running a bash script via the backend. Because the
script executes inside the backend (local shell or remote sandbox), the
same detection logic works regardless of where the agent runs.
"""

from __future__ import annotations

import asyncio
import logging
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    NotRequired,
    Protocol,
    cast,
    runtime_checkable,
)

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
)

from invincat_cli.local_context.script import (
    DETECT_CONTEXT_SCRIPT as DETECT_CONTEXT_SCRIPT,
)
from invincat_cli.local_context.script import (
    build_detect_script as build_detect_script,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from deepagents.backends.protocol import ExecuteResponse
    from deepagents.middleware.summarization import SummarizationEvent
    from langgraph.runtime import Runtime

    from invincat_cli.mcp.tools import MCPServerInfo


@runtime_checkable
class _ExecutableBackend(Protocol):
    """Any backend that supports `execute(command) -> ExecuteResponse`."""

    def execute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResponse: ...


@runtime_checkable
class _AsyncExecutableBackend(Protocol):
    """Any backend that provides an async `aexecute` method."""

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,  # noqa: ASYNC109  # Timeout is forwarded to backend, not used as asyncio timeout
    ) -> ExecuteResponse: ...


logger = logging.getLogger(__name__)


_DETECT_SCRIPT_TIMEOUT = 30
"""Timeout in seconds for the environment detection script."""


def _build_mcp_context(servers: list[MCPServerInfo]) -> str:
    from invincat_cli.local_context.mcp import build_mcp_context

    return build_mcp_context(servers)


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------


class LocalContextState(AgentState):
    """State for local context middleware."""

    local_context: NotRequired[str]
    """Formatted local context: cwd, project, package managers,
    runtimes, git, test command, files, tree, Makefile.
    """

    _local_context_refreshed_at_cutoff: NotRequired[Annotated[int, PrivateStateAttr]]
    """Cutoff index of the summarization event we last refreshed for.

    Stored in LangGraph checkpointed state (isolated per thread) and private
    (not exposed to subagents via `PrivateStateAttr`). Used to avoid redundant
    re-runs of the detection script for the same summarization event.
    """


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class LocalContextMiddleware(AgentMiddleware):
    """Inject local context (git state, project structure, etc.) into the system prompt.

    Runs a bash detection script via `backend.execute()` on first interaction
    and again after each summarization event, stores the result in state, and
    appends it to the system prompt on every model call.

    Because the script runs inside the backend, it works for both local shells
    and remote sandboxes.
    """

    state_schema = LocalContextState

    def __init__(
        self,
        backend: _ExecutableBackend | _AsyncExecutableBackend,
        *,
        mcp_server_info: list[MCPServerInfo] | None = None,
    ) -> None:
        """Initialize with a backend that supports shell execution.

        Args:
            backend: Backend instance that provides shell command execution.
            mcp_server_info: MCP server metadata to include in the system prompt.
        """
        self.backend = backend
        self._mcp_context = _build_mcp_context(mcp_server_info or [])

    @staticmethod
    def _handle_detect_result(result: ExecuteResponse) -> str | None:
        """Validate detection script output and normalize it for state storage.

        Args:
            result: Execution result from the backend.

        Returns:
            Stripped script output, or `None` on failure/empty output.
        """
        output = result.output.strip() if result.output else ""
        if result.exit_code is None or result.exit_code != 0:
            logger.warning(
                "Local context detection script %s; "
                "context will be omitted. Output: %.200s",
                f"exited with code {result.exit_code}"
                if result.exit_code is not None
                else "did not report an exit code",
                output or "(empty)",
            )
            return None
        if not output:
            logger.debug(
                "Local context detection script succeeded but produced no output"
            )
        return output or None

    def _run_detect_script(self) -> str | None:
        """Run the environment detection script.

        Returns:
            Stripped script output, or `None` on failure/empty output.
        """
        backend = self.backend
        if not isinstance(backend, _ExecutableBackend):
            logger.debug(
                "Skipping sync local context detection; backend %s only "
                "supports async execution",
                type(backend).__name__,
            )
            return None
        try:
            result = backend.execute(
                DETECT_CONTEXT_SCRIPT, timeout=_DETECT_SCRIPT_TIMEOUT
            )
        except NotImplementedError:
            # Expected for async-only backends (e.g. HarborSandbox) that
            # define a stub execute() raising NotImplementedError.
            logger.debug(
                "Backend %s does not support sync execute; "
                "context detection deferred to async path",
                type(backend).__name__,
            )
            return None
        except Exception:
            logger.warning(
                "Local context detection failed (backend: %s); context will "
                "be omitted from system prompt",
                type(backend).__name__,
                exc_info=True,
            )
            return None

        return LocalContextMiddleware._handle_detect_result(result)

    # override - state parameter is intentionally narrowed from
    # AgentState to LocalContextState for type safety within this middleware.
    def before_agent(  # type: ignore[override]
        self,
        state: LocalContextState,
        runtime: Runtime,  # noqa: ARG002  # Required by interface but not used in local context
    ) -> dict[str, Any] | None:
        """Run context detection on first interaction and refresh after summarization.

        On the first invocation, runs the detection script and stores the result.
        After a summarization event (indicated by a new `_summarization_event`
        in state), re-runs the script to capture any environment changes that
        occurred during the session.

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update with `local_context` populated on success. On a
                post-summarization refresh failure, returns a state update
                recording the cutoff (without `local_context`) to prevent
                retry loops.

                Returns `None` if context is already set and no refresh is
                needed, or if initial detection fails.
        """
        # --- Post-summarization refresh ---
        # _summarization_event is a private field from SummarizationState.
        # At runtime the merged state dict contains all middleware fields;
        # accessed as untyped dict value because LocalContextState does not
        # (and should not) redeclare it.
        raw_event = state.get("_summarization_event")
        if raw_event is not None:
            event: SummarizationEvent = raw_event
            cutoff = event.get("cutoff_index")
            refreshed_cutoff = state.get("_local_context_refreshed_at_cutoff")
            if cutoff != refreshed_cutoff:
                output = self._run_detect_script()
                if output:
                    return {
                        "local_context": output,
                        "_local_context_refreshed_at_cutoff": cutoff,
                    }
                # Script failed — record cutoff to avoid retry loop,
                # keep existing local_context.
                return {"_local_context_refreshed_at_cutoff": cutoff}

        # --- Initial detection (first invocation) ---
        if state.get("local_context"):
            return None

        output = self._run_detect_script()
        if output:
            return {"local_context": output}
        return None

    async def _arun_detect_script(self) -> str | None:
        """Run the environment detection script asynchronously.

        Prefers `aexecute` when the backend implements `_AsyncExecutableBackend`.
        Falls back to running the sync detection script in a thread pool
        for sync-only backends.

        Returns:
            Stripped script output, or `None` on failure/empty output.
        """
        backend = self.backend
        if not (
            isinstance(backend, _AsyncExecutableBackend)
            and asyncio.iscoroutinefunction(backend.aexecute)
        ):
            try:
                return await asyncio.to_thread(self._run_detect_script)
            except Exception:
                logger.warning(
                    "Local context detection via sync fallback failed "
                    "(backend: %s); context will be omitted from system prompt",
                    type(backend).__name__,
                    exc_info=True,
                )
                return None
        try:
            result = await backend.aexecute(
                DETECT_CONTEXT_SCRIPT, timeout=_DETECT_SCRIPT_TIMEOUT
            )
        except Exception:
            logger.warning(
                "Local context detection failed (backend: %s); context will "
                "be omitted from system prompt",
                type(backend).__name__,
                exc_info=True,
            )
            return None

        return LocalContextMiddleware._handle_detect_result(result)

    async def abefore_agent(  # type: ignore[override]
        self,
        state: LocalContextState,
        runtime: Runtime,  # noqa: ARG002  # Required by interface but not used in local context
    ) -> dict[str, Any] | None:
        """Async variant of `before_agent` for use in async execution contexts.

        Args:
            state: Current agent state.
            runtime: Runtime context.

        Returns:
            State update with `local_context` populated on success. On a
                post-summarization refresh failure, returns a state update
                recording the cutoff (without `local_context`) to prevent
                retry loops.

                Returns `None` if context is already set and no refresh is
                needed, or if initial detection fails.
        """
        raw_event = state.get("_summarization_event")
        if raw_event is not None:
            event: SummarizationEvent = raw_event
            cutoff = event.get("cutoff_index")
            refreshed_cutoff = state.get("_local_context_refreshed_at_cutoff")
            if cutoff != refreshed_cutoff:
                output = await self._arun_detect_script()
                if output:
                    return {
                        "local_context": output,
                        "_local_context_refreshed_at_cutoff": cutoff,
                    }
                return {"_local_context_refreshed_at_cutoff": cutoff}

        if state.get("local_context"):
            return None

        output = await self._arun_detect_script()
        if output:
            return {"local_context": output}
        return None

    def _get_modified_request(self, request: ModelRequest) -> ModelRequest | None:
        """Append local context and MCP info to the system prompt if available.

        Args:
            request: The model request to potentially modify.

        Returns:
            Modified request with context appended, or `None`.
        """
        state = cast("LocalContextState", request.state)
        local_context = state.get("local_context", "")

        parts = [p for p in (local_context, self._mcp_context) if p]
        if not parts:
            return None

        system_prompt = request.system_prompt or ""
        new_prompt = system_prompt + "\n\n" + "\n\n".join(parts)
        return request.override(system_prompt=new_prompt)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Inject local context into system prompt.

        Args:
            request: The model request being processed.
            handler: The handler function to call with the modified request.

        Returns:
            The model response from the handler.
        """
        modified_request = self._get_modified_request(request)
        return handler(modified_request or request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """Inject local context into system prompt (async).

        Args:
            request: The model request being processed.
            handler: The async handler function to call with the modified request.

        Returns:
            The model response from the handler.
        """
        modified_request = self._get_modified_request(request)
        return await handler(modified_request or request)


__all__ = ["LocalContextMiddleware"]
