"""Remote agent client — thin wrapper around LangGraph's `RemoteGraph`.

Delegates streaming, state management, and SSE handling to
`langgraph.pregel.remote.RemoteGraph`. The only added logic is converting raw
message dicts from the server into LangChain message objects that the CLI's
Textual adapter expects.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from invincat_cli.core.debug import configure_debug_logging
from invincat_cli.remote.helpers import (
    convert_interrupts as _convert_interrupts,
)
from invincat_cli.remote.helpers import (
    prepare_config as _prepare_config,
)
from invincat_cli.remote.helpers import require_thread_id as _require_thread_id
from invincat_cli.remote.messages import MESSAGE_CONVERTERS as _MESSAGE_CONVERTERS
from invincat_cli.remote.messages import convert_ai_message as _convert_ai_message
from invincat_cli.remote.messages import convert_human_message as _convert_human_message
from invincat_cli.remote.messages import convert_message_data as _convert_message_data
from invincat_cli.remote.messages import convert_tool_message as _convert_tool_message

logger = logging.getLogger(__name__)
configure_debug_logging(logger)


class RemoteAgent:
    """Client that talks to a LangGraph server over HTTP+SSE.

    Wraps `langgraph.pregel.remote.RemoteGraph` which handles SSE parsing,
    stream-mode negotiation (`messages-tuple`), namespace extraction, and
    interrupt detection. This class adds only message-object conversion for the
    Textual adapter and thread-ID normalization.
    """

    def __init__(
        self,
        url: str,
        *,
        graph_name: str = "agent",
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Initialize the remote agent client.

        Args:
            url: Base URL of the LangGraph server.
            graph_name: Name of the graph on the server.
            api_key: API key for authenticated deployments.

                When `None`, `RemoteGraph` auto-reads `LANGGRAPH_API_KEY`,
                `LANGSMITH_API_KEY`, or `LANGCHAIN_API_KEY` from
                the environment.
            headers: Extra HTTP headers to include in every request
                (e.g. bearer tokens, proxy headers).
        """
        self._url = url
        self._graph_name = graph_name
        self._api_key = api_key
        self._headers = headers
        self._graph: Any = None

    def _get_graph(self) -> Any:  # noqa: ANN401
        """Lazily create the `RemoteGraph` instance.

        Returns:
            A `RemoteGraph` connected to the server.
        """
        if self._graph is None:
            from langgraph.pregel.remote import RemoteGraph

            self._graph = RemoteGraph(
                self._graph_name,
                url=self._url,
                api_key=self._api_key,
                headers=self._headers,
            )
        return self._graph

    async def astream(
        self,
        input: dict | Any,  # noqa: A002, ANN401
        *,
        stream_mode: list[str] | None = None,
        subgraphs: bool = False,
        config: dict[str, Any] | None = None,
        context: Any | None = None,  # noqa: ANN401
        durability: str | None = None,  # noqa: ARG002
    ) -> AsyncIterator[tuple[tuple[str, ...], str, Any]]:
        """Stream agent execution, yielding tuples matching Pregel's format.

        Delegates to `RemoteGraph.astream` (which handles `messages-tuple`
        negotiation, SSE routing, and namespace parsing) and converts the raw
        message dicts into LangChain message objects for the adapter.

        Args:
            input: The input to send (messages dict or Command).
            stream_mode: Stream modes to request.
            subgraphs: Whether to stream subgraph events.
            config: LangGraph config with `configurable.thread_id`, etc.
            context: Runtime context (e.g. `CLIContext`) forwarded to the
                server via the SDK's `context=` parameter.
            durability: Ignored (server manages durability).

        Yields:
            3-tuples of `(namespace, stream_mode, data)`.

        Raises:
            ValueError: If `thread_id` is not present in `config`.
        """  # noqa: DOC502 — raised by _require_thread_id
        from langchain_core.messages import BaseMessage

        _require_thread_id(config)

        graph = self._get_graph()
        config = _prepare_config(config)
        dropped_count = 0

        async for ns, mode, data in graph.astream(
            input,
            stream_mode=stream_mode or ["messages", "updates"],
            subgraphs=subgraphs,
            config=config,
            context=context,
        ):
            if mode != "messages":
                logger.debug("RemoteGraph event mode=%s ns=%s", mode, ns)

            if mode == "messages":
                msg_dict, meta = data
                if isinstance(msg_dict, dict):
                    msg_obj = _convert_message_data(msg_dict)
                    if msg_obj is not None:
                        yield (ns, "messages", (msg_obj, meta or {}))
                    else:
                        dropped_count += 1
                elif isinstance(msg_dict, BaseMessage):
                    # Already a LangChain message object (pre-deserialized)
                    yield (ns, "messages", (msg_dict, meta or {}))
                else:
                    logger.warning(
                        "Unexpected message data type in stream: %s",
                        type(msg_dict).__name__,
                    )
                continue

            if mode == "updates" and isinstance(data, dict):
                update_data = data
                if "__interrupt__" in data:
                    update_data = {
                        **data,
                        "__interrupt__": _convert_interrupts(data["__interrupt__"]),
                    }
                yield (ns, "updates", update_data)
                continue

            yield (ns, mode, data)

        if dropped_count:
            logger.warning(
                "Dropped %d message(s) during stream due to conversion failures",
                dropped_count,
            )

    async def aget_state(
        self,
        config: dict[str, Any],
    ) -> Any:  # noqa: ANN401
        """Get the current state of a thread.

        Returns `None` when the thread does not exist on the server (404).
        All other errors (network, auth, 500) are logged at WARNING and
        re-raised so callers can handle them.

        Args:
            config: Config with `configurable.thread_id`.

        Returns:
            Thread state object with `values` and `next` attributes, or `None`
                if the thread is not found.

        Raises:
            ValueError: If `thread_id` is not present in `config`.
        """  # noqa: DOC502 — raised by _require_thread_id
        from langgraph_sdk.errors import NotFoundError

        thread_id = _require_thread_id(config)

        graph = self._get_graph()
        try:
            return await graph.aget_state(_prepare_config(config))
        except NotFoundError:
            logger.debug("Thread %s not found on server", thread_id)
            return None
        except Exception:
            logger.warning(
                "Failed to get state for thread %s", thread_id, exc_info=True
            )
            raise

    async def aupdate_state(
        self,
        config: dict[str, Any],
        values: dict[str, Any],
    ) -> None:
        """Update the state of a thread.

        Exceptions from the underlying graph (server/network errors) are logged
        at WARNING level and then re-raised so callers can handle them.

        Args:
            config: Config with `configurable.thread_id`.
            values: State values to update.

        Raises:
            ValueError: If `thread_id` is not present in `config`.
        """  # noqa: DOC502 — raised by _require_thread_id
        thread_id = _require_thread_id(config)

        graph = self._get_graph()
        try:
            await graph.aupdate_state(_prepare_config(config), values)
        except Exception:
            logger.warning(
                "Failed to update state for thread %s", thread_id, exc_info=True
            )
            raise

    async def aensure_thread(self, config: dict[str, Any]) -> None:
        """Ensure the remote thread record exists before mutating state.

        In the LangGraph dev server, checkpoint persistence and HTTP thread
        registration are separate. After a server restart, a thread may still
        have checkpointed state on disk while `POST /threads/{id}/state`
        returns 404 because the server has not yet materialized that thread in
        its live store.

        This method performs the idempotent HTTP-side registration with
        `if_exists='do_nothing'` so callers that recovered state from
        persistence can safely follow up with `aupdate_state`.

        Args:
            config: Config with `configurable.thread_id` and optional metadata.

        Raises:
            ValueError: If `thread_id` is not present in `config`.
        """  # noqa: DOC502 — raised by _require_thread_id
        _require_thread_id(config)

        graph = self._get_graph()
        prepared = _prepare_config(config)
        thread_id = prepared["configurable"]["thread_id"]
        metadata = prepared.get("metadata")
        thread_metadata = metadata if isinstance(metadata, dict) else None

        try:
            client = graph._validate_client()
            await client.threads.create(
                thread_id=thread_id,
                if_exists="do_nothing",
                metadata=thread_metadata,
                graph_id=self._graph_name,
            )
        except Exception:
            logger.warning(
                "Failed to ensure thread %s exists on remote server",
                thread_id,
                exc_info=True,
            )
            raise

    def with_config(self, config: dict[str, Any]) -> RemoteAgent:  # noqa: ARG002
        """Return self; config is passed per-call, not stored."""
        return self


__all__ = [
    "RemoteAgent",
    "_MESSAGE_CONVERTERS",
    "_convert_ai_message",
    "_convert_human_message",
    "_convert_interrupts",
    "_convert_message_data",
    "_convert_tool_message",
    "_prepare_config",
    "_require_thread_id",
]
