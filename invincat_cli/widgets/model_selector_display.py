"""Display rendering helpers for the model selector."""

from __future__ import annotations

import logging

from textual.containers import VerticalScroll
from textual.content import Content
from textual.widgets import Static

from invincat_cli import theme
from invincat_cli.config import Glyphs, get_glyphs
from invincat_cli.i18n import t
from invincat_cli.model_config import ModelProfileEntry, has_provider_credentials
from invincat_cli.widgets.model_selector_option import ModelOption

logger = logging.getLogger(__name__)


class ModelSelectorDisplayMixin:
    """Render model options and detail footer content."""

    async def _update_display(self) -> None:
        """Render the model list grouped by provider.

        Performs a full DOM rebuild (removes all children, re-mounts).
        Arrow-key navigation uses `_move_selection` instead to avoid
        the cost of a full rebuild.
        """
        if not self._options_container:
            return

        await self._options_container.remove_children()
        self._option_widgets = []

        if not self._filtered_models:
            msg = t("model.loading") if not self._loaded else t("model.no_matching")
            await self._options_container.mount(Static(Content.styled(msg, "dim")))
            self._update_footer()
            return

        # Group by provider, preserving insertion order so models from the
        # same provider cluster together in the visual list.
        by_provider: dict[str, list[tuple[str, str]]] = {}
        for model_spec, provider in self._filtered_models:
            by_provider.setdefault(provider, []).append((model_spec, provider))

        # Rebuild _filtered_models to match the provider-grouped display
        # order. Without this, _filtered_models stays in score-sorted order
        # while _option_widgets follow provider-grouped order, causing
        # _update_footer to look up the wrong model for the highlighted
        # index.
        grouped_order: list[tuple[str, str]] = []
        for entries in by_provider.values():
            grouped_order.extend(entries)

        # Remap selected_index so the same model stays highlighted.
        old_spec = self._filtered_models[self._selected_index][0]
        self._filtered_models = grouped_order
        self._selected_index = next(
            (i for i, (s, _) in enumerate(grouped_order) if s == old_spec),
            0,
        )

        glyphs = get_glyphs()
        flat_index = 0
        selected_widget: ModelOption | None = None

        # Build current model spec for comparison
        current_spec = self._active_current_spec()

        # Resolve credentials upfront so the widget-building loop
        # stays focused on layout
        creds = {p: has_provider_credentials(p) for p in by_provider}

        # Collect all widgets first, then batch-mount once to avoid
        # individual DOM mutations per widget
        all_widgets: list[Static] = []

        for provider, model_entries in by_provider.items():
            # Provider header with credential indicator
            has_creds = creds[provider]
            if has_creds is True:
                cred_indicator = glyphs.checkmark
            elif has_creds is False:
                cred_indicator = f"{glyphs.warning} missing credentials"
            else:
                cred_indicator = f"{glyphs.question} credentials unknown"
            all_widgets.append(
                Static(
                    Content.from_markup(
                        "[bold]$provider[/bold] [dim]$cred[/dim]",
                        provider=provider,
                        cred=cred_indicator,
                    ),
                    classes="model-provider-header",
                )
            )

            for model_spec, _prov in model_entries:
                is_current = model_spec == current_spec
                is_selected = flat_index == self._selected_index

                classes = "model-option"
                if is_selected:
                    classes += " model-option-selected"
                if is_current:
                    classes += " model-option-current"

                label = self._format_option_label(
                    model_spec,
                    selected=is_selected,
                    current=is_current,
                    has_creds=has_creds,
                    status=self._get_model_status(model_spec),
                )
                widget = ModelOption(
                    label=label,
                    model_spec=model_spec,
                    provider=provider,
                    index=flat_index,
                    has_creds=has_creds,
                    classes=classes,
                )
                all_widgets.append(widget)
                self._option_widgets.append(widget)

                if is_selected:
                    selected_widget = widget

                flat_index += 1

        await self._options_container.mount(*all_widgets)

        # Scroll the selected item into view without animation so the list
        # appears already scrolled to the current model on first paint.
        if selected_widget:
            if self._selected_index == 0:
                # First item: scroll to top so header is visible
                scroll_container = self.query_one(".model-list", VerticalScroll)
                scroll_container.scroll_home(animate=False)
            else:
                selected_widget.scroll_visible(animate=False)

        self._update_footer()

    @staticmethod
    def _format_option_label(
        model_spec: str,
        *,
        selected: bool,
        current: bool,
        has_creds: bool | None,
        status: str | None = None,
    ) -> Content:
        """Build the display label for a model option.

        Args:
            model_spec: The `provider:model` string.
            selected: Whether this option is currently highlighted.
            current: Whether this is the active model.
            has_creds: Credential status (True/False/None).
            status: Model status from profile (e.g., `'deprecated'`,
                `'beta'`, `'alpha'`). `'deprecated'` renders in red;
                other non-None values render in yellow.

        Returns:
            Styled Content label.
        """
        colors = theme.get_theme_colors()
        glyphs = get_glyphs()
        cursor = f"{glyphs.cursor} " if selected else "  "
        if not has_creds:
            spec = Content.styled(model_spec, colors.warning)
        else:
            spec = Content(model_spec)
        suffix = (
            Content.styled(f" ({t('model.current')})", "dim")
            if current
            else Content("")
        )
        if status == "deprecated":
            status_suffix = Content.styled(" (deprecated)", colors.error)
        elif status:
            status_suffix = Content.styled(f" ({status})", colors.warning)
        else:
            status_suffix = Content("")
        return Content.assemble(cursor, spec, suffix, status_suffix)

    @staticmethod
    def _format_footer(
        profile_entry: ModelProfileEntry | None,
        glyphs: Glyphs,
        provider: str | None = None,
        model_name: str | None = None,
    ) -> Content:
        """Build the detail footer text for the highlighted model.

        Args:
            profile_entry: Profile data with override tracking, or None.
            glyphs: Glyph set for display characters.
            provider: Provider name for fetching params if profile is empty.
            model_name: Model name for fetching params if profile is empty.

        Returns:
            Styled `Content` for the 4-line footer.
        """
        from invincat_cli.model_config import ModelConfig
        from invincat_cli.textual_adapter import format_token_count

        if profile_entry is None:
            return Content.styled(f"{t('model.profile_not_available')}\n\n\n", "dim")

        profile = profile_entry["profile"]
        overridden = profile_entry["overridden_keys"]

        lines = ["", "", ""]

        if "max_input_tokens" in profile:
            try:
                token_count = format_token_count(int(profile["max_input_tokens"]))
                if "max_input_tokens" in overridden:
                    lines[0] = f"Context: *{token_count} in"
                else:
                    lines[0] = f"Context: {token_count} in"
            except (ValueError, TypeError, OverflowError):
                lines[0] = f"Context: {profile['max_input_tokens']} in"

        if provider and model_name:
            config = ModelConfig.load()
            kwargs = config.get_kwargs(provider, model_name=model_name)
            if "base_url" in kwargs:
                line_idx = 1 if lines[0] else 0
                lines[line_idx] = f"Base URL: {kwargs['base_url']}"

        return Content("\n".join(lines))

    def _get_model_status(self, model_spec: str) -> str | None:
        """Look up the status field for a model from its profile.

        Args:
            model_spec: The `provider:model` string.

        Returns:
            Status string (e.g., `'deprecated'`) if the model has a profile
            with a `status` key, otherwise None.
        """
        entry = self._profiles.get(model_spec)
        if entry is None:
            return None
        profile = entry.get("profile")
        if not profile:
            return None
        return profile.get("status")

    def _update_footer(self) -> None:
        """Update the detail footer for the currently highlighted model."""
        footer = self.query_one("#model-detail-footer", Static)
        if not self._filtered_models:
            footer.update(Content.styled(t("model.no_selected"), "dim"))
            return
        index = min(self._selected_index, len(self._filtered_models) - 1)
        spec, provider = self._filtered_models[index]
        model_name = spec.split(":", 1)[1] if ":" in spec else spec
        entry = self._profiles.get(spec)
        try:
            text = self._format_footer(
                entry, get_glyphs(), provider=provider, model_name=model_name
            )
        except (KeyError, ValueError, TypeError):  # Resilient footer rendering
            logger.warning("Failed to format footer for %s", spec, exc_info=True)
            text = Content.styled(f"{t('model.could_not_load')}\n\n\n", "dim")
        footer.update(text)
