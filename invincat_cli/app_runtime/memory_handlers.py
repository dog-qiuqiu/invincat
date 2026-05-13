"""App-bound memory and offload handlers."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from invincat_cli.app_runtime.memory import (
    AUTO_OFFLOAD_COOLDOWN_SECONDS,
    AUTO_OFFLOAD_THRESHOLD,
    build_auto_offload_message,
    build_offload_budget_cache_key,
    build_offload_success_message,
    build_offload_threshold_not_met_message,
    format_memory_update_success,
    resolve_auto_offload_decision,
    resolve_memory_update_notification,
)
from invincat_cli.i18n import t
from invincat_cli.widgets.messages import AppMessage, ErrorMessage

logger = logging.getLogger(__name__)


async def get_conversation_token_count(app: Any) -> int | None:  # noqa: ANN401
    """Return the approximate conversation-only token count."""
    if not app._agent:
        return None
    try:
        from langchain_core.messages.utils import count_tokens_approximately

        config = {"configurable": {"thread_id": app._lc_thread_id}}
        state = await app._agent.aget_state(config)
        if not state or not state.values:
            return None
        messages = state.values.get("messages", [])
        if not messages:
            return None
        return count_tokens_approximately(messages)
    except Exception:
        logger.debug("Failed to retrieve conversation token count", exc_info=True)
        return None


async def maybe_auto_offload(app: Any) -> None:  # noqa: ANN401
    """Trigger offload automatically when the context window is nearly full."""
    from invincat_cli.config import settings

    decision = resolve_auto_offload_decision(
        tokens_approximate=app._tokens_approximate,
        now=time.monotonic(),
        cooldown_until=app._auto_offload_cooldown_until,
        context_tokens=app._context_tokens,
        context_limit=settings.model_context_limit,
        threshold=AUTO_OFFLOAD_THRESHOLD,
        cooldown_seconds=AUTO_OFFLOAD_COOLDOWN_SECONDS,
    )
    if decision is None:
        return

    await app._mount_message(AppMessage(build_auto_offload_message(decision)))
    await app._handle_offload()
    app._auto_offload_cooldown_until = decision.cooldown_until


async def maybe_notify_memory_update(app: Any) -> None:  # noqa: ANN401
    """Show a status bar notification when memory files were updated."""
    try:
        state_values = await app._get_thread_state_values(app._lc_thread_id)
        updated_paths = state_values.get("_auto_memory_updated_paths")
        notification = resolve_memory_update_notification(
            updated_paths,
            home=Path.home(),
        )
        if notification is None:
            return
        success_msg = format_memory_update_success(
            notification,
            single_template=t("status.memory_updated"),
            multiple_template=t("status.memory_updated_n"),
        )

        app._update_status(t("status.memory_updating"))
        if app._memory_status_clear_timer is not None:
            app._memory_status_clear_timer.stop()
        app._memory_status_clear_timer = app.set_timer(
            0.8, lambda msg=success_msg: app._on_memory_update_done(msg)
        )
    except Exception:
        logger.debug("Failed to check memory update state", exc_info=True)


def on_memory_update_done(app: Any, msg: str) -> None:  # noqa: ANN401
    """Transition from the in-progress memory status to the success message."""
    app._update_status(msg)
    if app._memory_status_clear_timer is not None:
        app._memory_status_clear_timer.stop()
    app._memory_status_clear_timer = app.set_timer(4.0, app._clear_memory_status)


def clear_memory_status(app: Any) -> None:  # noqa: ANN401
    """Clear the memory-update status bar message."""
    app._memory_status_clear_timer = None
    app._update_status("")


def resolve_offload_budget_str(app: Any) -> str | None:  # noqa: ANN401
    """Resolve the offload retention budget as a human-readable string."""
    from invincat_cli.config import create_model, settings

    cache_key = build_offload_budget_cache_key(
        model_provider=settings.model_provider or "",
        model_name=settings.model_name or "",
        model_context_limit=settings.model_context_limit,
        profile_override=app._profile_override,
    )
    if app._offload_budget_cache is not None:
        cached_key, cached_val = app._offload_budget_cache
        if cached_key == cache_key:
            return cached_val

    val: str | None = None
    try:
        from deepagents.middleware.summarization import (
            compute_summarization_defaults,
        )
        from invincat_cli.offload import format_offload_limit

        model_spec = f"{settings.model_provider}:{settings.model_name}"
        result = create_model(
            model_spec,
            profile_overrides=app._profile_override,
        )
        defaults = compute_summarization_defaults(result.model)

        val = format_offload_limit(
            defaults["keep"],
            settings.model_context_limit,
        )
    except Exception:
        logger.debug("Failed to compute offload budget string", exc_info=True)

    app._offload_budget_cache = (cache_key, val)
    return val


async def handle_offload(app: Any) -> None:  # noqa: ANN401
    """Offload older messages to free context window space."""
    from invincat_cli.config import settings
    from invincat_cli.offload import (
        OffloadModelError,
        OffloadThresholdNotMet,
        perform_offload,
    )

    if not app._agent or not app._lc_thread_id:
        await app._mount_message(AppMessage(t("offload.nothing_to_offload")))
        return

    if app._agent_running:
        await app._mount_message(AppMessage(t("offload.cannot_while_running")))
        return

    config: RunnableConfig = {"configurable": {"thread_id": app._lc_thread_id}}

    try:
        state_values = await app._get_thread_state_values(app._lc_thread_id)
    except Exception as exc:
        await app._mount_message(
            ErrorMessage(t("offload.failed_read_state").format(error=str(exc)))
        )
        return

    if not state_values:
        await app._mount_message(AppMessage(t("offload.nothing_to_offload")))
        return

    app._agent_running = True
    try:
        from invincat_cli.hooks import dispatch_hook
        from langchain_core.messages.utils import convert_to_messages

        await dispatch_hook("context.offload", {})
        await dispatch_hook("context.compact", {})
        await app._set_spinner(t("status.offloading"))

        raw_messages = state_values.get("messages", [])
        if raw_messages and isinstance(raw_messages[0], dict):
            raw_messages = convert_to_messages(raw_messages)

        prior_event = state_values.get("_summarization_event")
        if isinstance(prior_event, dict):
            summary_msg_raw = prior_event.get("summary_message")
            if isinstance(summary_msg_raw, dict):
                converted = convert_to_messages([summary_msg_raw])
                if converted:
                    prior_event = {**prior_event, "summary_message": converted[0]}

        result = await perform_offload(
            messages=raw_messages,
            prior_event=prior_event,
            thread_id=app._lc_thread_id,
            model_spec=(f"{settings.model_provider}:{settings.model_name}"),
            profile_overrides=app._profile_override,
            context_limit=settings.model_context_limit,
            total_context_tokens=app._context_tokens,
            backend=app._backend,
        )

        if isinstance(result, OffloadThresholdNotMet):
            await app._mount_message(
                AppMessage(
                    build_offload_threshold_not_met_message(
                        conversation_tokens=result.conversation_tokens,
                        total_context_tokens=result.total_context_tokens,
                        context_limit=result.context_limit,
                        budget_str=result.budget_str,
                    )
                )
            )
            return

        if result.offload_warning:
            await app._mount_message(ErrorMessage(result.offload_warning))

        if remote := app._remote_agent():
            await remote.aensure_thread(config)  # ty: ignore[invalid-argument-type]

        await app._agent.aupdate_state(
            config, {"_summarization_event": result.new_event}
        )

        await app._mount_message(
            AppMessage(
                build_offload_success_message(
                    messages_offloaded=result.messages_offloaded,
                    tokens_before=result.tokens_before,
                    tokens_after=result.tokens_after,
                    pct_decrease=result.pct_decrease,
                    messages_kept=result.messages_kept,
                )
            )
        )

        app._on_tokens_update(result.tokens_after)
        from invincat_cli.textual_adapter import _persist_context_tokens

        await _persist_context_tokens(app._agent, config, result.tokens_after)

    except OffloadModelError as exc:
        logger.warning("Offload model creation failed: %s", exc, exc_info=True)
        await app._mount_message(ErrorMessage(str(exc)))
    except Exception as exc:
        logger.exception("Offload failed")
        await app._mount_message(ErrorMessage(t("offload.failed").format(error=str(exc))))
    finally:
        app._agent_running = False
        try:
            await app._set_spinner(None)
        except Exception:
            logger.exception("Failed to dismiss spinner after offload")
