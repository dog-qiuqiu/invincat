"""App-bound server startup and resume handlers."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any, cast

from textual.css.query import NoMatches

from invincat_cli.app_runtime.input_handlers import handle_user_message
from invincat_cli.app_runtime.server import (
    count_mcp_tools,
    normalize_server_start_error,
    resolve_mcp_preload_result,
    resolve_most_recent_agent_filter,
    resolve_no_recent_threads_notice,
    resolve_thread_not_found_notice,
    should_drain_deferred_on_server_ready,
    should_drain_queue_on_server_ready,
    should_update_default_agent_from_thread,
)
from invincat_cli.app_runtime.startup import resolve_startup_followup
from invincat_cli.app_runtime.thread_handlers import load_thread_history
from invincat_cli.i18n import t
from invincat_cli.widgets.messages import ErrorMessage
from invincat_cli.widgets.welcome import WelcomeBanner

logger = logging.getLogger(__name__)


async def resolve_resume_thread(app: Any) -> None:  # noqa: ANN401
    """Resolve a resume intent into a concrete thread ID."""
    from invincat_cli.sessions import (
        find_similar_threads,
        generate_thread_id,
        get_most_recent,
        get_thread_agent,
        thread_exists,
    )

    resume = app._resume_thread_intent
    app._resume_thread_intent = None

    if not resume:
        return

    try:
        if resume == "__MOST_RECENT__":
            agent_filter = resolve_most_recent_agent_filter(
                assistant_id=app._assistant_id
            )
            thread_id = await get_most_recent(agent_filter)
            if thread_id:
                agent_name = await get_thread_agent(thread_id)
                if agent_name:
                    app._assistant_id = agent_name
                    if app._server_kwargs:
                        app._server_kwargs["assistant_id"] = agent_name
                app._lc_thread_id = thread_id
            else:
                app._lc_thread_id = generate_thread_id()
                notice = resolve_no_recent_threads_notice(agent_filter)
                msg = t(notice.key, **notice.params)
                app.notify(msg, severity="warning", markup=False)
        elif await thread_exists(resume):
            app._lc_thread_id = resume
            if should_update_default_agent_from_thread(assistant_id=app._assistant_id):
                agent_name = await get_thread_agent(resume)
                if agent_name:
                    app._assistant_id = agent_name
                    if app._server_kwargs:
                        app._server_kwargs["assistant_id"] = agent_name
        else:
            app._lc_thread_id = generate_thread_id()
            similar = await find_similar_threads(resume)
            notice = resolve_thread_not_found_notice(
                thread_id=resume,
                similar=similar,
            )
            hint = t(notice.key, **notice.params)
            app.notify(hint, severity="warning", timeout=6, markup=False)
    except Exception:
        logger.exception("Failed to resolve resume thread %r", resume)
        app._lc_thread_id = generate_thread_id()
        app.notify(
            t("app.thread_lookup_failed"),
            severity="warning",
        )

    if app._session_state:
        app._session_state.thread_id = app._lc_thread_id


async def start_server_background(app: Any) -> None:  # noqa: ANN401
    """Resolve resume intent, start server, and post readiness messages."""
    if app._resume_thread_intent:
        await app._resolve_resume_thread()

    model_instance: Any | None = None
    if app._model_kwargs is not None:
        from invincat_cli.config import create_model
        from invincat_cli.model_config import ModelConfigError, save_recent_model

        try:
            result = create_model(**app._model_kwargs)
        except ModelConfigError as exc:
            app.post_message(app.ServerStartFailed(error=exc))
            return
        result.apply_to_settings()
        save_recent_model(f"{result.provider}:{result.model_name}")
        model_instance = result.model
        app._model_kwargs = None

    from invincat_cli.server.manager import start_server_and_get_agent

    coros: list[Any] = [start_server_and_get_agent(**app._server_kwargs)]

    if app._mcp_preload_kwargs is not None:
        from invincat_cli.main import _preload_session_mcp_server_info

        coros.append(_preload_session_mcp_server_info(**app._mcp_preload_kwargs))

    try:
        results = await asyncio.gather(*coros, return_exceptions=True)
    except Exception as exc:  # noqa: BLE001  # defensive catch around gather
        app.post_message(app.ServerStartFailed(error=exc))
        return

    server_result = results[0]
    server_error = normalize_server_start_error(server_result)
    if server_error is not None:
        app.post_message(app.ServerStartFailed(error=server_error))
        return

    agent, server_proc, _ = cast(tuple[Any, Any, Any], server_result)
    app._server_proc = server_proc

    mcp_preload = resolve_mcp_preload_result(results)
    if mcp_preload.error is not None:
        logger.warning(
            "MCP metadata preload failed: %s",
            mcp_preload.error,
            exc_info=mcp_preload.error,
        )

    app.post_message(
        app.ServerReady(
            agent=agent,
            server_proc=server_proc,
            mcp_server_info=mcp_preload.info,
            model=model_instance,
        )
    )


def handle_server_ready(app: Any, event: Any) -> None:  # noqa: ANN401
    """Handle successful background server startup."""
    app._connecting = False
    app._agent = event.agent
    app._server_proc = event.server_proc
    app._mcp_server_info = event.mcp_server_info
    app._mcp_tool_count = count_mcp_tools(event.mcp_server_info)
    if event.model is not None:
        app._model = event.model

    try:
        banner = app.query_one("#welcome-banner", WelcomeBanner)
        banner.set_connected(app._mcp_tool_count)
    except NoMatches:
        logger.warning("Welcome banner not found during server ready transition")

    followup = resolve_startup_followup(
        connecting=app._connecting,
        initial_prompt=app._initial_prompt,
        thread_id=app._lc_thread_id,
        agent=app._agent,
    )
    if followup and followup.kind == "submit_prompt" and followup.prompt is not None:
        app.call_after_refresh(
            lambda: asyncio.create_task(handle_user_message(app, followup.prompt))
        )
    elif followup and followup.kind == "load_history":
        app.call_after_refresh(lambda: asyncio.create_task(load_thread_history(app)))

    if should_drain_deferred_on_server_ready(
        deferred_action_count=len(app._deferred_actions),
        agent_running=app._agent_running,
    ):

        async def _safe_drain() -> None:
            try:
                await app._maybe_drain_deferred()
            except Exception:
                logger.exception("Unhandled error while draining deferred actions")
                with suppress(Exception):
                    await app._mount_message(
                        ErrorMessage(
                            "A deferred action failed during startup. "
                            "You may need to retry the operation."
                        )
                    )

        app.call_after_refresh(lambda: asyncio.create_task(_safe_drain()))

    if should_drain_queue_on_server_ready(
        pending_message_count=len(app._pending_messages),
        initial_prompt=app._initial_prompt,
    ):
        app.call_after_refresh(
            lambda: asyncio.create_task(app._process_next_from_queue())
        )


def handle_server_start_failed(app: Any, event: Any) -> None:  # noqa: ANN401
    """Handle background server startup failure."""
    app._connecting = False
    logger.error("Server startup failed: %s", event.error, exc_info=event.error)
    try:
        banner = app.query_one("#welcome-banner", WelcomeBanner)
        banner.set_failed(str(event.error))
    except NoMatches:
        logger.warning("Welcome banner not found during server failure transition")

    if app._pending_messages:
        app._pending_messages.clear()
        for widget in app._queued_widgets:
            widget.remove()
        app._queued_widgets.clear()
    app._deferred_actions.clear()
    app._pending_plan_handoff_prompt = None
