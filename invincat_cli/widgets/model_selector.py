"""Interactive model selector screen for /model command."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical, VerticalScroll
from textual.content import Content
from textual.events import (
    Click,  # noqa: TC002 - needed at runtime for Textual event dispatch
)
from textual.fuzzy import Matcher
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, Static

if TYPE_CHECKING:
    from collections.abc import Mapping

    from textual.app import ComposeResult

from invincat_cli import theme
from invincat_cli.config import Glyphs, get_glyphs, is_ascii_mode
from invincat_cli.i18n import t
from invincat_cli.model_config import (
    ModelProfileEntry,
    PROVIDER_API_KEY_ENV,
    get_available_models,
    get_model_profiles,
    has_provider_credentials,
    register_provider_model,
)

logger = logging.getLogger(__name__)

ModelTarget = Literal["primary", "memory"]


class ModelOption(Static):
    """A clickable model option in the selector."""

    def __init__(
        self,
        label: str | Content,
        model_spec: str,
        provider: str,
        index: int,
        *,
        has_creds: bool | None = True,
        classes: str = "",
    ) -> None:
        """Initialize a model option.

        Args:
            label: Display content — a `Content` object (preferred) or a
                plain string that `Static` will parse as markup.
            model_spec: The model specification (provider:model format).
            provider: The provider name.
            index: The index of this option in the filtered list.
            has_creds: Whether the provider has valid credentials. True if
                confirmed, False if missing, None if unknown.
            classes: CSS classes for styling.
        """
        super().__init__(label, classes=classes)
        self.model_spec = model_spec
        self.provider = provider
        self.index = index
        self.has_creds = has_creds

    class Clicked(Message):
        """Message sent when a model option is clicked."""

        def __init__(self, model_spec: str, provider: str, index: int) -> None:
            """Initialize the Clicked message.

            Args:
                model_spec: The model specification.
                provider: The provider name.
                index: The index of the clicked option.
            """
            super().__init__()
            self.model_spec = model_spec
            self.provider = provider
            self.index = index

    def on_click(self, event: Click) -> None:
        """Handle click on this option.

        Args:
            event: The click event.
        """
        event.stop()
        self.post_message(self.Clicked(self.model_spec, self.provider, self.index))


class ModelSelectorScreen(ModalScreen[tuple[str, str, ModelTarget] | None]):
    """Full-screen modal for model selection.

    Displays available models grouped by provider with keyboard navigation
    and search filtering. Current model is highlighted.

    Returns `(model_spec, provider, target)` on selection, or None on cancel.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False, priority=True),
        Binding("k", "move_up", "Up", show=False, priority=True),
        Binding("down", "move_down", "Down", show=False, priority=True),
        Binding("j", "move_down", "Down", show=False, priority=True),
        Binding("tab", "tab_complete", "Tab complete", show=False, priority=True),
        Binding("pageup", "page_up", "Page up", show=False, priority=True),
        Binding("pagedown", "page_down", "Page down", show=False, priority=True),
        Binding("1", "target_primary", "Primary target", show=False, priority=True),
        Binding("2", "target_memory", "Memory target", show=False, priority=True),
        Binding("enter", "select", "Select", show=False, priority=True),
        Binding("ctrl+n", "register_model", "Register model", show=False, priority=True),
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
    ]

    CSS = """
    ModelSelectorScreen {
        align: center middle;
    }

    ModelSelectorScreen > Vertical {
        width: 80;
        max-width: 90%;
        height: 80%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }

    ModelSelectorScreen .model-selector-title {
        text-style: bold;
        color: $primary;
        text-align: center;
        margin-bottom: 1;
    }

    ModelSelectorScreen #model-filter {
        margin-bottom: 1;
        border: solid $primary-lighten-2;
    }

    ModelSelectorScreen #model-filter:focus {
        border: solid $primary;
    }

    ModelSelectorScreen .model-list {
        height: 1fr;
        min-height: 5;
        scrollbar-gutter: stable;
        background: $background;
    }

    ModelSelectorScreen #model-options {
        height: auto;
    }

    ModelSelectorScreen .model-provider-header {
        color: $primary;
        margin-top: 1;
    }

    ModelSelectorScreen #model-options > .model-provider-header:first-child {
        margin-top: 0;
    }

    ModelSelectorScreen .model-option {
        height: 1;
        padding: 0 1;
    }

    ModelSelectorScreen .model-option:hover {
        background: $surface-lighten-1;
    }

    ModelSelectorScreen .model-option-selected {
        background: $primary;
        color: $background;
        text-style: bold;
    }

    ModelSelectorScreen .model-option-selected:hover {
        background: $primary-lighten-1;
    }

    ModelSelectorScreen .model-option-current {
        text-style: italic;
    }

    ModelSelectorScreen .model-selector-help {
        height: 1;
        color: $text-muted;
        text-style: italic;
        margin-top: 1;
        text-align: center;
    }

    ModelSelectorScreen .model-detail-footer {
        height: 4;
        padding: 0 2;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        current_model: str | None = None,
        current_provider: str | None = None,
        current_memory_model: str | None = None,
        current_memory_provider: str | None = None,
        initial_target: ModelTarget = "primary",
        cli_profile_override: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the ModelSelectorScreen.

        Data loading (model discovery, profiles) is deferred to `on_mount`
        so the screen pushes instantly and populates asynchronously.

        Args:
            current_model: The currently active model name (to highlight).
            current_provider: The provider of the current model.
            current_memory_model: Current dedicated memory model name.
            current_memory_provider: Provider for memory model.
            initial_target: Initial selection target (`primary`/`memory`).
            cli_profile_override: Extra profile fields from `--profile-override`.

                Merged on top of upstream + config.toml profiles so that CLI
                overrides appear with `*` markers in the detail footer.
        """
        super().__init__()
        self._current_model = current_model
        self._current_provider = current_provider
        self._current_memory_model = current_memory_model
        self._current_memory_provider = current_memory_provider
        self._target: ModelTarget = initial_target
        self._cli_profile_override = cli_profile_override

        # Model data — populated asynchronously in on_mount via _load_model_data
        self._all_models: list[tuple[str, str]] = []
        self._filtered_models: list[tuple[str, str]] = []
        self._selected_index = 0
        self._options_container: Container | None = None
        self._option_widgets: list[ModelOption] = []
        self._filter_text = ""
        self._current_spec: str | None = None
        if current_model and current_provider:
            self._current_spec = f"{current_provider}:{current_model}"
        elif current_model:
            self._current_spec = current_model
        self._current_memory_spec: str | None = None
        if current_memory_model and current_memory_provider:
            self._current_memory_spec = f"{current_memory_provider}:{current_memory_model}"
        elif current_memory_model:
            self._current_memory_spec = current_memory_model
        self._profiles: Mapping[str, ModelProfileEntry] = {}
        self._loaded = False

    def _find_current_model_index(self) -> int:
        """Find the index of the current model in the filtered list.

        Returns:
            Index of the current model, or 0 if not found.
        """
        current_spec = self._active_current_spec()
        if not current_spec:
            return 0

        for i, (model_spec, _) in enumerate(self._filtered_models):
            if model_spec == current_spec:
                return i
        return 0

    def _active_current_spec(self) -> str | None:
        if self._target == "memory":
            return self._current_memory_spec
        return self._current_spec

    def _target_label(self) -> str:
        return (
            t("model.target_memory")
            if self._target == "memory"
            else t("model.target_primary")
        )

    def _help_text(self) -> str:
        glyphs = get_glyphs()
        return (
            f"1 {t('model.target_primary')}"
            f" {glyphs.bullet} 2 {t('model.target_memory')}"
            f" {glyphs.bullet} {glyphs.arrow_up}/{glyphs.arrow_down} {t('model.navigate')}"
            f" {glyphs.bullet} Enter {t('model.select_action')}"
            f" {glyphs.bullet} Ctrl+N {t('model.register_action')}"
            f" {glyphs.bullet} Esc {t('model.cancel_action')}"
        )

    def _refresh_title(self) -> None:
        try:
            title_widget = self.query_one("#model-selector-title", Static)
        except Exception:
            return
        current_spec = self._active_current_spec()
        if current_spec:
            title = (
                f"{t('model.title')} "
                f"[{t('model.target_short', target=self._target_label())}] "
                f"({t('model.current_model', model=current_spec)})"
            )
        else:
            title = f"{t('model.title')} [{t('model.target_short', target=self._target_label())}]"
        title_widget.update(title)

    def compose(self) -> ComposeResult:
        """Compose the screen layout.

        Yields:
            Widgets for the model selector UI.
        """
        with Vertical():
            # Title with current model in provider:model format
            current_spec = self._active_current_spec()
            if current_spec:
                title = (
                    f"{t('model.title')} "
                    f"[{t('model.target_short', target=self._target_label())}] "
                    f"({t('model.current_model', model=current_spec)})"
                )
            else:
                title = (
                    f"{t('model.title')} "
                    f"[{t('model.target_short', target=self._target_label())}]"
                )
            yield Static(title, classes="model-selector-title", id="model-selector-title")

            # Search input
            yield Input(
                placeholder=t("model.filter_placeholder"),
                id="model-filter",
            )

            # Scrollable model list
            with VerticalScroll(classes="model-list"):
                self._options_container = Container(id="model-options")
                yield self._options_container

            # Model detail footer
            yield Static("", classes="model-detail-footer", id="model-detail-footer")

            # Help text
            help_text = self._help_text()
            yield Static(help_text, classes="model-selector-help")

    @staticmethod
    def _load_model_data(
        cli_override: dict[str, Any] | None,
    ) -> tuple[
        list[tuple[str, str]],
        Mapping[str, ModelProfileEntry],
    ]:
        """Gather model discovery data synchronously.

        Intended to be called via `asyncio.to_thread` so filesystem I/O in
        `get_available_models` does not block the event loop.

        Returns:
            Tuple of (all_models, profiles) where `all_models` is a list of
                `(provider:model spec, provider)` pairs and `profiles` maps
                spec strings to profile entries.
        """
        all_models: list[tuple[str, str]] = [
            (f"{provider}:{model}", provider)
            for provider, models in get_available_models().items()
            for model in models
        ]

        profiles = get_model_profiles(cli_override=cli_override)
        return all_models, profiles

    async def on_mount(self) -> None:
        """Set up the screen on mount.

        Loads model data in a background thread so the screen frame renders
        immediately, then populates the model list.
        """
        if is_ascii_mode():
            colors = theme.get_theme_colors(self)
            container = self.query_one(Vertical)
            container.styles.border = ("ascii", colors.success)

        # Focus the filter input immediately so the user can start typing
        # while model data loads.
        filter_input = self.query_one("#model-filter", Input)
        filter_input.focus()
        self._refresh_title()

        # Offload to thread because get_available_models does filesystem I/O
        try:
            all_models, profiles = await asyncio.to_thread(
                self._load_model_data, self._cli_profile_override
            )
        except Exception:
            logger.exception("Failed to load model data for /model selector")
            self._loaded = True
            if self.is_running:
                self.notify(
                    t("model.load_error"),
                    severity="error",
                    timeout=10,
                    markup=False,
                )
                await self._update_display()
                self._update_footer()
            return

        # Screen may have been dismissed while the thread was running
        if not self.is_running:
            return

        self._all_models = all_models
        self._profiles = profiles
        self._filtered_models = list(self._all_models)
        self._selected_index = self._find_current_model_index()
        self._loaded = True

        # Re-apply any filter text the user typed while data was loading
        if self._filter_text:
            self._update_filtered_list()

        await self._update_display()
        self._update_footer()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter models as user types.

        Args:
            event: The input changed event.
        """
        if self._maybe_consume_target_shortcut(event.value):
            return
        self._filter_text = event.value
        if not self._loaded:
            return  # on_mount will re-apply filter after data loads
        self._update_filtered_list()
        self.call_after_refresh(self._update_display)

    def _maybe_consume_target_shortcut(self, value: str) -> bool:
        """Consume single-key target shortcuts typed into the filter box."""
        raw = value.strip()
        if raw not in {"1", "2"}:
            return False

        if raw == "1":
            self._switch_target("primary")
        else:
            self._switch_target("memory")

        self._filter_text = ""
        try:
            filter_input = self.query_one("#model-filter", Input)
            if filter_input.value.strip() in {"1", "2"}:
                filter_input.value = ""
        except Exception:
            logger.debug("Failed to clear model filter after target shortcut", exc_info=True)
        return True

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key when filter input is focused.

        Args:
            event: The input submitted event.
        """
        event.stop()
        self.action_select()

    async def on_key(self, event: events.Key) -> None:
        """Handle key events that Input widgets would otherwise consume.

        Textual's Input captures Ctrl+key combinations when focused,
        preventing screen-level BINDINGS from firing. This handler
        intercepts Ctrl+N and target-toggle shortcuts.
        """
        if event.key == "ctrl+n":
            event.prevent_default()
            event.stop()
            await self.action_register_model()
        elif event.key == "1" and not self._filter_text.strip():
            event.prevent_default()
            event.stop()
            self.action_target_primary()
        elif event.key == "2" and not self._filter_text.strip():
            event.prevent_default()
            event.stop()
            self.action_target_memory()

    def on_model_option_clicked(self, event: ModelOption.Clicked) -> None:
        """Handle click on a model option.

        Args:
            event: The click event with model info.
        """
        self._selected_index = event.index
        self.dismiss((event.model_spec, event.provider, self._target))

    def _update_filtered_list(self) -> None:
        """Update the filtered models based on search text using fuzzy matching.

        Results are sorted by match score (best first).
        """
        query = self._filter_text.strip()
        if not query:
            self._filtered_models = list(self._all_models)
            self._selected_index = self._find_current_model_index()
            return

        tokens = query.split()

        try:
            matchers = [Matcher(token, case_sensitive=False) for token in tokens]
            scored: list[tuple[float, str, str]] = []
            for spec, provider in self._all_models:
                scores = [m.match(spec) for m in matchers]
                if all(s > 0 for s in scores):
                    scored.append((min(scores), spec, provider))
        except (ValueError, TypeError, AttributeError):
            # Graceful fallback for known edge-case Matcher failures
            # (e.g. invalid token type, unexpected None in model list).
            logger.warning(
                "Fuzzy matcher failed for query %r, falling back to full list",
                query,
                exc_info=True,
            )
            self._filtered_models = list(self._all_models)
            self._selected_index = self._find_current_model_index()
            return
        except Exception:
            # Unexpected error — log at ERROR and re-raise so it isn't silently
            # swallowed (e.g. MemoryError, KeyboardInterrupt subclasses).
            logger.error(
                "Unexpected error in fuzzy matcher for query %r",
                query,
                exc_info=True,
            )
            raise

        self._filtered_models = [
            (spec, provider) for score, spec, provider in sorted(scored, reverse=True)
        ]
        self._selected_index = 0

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
        suffix = Content.styled(f" ({t('model.current')})", "dim") if current else Content("")
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
        from invincat_cli.textual_adapter import format_token_count
        from invincat_cli.model_config import ModelConfig

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
            text = self._format_footer(entry, get_glyphs(), provider=provider, model_name=model_name)
        except (KeyError, ValueError, TypeError):  # Resilient footer rendering
            logger.warning("Failed to format footer for %s", spec, exc_info=True)
            text = Content.styled(f"{t('model.could_not_load')}\n\n\n", "dim")
        footer.update(text)

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
        screen = ModelRegisterScreen()
        self.app.push_screen(screen, self._handle_register_result)

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
        self._profiles = get_model_profiles(
            cli_override=self._cli_profile_override
        )

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


class ModelRegisterScreen(ModalScreen[tuple[str, str] | None]):
    """Modal screen for registering a new model provider.

    Provides a form with fields for provider name, model name, API key env,
    base URL, and class path.  On submit, writes the configuration to
    ``~/.invincat/config.toml`` and returns ``(provider, model)``.

    Returns ``None`` on cancel.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("enter", "submit", "Submit", show=False, priority=True),
        Binding("tab", "next_field", "Next field", show=False, priority=True),
        Binding("shift+tab", "prev_field", "Prev field", show=False, priority=True),
        Binding("down", "next_field", "Next field", show=False, priority=True),
        Binding("up", "prev_field", "Prev field", show=False, priority=True),
    ]

    CSS = """
    ModelRegisterScreen {
        align: center middle;
    }

    ModelRegisterScreen > Vertical {
        width: 70;
        max-width: 90%;
        height: auto;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }

    ModelRegisterScreen .register-title {
        text-style: bold;
        color: $primary;
        text-align: center;
        margin-bottom: 1;
    }

    ModelRegisterScreen .register-field-label {
        color: $text;
        margin-top: 1;
    }

    ModelRegisterScreen .register-field-hint {
        color: $text-muted;
        text-style: italic;
        margin-bottom: 0;
    }

    ModelRegisterScreen .register-input {
        margin-bottom: 0;
    }

    ModelRegisterScreen .register-error {
        color: $error;
        margin-top: 1;
    }

    ModelRegisterScreen .register-help {
        color: $text-muted;
        text-style: italic;
        margin-top: 1;
        text-align: center;
    }
    """

    _FIELD_IDS = [
        "reg-provider",
        "reg-model",
        "reg-api-key-env",
        "reg-base-url",
        "reg-max-input-tokens",
        "reg-class-path",
    ]

    def compose(self) -> ComposeResult:
        """Compose the registration form layout."""
        with Vertical():
            yield Static(t("model.register_title"), classes="register-title")

            yield Static(t("model.register_provider_label"), classes="register-field-label")
            yield Input(
                placeholder=t("model.register_provider_placeholder"),
                id="reg-provider",
                classes="register-input",
            )

            yield Static(t("model.register_model_label"), classes="register-field-label")
            yield Input(
                placeholder=t("model.register_model_placeholder"),
                id="reg-model",
                classes="register-input",
            )

            yield Static(t("model.register_apikey_label"), classes="register-field-label")
            yield Static(
                t("model.register_apikey_hint"), classes="register-field-hint"
            )
            yield Input(
                placeholder=t("model.register_apikey_placeholder"),
                id="reg-api-key-env",
                classes="register-input",
            )

            yield Static(t("model.register_baseurl_label"), classes="register-field-label")
            yield Input(
                placeholder=t("model.register_baseurl_placeholder"),
                id="reg-base-url",
                classes="register-input",
            )

            yield Static(t("model.register_max_input_tokens_label"), classes="register-field-label")
            yield Input(
                placeholder=t("model.register_max_input_tokens_placeholder"),
                id="reg-max-input-tokens",
                classes="register-input",
            )

            yield Static(t("model.register_classpath_label"), classes="register-field-label")
            yield Static(
                t("model.register_classpath_hint"), classes="register-field-hint"
            )
            yield Input(
                placeholder=t("model.register_classpath_placeholder"),
                id="reg-class-path",
                classes="register-input",
            )

            yield Static("", id="reg-error", classes="register-error")
            yield Static(
                f"Tab {t('model.register_next_field')}"
                f" {get_glyphs().bullet} Enter {t('model.register_submit')}"
                f" {get_glyphs().bullet} Esc {t('model.cancel_action')}",
                classes="register-help",
            )

    def on_mount(self) -> None:
        """Focus the first input field on mount."""
        self.query_one("#reg-provider", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key from any input field — submit the form."""
        event.stop()
        event.prevent_default()
        self.run_worker(self.action_submit(), exclusive=True)

    def on_key(self, event: events.Key) -> None:
        """Handle Escape key to cancel."""
        if event.key == "escape":
            event.prevent_default()
            event.stop()
            self.action_cancel()

    def action_next_field(self) -> None:
        """Move focus to the next input field."""
        self._cycle_field(1)

    def action_prev_field(self) -> None:
        """Move focus to the previous input field."""
        self._cycle_field(-1)

    def _cycle_field(self, delta: int) -> None:
        """Cycle focus through input fields.

        Args:
            delta: Direction to cycle (+1 forward, -1 backward).
        """
        focused = self.focused
        if not isinstance(focused, Input):
            self.query_one("#reg-provider", Input).focus()
            return

        try:
            idx = self._FIELD_IDS.index(focused.id)
        except ValueError:
            self.query_one("#reg-provider", Input).focus()
            return

        next_idx = (idx + delta) % len(self._FIELD_IDS)
        next_id = self._FIELD_IDS[next_idx]
        self.query_one(f"#{next_id}", Input).focus()

    async def action_submit(self) -> None:
        """Validate and submit the registration form."""
        provider = self.query_one("#reg-provider", Input).value.strip()
        model_name = self.query_one("#reg-model", Input).value.strip()
        api_key_env = self.query_one("#reg-api-key-env", Input).value.strip() or None
        base_url = self.query_one("#reg-base-url", Input).value.strip()
        max_input_tokens_str = self.query_one("#reg-max-input-tokens", Input).value.strip()
        class_path = self.query_one("#reg-class-path", Input).value.strip() or None

        error_widget = self.query_one("#reg-error", Static)

        if not provider:
            error_widget.update(t("model.register_error_provider"))
            self.query_one("#reg-provider", Input).focus()
            return

        if not model_name:
            error_widget.update(t("model.register_error_model"))
            self.query_one("#reg-model", Input).focus()
            return

        if not base_url:
            error_widget.update(t("model.register_error_baseurl"))
            self.query_one("#reg-base-url", Input).focus()
            return

        if not max_input_tokens_str:
            error_widget.update(t("model.register_error_max_input_tokens_positive"))
            self.query_one("#reg-max-input-tokens", Input).focus()
            return

        max_input_tokens: int | None = None
        try:
            max_input_tokens = int(max_input_tokens_str)
            if max_input_tokens <= 0:
                error_widget.update(t("model.register_error_max_input_tokens_positive"))
                self.query_one("#reg-max-input-tokens", Input).focus()
                return
        except ValueError:
            error_widget.update(t("model.register_error_max_input_tokens_integer"))
            self.query_one("#reg-max-input-tokens", Input).focus()
            return

        if ":" in provider or ":" in model_name:
            error_widget.update(t("model.register_error_colon"))
            return

        if class_path and ":" not in class_path:
            error_widget.update(t("model.register_error_classpath"))
            return

        if not api_key_env and provider in PROVIDER_API_KEY_ENV:
            api_key_env = PROVIDER_API_KEY_ENV[provider]

        try:
            success = await asyncio.to_thread(
                register_provider_model,
                provider,
                model_name,
                api_key_env=api_key_env,
                base_url=base_url,
                max_input_tokens=max_input_tokens,
                class_path=class_path,
            )

            if success:
                self.dismiss((provider, model_name))
            else:
                error_widget.update(t("model.register_error_save"))
        except Exception as e:
            error_widget.update(f"Error: {e}")

    def action_cancel(self) -> None:
        """Cancel the registration."""
        self.dismiss(None)
