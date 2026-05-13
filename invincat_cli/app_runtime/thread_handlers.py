"""App-bound thread history handlers."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from langchain_core.runnables import RunnableConfig
from textual.containers import Container, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches

from invincat_cli.app_runtime.state import ThreadHistoryPayload
from invincat_cli.app_runtime.thread_history import (
    build_resume_summary,
    merge_thread_state_with_fallback,
    thread_history_payload_from_state_values,
)
from invincat_cli.app_runtime.thread_links import build_thread_message
from invincat_cli.app_runtime.thread_runtime import (
    ThreadSwitchSnapshot,
    capture_thread_switch_snapshot,
    should_handle_thread_switch_error_as_prefetch_failure,
    thread_loading_status,
    thread_resume_block_message_key,
    thread_resume_block_reason,
    thread_switch_banner_update,
    thread_switch_failed_message,
    thread_switch_failure_log,
    thread_switch_prefetch_failure_log,
    thread_switch_rollback_banner_update,
    thread_switch_rollback_restore_failure_log,
)
from invincat_cli.i18n import t
from invincat_cli.widgets.messages import AppMessage, AssistantMessage

logger = logging.getLogger(__name__)


async def get_thread_state_values(
    app: Any,  # noqa: ANN401
    thread_id: str,
) -> dict[str, Any]:
    """Fetch thread state values, with remote checkpointer fallback."""
    if not app._agent:
        return {}

    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    state = await app._agent.aget_state(config)

    values: dict[str, Any] = {}
    if state and state.values:
        values = dict(state.values)

    messages = values.get("messages")
    if isinstance(messages, list) and messages:
        return values
    if not app._remote_agent():
        return values

    logger.debug(
        "Remote state empty for thread %s; falling back to local checkpointer",
        thread_id,
    )
    fallback_values = await app._read_channel_values_from_checkpointer(thread_id)
    return merge_thread_state_with_fallback(values, fallback_values)


async def fetch_thread_history_data(
    app: Any,  # noqa: ANN401
    thread_id: str,
) -> ThreadHistoryPayload:
    """Fetch and convert stored messages for a thread."""
    state_values = await app._get_thread_state_values(thread_id)
    return await asyncio.to_thread(
        thread_history_payload_from_state_values,
        state_values,
    )


async def read_channel_values_from_checkpointer(thread_id: str) -> dict[str, Any]:
    """Read checkpoint channel values directly from the SQLite checkpointer."""
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        from invincat_cli.sessions import get_db_path

        db_path = str(get_db_path())
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
            tup = await saver.aget_tuple(config)
            if tup and tup.checkpoint:
                channel_values = tup.checkpoint.get("channel_values", {})
                if isinstance(channel_values, dict):
                    return dict(channel_values)
    except (ImportError, OSError) as exc:
        logger.warning(
            "Failed to read checkpointer directly for %s: %s",
            thread_id,
            exc,
        )
    except Exception:
        logger.warning(
            "Unexpected error reading checkpointer for %s",
            thread_id,
            exc_info=True,
        )
    return {}


async def upgrade_thread_message_link(
    widget: AppMessage,
    *,
    prefix: str,
    thread_id: str,
) -> None:
    """Upgrade a plain thread message to a linked one when URL resolves."""
    try:
        thread_msg = await build_thread_message(prefix, thread_id)
        if not isinstance(thread_msg, Content):
            logger.debug(
                "Skipping thread link upgrade for %s: URL did not resolve",
                thread_id,
            )
            return
        if widget.parent is None:
            logger.debug(
                "Skipping thread link upgrade for %s: widget no longer mounted",
                thread_id,
            )
            return
        widget._content = thread_msg
        widget.update(thread_msg)
    except Exception:
        logger.warning(
            "Failed to upgrade thread message link for %s",
            thread_id,
            exc_info=True,
        )


def schedule_thread_message_link(
    app: Any,  # noqa: ANN401
    widget: AppMessage,
    *,
    prefix: str,
    thread_id: str,
) -> None:
    """Schedule thread URL link resolution and apply updates in the background."""
    app.run_worker(
        app._upgrade_thread_message_link(
            widget,
            prefix=prefix,
            thread_id=thread_id,
        ),
        exclusive=False,
    )


async def load_thread_history(
    app: Any,  # noqa: ANN401
    *,
    thread_id: str | None = None,
    preloaded_payload: ThreadHistoryPayload | None = None,
) -> None:
    """Load and render message history when resuming a thread."""
    history_thread_id = thread_id or app._lc_thread_id
    if not history_thread_id:
        logger.debug("Skipping history load: no thread ID available")
        return
    if preloaded_payload is None and not app._agent:
        logger.debug(
            "Skipping history load for %s: no active agent and no preloaded data",
            history_thread_id,
        )
        return

    try:
        payload = (
            preloaded_payload
            if preloaded_payload is not None
            else await app._fetch_thread_history_data(history_thread_id)
        )
        if not payload.messages:
            return

        if payload.context_tokens > 0:
            app._on_tokens_update(payload.context_tokens)

        _archived, visible = app._message_store.bulk_load(payload.messages)

        if app._status_bar:
            app._status_bar.set_message_count(app._message_store.total_count)

        try:
            messages_container = app.query_one("#messages", Container)
        except NoMatches:
            return

        widgets = [msg_data.to_widget() for msg_data in visible]
        if widgets:
            await messages_container.mount(*widgets)

        assistant_updates = [
            widget.set_content(msg_data.content)
            for widget, msg_data in zip(widgets, visible, strict=False)
            if isinstance(widget, AssistantMessage) and msg_data.content
        ]
        if assistant_updates:
            assistant_results = await asyncio.gather(
                *assistant_updates,
                return_exceptions=True,
            )
            for error in assistant_results:
                if isinstance(error, Exception):
                    logger.warning(
                        "Failed to render assistant history message for %s: %s",
                        history_thread_id,
                        error,
                    )

        summary = build_resume_summary(payload.messages, payload.context_tokens)
        if summary:
            await app._mount_message(AppMessage(summary))

        thread_msg_widget = AppMessage(
            t("thread.resumed").format(thread_id=history_thread_id)
        )
        await app._mount_message(thread_msg_widget)
        app._schedule_thread_message_link(
            thread_msg_widget,
            prefix="Resumed thread",
            thread_id=history_thread_id,
        )

        def scroll_to_end() -> None:
            with suppress(NoMatches):
                chat = app.query_one("#chat", VerticalScroll)
                chat.scroll_end(animate=False, immediate=True)

        app.set_timer(0.1, scroll_to_end)

    except Exception as exc:
        logger.exception(
            "Failed to load thread history for %s",
            history_thread_id,
        )
        await app._mount_message(
            AppMessage(t("thread.history_load_failed").format(error=str(exc)))
        )


async def reset_thread_conversation_view(app: Any) -> None:  # noqa: ANN401
    """Clear visible conversation state before loading another thread."""
    app._pending_messages.clear()
    app._queued_widgets.clear()
    await app._clear_messages()
    app._context_tokens = 0
    app._tokens_approximate = False
    app._update_tokens(0)
    app._update_status("")


def apply_thread_switch_ids(app: Any, thread_id: str) -> None:  # noqa: ANN401
    """Apply active thread IDs and update the welcome banner."""
    assert app._session_state is not None

    app._session_state.thread_id = thread_id
    app._lc_thread_id = thread_id
    banner_update = thread_switch_banner_update(thread_id)
    app._update_welcome_banner(
        banner_update.thread_id,
        missing_message=banner_update.missing_message,
        warn_if_missing=banner_update.warn_if_missing,
    )


def rollback_thread_switch_ids(
    app: Any,  # noqa: ANN401
    snapshot: ThreadSwitchSnapshot,
) -> None:
    """Restore active thread IDs from a pre-switch snapshot."""
    assert app._session_state is not None

    app._session_state.thread_id = snapshot.session_thread_id
    app._lc_thread_id = snapshot.lc_thread_id
    banner_update = thread_switch_rollback_banner_update(
        snapshot.session_thread_id,
    )
    app._update_welcome_banner(
        banner_update.thread_id,
        missing_message=banner_update.missing_message,
        warn_if_missing=banner_update.warn_if_missing,
    )


async def restore_previous_thread_after_failed_switch(
    app: Any,  # noqa: ANN401
    *,
    snapshot: ThreadSwitchSnapshot,
    failed_thread_id: str,
) -> bool:
    """Try to restore the previous thread view after a failed switch."""
    try:
        await app._clear_messages()
        await app._load_thread_history(thread_id=snapshot.session_thread_id)
    except Exception:
        logger.warning(
            thread_switch_rollback_restore_failure_log(failed_thread_id),
            exc_info=True,
        )
        return False
    return True


async def resume_thread(app: Any, thread_id: str) -> None:  # noqa: ANN401
    """Resume a previously saved thread."""
    block_reason = thread_resume_block_reason(
        has_agent=app._agent is not None,
        has_session=app._session_state is not None,
        current_thread_id=(
            app._session_state.thread_id if app._session_state else None
        ),
        requested_thread_id=thread_id,
        switching=app._thread_switching,
    )
    if block_reason is not None:
        await app._mount_message(
            AppMessage(
                t(thread_resume_block_message_key(block_reason)).format(
                    thread_id=thread_id
                )
            )
        )
        return

    assert app._session_state is not None

    snapshot = capture_thread_switch_snapshot(
        lc_thread_id=app._lc_thread_id,
        session_thread_id=app._session_state.thread_id,
    )
    app._thread_switching = True
    if app._chat_input:
        app._chat_input.set_cursor_active(active=False)

    prefetched_payload: ThreadHistoryPayload | None = None
    try:
        app._update_status(thread_loading_status(thread_id))
        prefetched_payload = await app._fetch_thread_history_data(thread_id)

        await app._reset_thread_conversation_view()
        app._apply_thread_switch_ids(thread_id)
        await app._load_thread_history(
            thread_id=thread_id,
            preloaded_payload=prefetched_payload,
        )
    except Exception as exc:
        if should_handle_thread_switch_error_as_prefetch_failure(
            has_prefetched_payload=prefetched_payload is not None,
        ):
            logger.exception(thread_switch_prefetch_failure_log(thread_id))
            await app._mount_message(
                AppMessage(
                    thread_switch_failed_message(
                        thread_id=thread_id,
                        error=exc,
                    )
                )
            )
            return
        logger.exception(thread_switch_failure_log(thread_id))
        app._rollback_thread_switch_ids(snapshot)
        rollback_restored = await app._restore_previous_thread_after_failed_switch(
            snapshot=snapshot,
            failed_thread_id=thread_id,
        )
        await app._mount_message(
            AppMessage(
                thread_switch_failed_message(
                    thread_id=thread_id,
                    error=exc,
                    rollback_restore_failed=not rollback_restored,
                )
            )
        )
    finally:
        app._thread_switching = False
        app._update_status("")
        if app._chat_input:
            app._chat_input.set_cursor_active(active=not app._agent_running)
