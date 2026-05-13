"""App-bound model selector and switch handlers."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from functools import partial
from typing import Any

from textual.app import ScreenStackError
from textual.containers import VerticalScroll
from textual.css.query import NoMatches

from invincat_cli.app_runtime.model_args import split_model_spec
from invincat_cli.app_runtime.model_runtime import (
    ResolvedModelSpec,
    already_using_model_display,
    can_start_deferred_server_for_model_switch,
    choose_default_model_clear_fn,
    choose_default_model_save_fn,
    is_target_already_using,
    missing_credentials_detail,
    model_switch_requires_server_error,
    model_switch_target_kwargs,
    model_status_fields,
    model_target_translation_key,
    normalize_default_model_spec,
    resolve_model_spec,
    should_primary_switch_update_memory_status,
    should_start_server_after_primary_model_switch,
)
from invincat_cli.app_runtime.state import DeferredAction
from invincat_cli.app_runtime.ui_actions import (
    resolve_model_selector_state,
    should_defer_modal_action,
)
from invincat_cli.i18n import t
from invincat_cli.model_config import ModelTarget
from invincat_cli.widgets.messages import AppMessage, ErrorMessage

logger = logging.getLogger(__name__)


async def show_model_selector(
    app: Any,  # noqa: ANN401
    *,
    target: ModelTarget = "primary",
    extra_kwargs: dict[str, Any] | None = None,
) -> None:
    """Show interactive model selector as a modal screen."""
    from invincat_cli.config import settings
    from invincat_cli.widgets.model_selector import ModelSelectorScreen

    selector_state = resolve_model_selector_state(
        settings_model_provider=settings.model_provider,
        settings_model_name=settings.model_name,
        memory_model_override=app._memory_model_override,
    )

    def handle_result(result: tuple[str, str, ModelTarget] | None) -> None:
        if result is not None:
            model_spec, _, selected_target = result
            action = partial(
                app._switch_model,
                model_spec,
                target=selected_target,
                extra_kwargs=extra_kwargs,
                persist_as_default=True,
            )
            if should_defer_modal_action(
                agent_running=app._agent_running,
                shell_running=app._shell_running,
                connecting=app._connecting,
            ):
                app._defer_action(
                    DeferredAction(
                        kind="model_switch",
                        execute=action,
                    )
                )
                app.notify(t("app.model_switch_pending"), timeout=3)
            else:
                app.call_later(action)
        if app._chat_input:
            app._chat_input.focus_input()

    screen = ModelSelectorScreen(
        current_model=selector_state.current_model,
        current_provider=selector_state.current_provider,
        current_memory_model=selector_state.memory_model,
        current_memory_provider=selector_state.memory_provider,
        initial_target=target,
        cli_profile_override=app._profile_override,
    )
    app.push_screen(screen, handle_result)


def start_server_after_primary_model_switch(
    app: Any,  # noqa: ANN401
    *,
    resolved: ResolvedModelSpec,
    target_kwargs: dict[str, Any] | None,
) -> None:
    """Update deferred server kwargs and start the background server."""
    assert app._server_kwargs is not None

    app._server_kwargs["model_name"] = resolved.display
    app._server_kwargs["model_params"] = target_kwargs
    app._model_kwargs = None
    app._defer_server_start = False
    app._connecting = True
    with suppress(NoMatches):
        banner = app.query_one("#welcome-banner")
        banner.set_connecting()
    app.run_worker(
        app._start_server_background,
        exclusive=True,
        group="server-startup",
    )


def apply_primary_model_status(app: Any, *, model_result: Any) -> None:  # noqa: ANN401
    """Update status-bar labels after switching the primary model."""
    if not app._status_bar:
        return

    status_model = model_status_fields(
        provider=model_result.provider,
        model_name=model_result.model_name,
    )
    app._status_bar.set_model(
        provider=status_model.provider,
        model=status_model.model,
    )
    if should_primary_switch_update_memory_status(
        memory_model_override=app._memory_model_override,
    ):
        app._status_bar.set_memory_model(
            provider=status_model.provider,
            model=status_model.model,
            follow_primary=True,
        )


async def apply_primary_model_switch(
    app: Any,  # noqa: ANN401
    *,
    resolved: ResolvedModelSpec,
    model_result: Any,
    target_kwargs: dict[str, Any] | None,
    remote_agent: Any,
    save_recent_model: Any,
) -> None:
    """Apply primary model switch side effects."""
    model_result.apply_to_settings()
    app._model_override = resolved.display
    app._model_params_override = target_kwargs
    app._invalidate_planner_agent_cache()
    if remote_agent is None:
        app._model = model_result.model

    app._apply_primary_model_status(model_result=model_result)

    if should_start_server_after_primary_model_switch(
        has_remote_agent=remote_agent is not None,
        has_server_kwargs=app._server_kwargs is not None,
    ):
        app._start_server_after_primary_model_switch(
            resolved=resolved,
            target_kwargs=target_kwargs,
        )

    if not await asyncio.to_thread(save_recent_model, resolved.display):
        await app._mount_message(ErrorMessage(t("model.preference_save_failed")))
    else:
        await app._mount_message(
            AppMessage(t("model.switched_to").format(model=resolved.display))
        )
    logger.info("Primary model switched to %s", resolved.display)


async def apply_memory_model_switch(
    app: Any,  # noqa: ANN401
    *,
    resolved: ResolvedModelSpec,
    model_result: Any,
    target_kwargs: dict[str, Any] | None,
) -> None:
    """Apply memory model switch side effects."""
    app._memory_model_override = resolved.display
    app._memory_model_params_override = target_kwargs
    status_model = model_status_fields(
        provider=model_result.provider,
        model_name=model_result.model_name,
    )
    if app._status_bar:
        app._status_bar.set_memory_model(
            provider=status_model.provider,
            model=status_model.model,
            follow_primary=False,
        )
    await app._mount_message(
        AppMessage(t("model.memory_switched_to").format(model=resolved.display))
    )
    logger.info("Memory model switched to %s", resolved.display)


async def switch_model(
    app: Any,  # noqa: ANN401
    model_spec: str,
    *,
    target: ModelTarget = "primary",
    extra_kwargs: dict[str, Any] | None = None,
    persist_as_default: bool = False,
) -> None:
    """Switch to a new model, preserving conversation history."""
    from invincat_cli.config import create_model, detect_provider, settings
    from invincat_cli.model_config import (
        clear_caches,
        get_credential_env_var,
        get_target_model_params,
        has_provider_credentials,
        save_recent_model,
    )

    logger.info("Switching %s model to %s", target, model_spec)

    if app._model_switching:
        await app._mount_message(AppMessage(t("model.switch_in_progress")))
        return

    app._model_switching = True
    try:
        current_model_name = settings.model_name
        current_model_provider = settings.model_provider

        clear_caches()

        resolved = resolve_model_spec(
            model_spec,
            detect_provider=detect_provider,
        )

        has_creds = (
            has_provider_credentials(resolved.provider)
            if resolved.provider
            else None
        )
        if has_creds is False and resolved.provider is not None:
            detail = missing_credentials_detail(
                resolved.provider,
                get_credential_env_var=get_credential_env_var,
            )
            await app._mount_message(
                ErrorMessage(t("model.missing_credentials").format(detail=detail))
            )
            return
        if has_creds is None and resolved.provider:
            logger.debug(
                "Credentials for provider '%s' cannot be verified; proceeding anyway",
                resolved.provider,
            )

        target_kwargs = model_switch_target_kwargs(
            extra_kwargs=extra_kwargs,
            saved_kwargs=get_target_model_params(target, resolved.display),
        )

        remote_agent = app._remote_agent()
        can_start_deferred_server = can_start_deferred_server_for_model_switch(
            target=target,
            has_server_kwargs=app._server_kwargs is not None,
            connecting=app._connecting,
        )
        if model_switch_requires_server_error(
            has_remote_agent=remote_agent is not None,
            can_start_deferred_server=can_start_deferred_server,
        ):
            await app._mount_message(ErrorMessage(t("model.switch_requires_server")))
            return

        if is_target_already_using(
            target=target,
            resolved=resolved,
            current_provider=current_model_provider,
            current_model_name=current_model_name,
            memory_model_override=app._memory_model_override,
        ):
            current = already_using_model_display(
                target=target,
                resolved=resolved,
                current_provider=current_model_provider,
                current_model_name=current_model_name,
            )
            await app._mount_message(
                AppMessage(t("model.already_using").format(model=current))
            )
            return

        try:
            model_result = create_model(
                resolved.display,
                extra_kwargs=target_kwargs,
                profile_overrides=app._profile_override,
            )
        except Exception as exc:
            logger.exception(
                "Failed to resolve model metadata for %s",
                resolved.display,
            )
            await app._mount_message(
                ErrorMessage(t("model.switch_failed").format(error=str(exc)))
            )
            return

        if target == "primary":
            await app._apply_primary_model_switch(
                resolved=resolved,
                model_result=model_result,
                target_kwargs=target_kwargs,
                remote_agent=remote_agent,
                save_recent_model=save_recent_model,
            )
        else:
            await app._apply_memory_model_switch(
                resolved=resolved,
                model_result=model_result,
                target_kwargs=target_kwargs,
            )

        if persist_as_default:
            await app._set_default_model(
                resolved.display,
                target=target,
                announce=False,
            )

        with suppress(NoMatches, ScreenStackError):
            app.query_one("#chat", VerticalScroll).anchor()
    finally:
        app._model_switching = False


async def set_default_model(
    app: Any,  # noqa: ANN401
    model_spec: str,
    *,
    target: ModelTarget = "primary",
    announce: bool = True,
    apply_to_session: bool = False,
) -> bool:
    """Set the default model target in config without switching session."""
    from invincat_cli.config import detect_provider
    from invincat_cli.model_config import (
        save_default_model,
        save_memory_default_model,
    )

    model_spec = normalize_default_model_spec(
        model_spec,
        detect_provider=detect_provider,
    )

    save_fn = choose_default_model_save_fn(
        target,
        save_default_model=save_default_model,
        save_memory_default_model=save_memory_default_model,
    )
    target_label = t(model_target_translation_key(target))

    if await asyncio.to_thread(save_fn, model_spec):
        if apply_to_session and target == "memory":
            app._memory_model_override = model_spec
            app._memory_model_params_override = None
            if app._status_bar:
                mem_provider, mem_model = split_model_spec(model_spec)
                app._status_bar.set_memory_model(
                    provider=mem_provider,
                    model=mem_model,
                    follow_primary=False,
                )
        if announce:
            await app._mount_message(
                AppMessage(
                    t("model.default_target_set_to").format(
                        target=target_label, spec=model_spec
                    )
                )
            )
        return True

    if announce:
        await app._mount_message(
            ErrorMessage(t("model.failed_target_save").format(target=target_label))
        )
    return False


async def clear_default_model(
    app: Any,  # noqa: ANN401
    *,
    target: ModelTarget = "primary",
) -> None:
    """Remove default model target from config."""
    from invincat_cli.model_config import (
        clear_default_model as clear_primary_default_model,
    )
    from invincat_cli.model_config import (
        clear_memory_default_model,
    )

    clear_fn = choose_default_model_clear_fn(
        target,
        clear_default_model=clear_primary_default_model,
        clear_memory_default_model=clear_memory_default_model,
    )
    target_label = t(model_target_translation_key(target))

    if await asyncio.to_thread(clear_fn):
        await app._mount_message(
            AppMessage(t("model.default_target_cleared").format(target=target_label))
        )
    else:
        await app._mount_message(
            ErrorMessage(t("model.failed_target_clear").format(target=target_label))
        )
