"""Action handlers for the model selector."""

from __future__ import annotations

import asyncio
import logging

from textual.containers import VerticalScroll
from textual.content import Content
from textual.widgets import Input, Static

from invincat_cli.i18n import t
from invincat_cli.model_config import (
    ModelConfig,
    get_available_models,
    get_model_profiles,
)
from invincat_cli.widgets.model_register import ModelRegisterScreen, ModelTarget

logger = logging.getLogger(__name__)


class ModelSelectorActionMixin:
    """Handle selector navigation and register/edit actions."""

    def _move_selection(self, delta: int) -> None:
        """Move selection by delta, updating only the affected widgets.

        Args:
            delta: Number of positions to move (-1 for up, +1 for down).
        """
        if not self._filtered_models or not self._option_widgets:
            return

        count = len(self._filtered_models)
        # Defensive second check: in theory the guard above is sufficient in
        # Python's single-threaded event loop, but if _filtered_models is ever
        # mutated synchronously between the guard and here (e.g. a direct
        # assignment from a reactive update), we avoid a ZeroDivisionError.
        if count == 0:
            return

        old_index = self._selected_index
        new_index = (old_index + delta) % count
        self._selected_index = new_index

        # Update the previously selected widget
        old_widget = self._option_widgets[old_index]
        old_widget.remove_class("model-option-selected")
        old_widget.update(
            self._format_option_label(
                old_widget.model_spec,
                selected=False,
                current=old_widget.model_spec == self._active_current_spec(),
                has_creds=old_widget.has_creds,
                status=self._get_model_status(old_widget.model_spec),
            )
        )

        # Update the newly selected widget
        new_widget = self._option_widgets[new_index]
        new_widget.add_class("model-option-selected")
        new_widget.update(
            self._format_option_label(
                new_widget.model_spec,
                selected=True,
                current=new_widget.model_spec == self._active_current_spec(),
                has_creds=new_widget.has_creds,
                status=self._get_model_status(new_widget.model_spec),
            )
        )

        # Scroll the selected item into view
        if new_index == 0:
            scroll_container = self.query_one(".model-list", VerticalScroll)
            scroll_container.scroll_home(animate=False)
        else:
            new_widget.scroll_visible()

        self._update_footer()

    def action_move_up(self) -> None:
        """Move selection up."""
        self._move_selection(-1)

    def action_move_down(self) -> None:
        """Move selection down."""
        self._move_selection(1)

    def _switch_target(self, target: ModelTarget) -> None:
        if self._target == target:
            return
        self._target = target
        if self._filtered_models:
            self._selected_index = self._find_current_model_index()
        self.call_after_refresh(self._update_display)
        self._refresh_title()
        self._restore_help_text()

    def action_target_primary(self) -> None:
        """Switch selector target to primary model config."""
        self._switch_target("primary")

    def action_target_memory(self) -> None:
        """Switch selector target to memory-model config."""
        self._switch_target("memory")

    def action_tab_complete(self) -> None:
        """Replace search text with the currently selected model spec."""
        if not self._filtered_models:
            return
        model_spec, _ = self._filtered_models[self._selected_index]
        filter_input = self.query_one("#model-filter", Input)
        filter_input.value = model_spec
        filter_input.cursor_position = len(model_spec)

    def _visible_page_size(self) -> int:
        """Return the number of model options that fit in one visual page.

        Returns:
            Number of model options per page, at least 1.
        """
        default_page_size = 10
        try:
            scroll = self.query_one(".model-list", VerticalScroll)
            height = scroll.size.height
        except Exception:  # noqa: BLE001  # Fallback to default page size on any widget query error
            return default_page_size
        if height <= 0:
            return default_page_size

        total_models = len(self._filtered_models)
        if total_models == 0:
            return default_page_size

        # Each provider header = 1 row + margin-top: 1 (first has margin 0)
        num_headers = len(self.query(".model-provider-header"))
        header_rows = max(0, num_headers * 2 - 1) if num_headers else 0
        total_rows = total_models + header_rows
        return max(1, int(height * total_models / total_rows))

    def action_page_up(self) -> None:
        """Move selection up by one visible page."""
        if not self._filtered_models:
            return
        page = self._visible_page_size()
        target = max(0, self._selected_index - page)
        delta = target - self._selected_index
        if delta != 0:
            self._move_selection(delta)

    def action_page_down(self) -> None:
        """Move selection down by one visible page."""
        if not self._filtered_models:
            return
        count = len(self._filtered_models)
        page = self._visible_page_size()
        target = min(count - 1, self._selected_index + page)
        delta = target - self._selected_index
        if delta != 0:
            self._move_selection(delta)

    def action_select(self) -> None:
        """Select the current model."""
        # If there are filtered results, always select the highlighted model
        if self._filtered_models:
            model_spec, provider = self._filtered_models[self._selected_index]
            self.dismiss((model_spec, provider, self._target))
            return

        # No matches - check if user typed a custom provider:model spec
        filter_input = self.query_one("#model-filter", Input)
        custom_input = filter_input.value.strip()

        if custom_input and ":" in custom_input:
            provider = custom_input.split(":", 1)[0]
            self.dismiss((custom_input, provider, self._target))
        elif custom_input:
            self.dismiss((custom_input, "", self._target))

    def _restore_help_text(self) -> None:
        """Restore the help text to its default content."""
        help_text = self._help_text()
        try:
            help_widget = self.query_one(".model-selector-help", Static)
            help_widget.update(help_text)
        except Exception:
            logger.debug("Failed to update model-selector help text", exc_info=True)

    async def action_register_model(self) -> None:
        """Open the model registration screen."""
        screen = ModelRegisterScreen(target=self._target)
        self.app.push_screen(screen, self._handle_register_result)

    async def action_edit_model(self) -> None:
        """Open the model registration screen prefilled for the selected model."""
        if not self._filtered_models:
            return

        model_spec, provider = self._filtered_models[self._selected_index]
        if provider not in ModelRegisterScreen.PROVIDER_OPTIONS:
            self.notify(
                t("model.edit_provider_unsupported", provider=provider),
                severity="error",
                timeout=6,
                markup=False,
            )
            return

        _, model_name = model_spec.split(":", 1)
        initial_values = await asyncio.to_thread(
            self._load_registration_values, provider, model_name, self._target
        )
        screen = ModelRegisterScreen(
            initial_values=initial_values,
            edit_mode=True,
            target=self._target,
        )
        self.app.push_screen(screen, self._handle_register_result)

    @staticmethod
    def _load_registration_values(
        provider: str, model_name: str, target: ModelTarget
    ) -> dict[str, str]:
        """Load saved registration form values for a configured model."""
        config = ModelConfig.load()
        params = config.get_kwargs(provider, model_name=model_name)
        target_params = config.get_target_model_params(
            target, f"{provider}:{model_name}"
        )
        profile = config.get_profile_overrides(provider, model_name=model_name)

        values = {
            "provider": provider,
            "model": model_name,
            "api_key_env": str(params.get("api_key_env") or ""),
            "base_url": str(params.get("base_url") or ""),
            "max_input_tokens": str(profile.get("max_input_tokens") or ""),
            "deepseek_thinking": "enabled",
            "deepseek_effort": "high",
        }

        behavior_params = {**params, **target_params}

        extra_body = behavior_params.get("extra_body")
        if isinstance(extra_body, dict):
            thinking = extra_body.get("thinking")
            if isinstance(thinking, dict):
                thinking_type = thinking.get("type")
                if thinking_type in {"enabled", "disabled"}:
                    values["deepseek_thinking"] = thinking_type

        effort = behavior_params.get("reasoning_effort")
        if effort in {"low", "medium", "high"}:
            values["deepseek_effort"] = str(effort)

        return values

    def _handle_register_result(self, result: tuple[str, str] | None) -> None:
        """Handle the result from the model registration screen.

        Args:
            result: Tuple of (provider, model_name) on success, None on cancel.
        """
        if result is None:
            return

        provider_name, model_name = result
        model_spec = f"{provider_name}:{model_name}"

        from invincat_cli.model_config import clear_caches

        clear_caches()

        self._all_models = [
            (f"{p}:{m}", p)
            for p, models in get_available_models().items()
            for m in models
        ]
        self._profiles = get_model_profiles(cli_override=self._cli_profile_override)

        self._filter_text = ""
        filter_input = self.query_one("#model-filter", Input)
        filter_input.value = ""

        self._update_filtered_list()

        for i, (spec, _) in enumerate(self._filtered_models):
            if spec == model_spec:
                self._selected_index = i
                break

        self.call_after_refresh(self._update_display)

        help_widget = self.query_one(".model-selector-help", Static)
        help_widget.update(
            Content.from_markup(
                f"[bold green]{t('model.register_success', spec=model_spec)}[/bold green]"
            )
        )
        self.set_timer(3.0, self._restore_help_text)
