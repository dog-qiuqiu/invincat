"""Interactive model selector screen for /model command."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from textual import events
from textual.containers import Container, Vertical, VerticalScroll
from textual.fuzzy import Matcher
from textual.screen import ModalScreen
from textual.widgets import Input, Static

if TYPE_CHECKING:
    from collections.abc import Mapping

    from textual.app import ComposeResult

from invincat_cli import theme
from invincat_cli.config import get_glyphs, is_ascii_mode
from invincat_cli.i18n import t
from invincat_cli.model_config import (
    ModelProfileEntry,
    get_available_models,
    get_model_profiles,
)
from invincat_cli.widgets.model_register import (
    ModelRegisterScreen,
    ModelTarget,
    ProviderSelect,
)
from invincat_cli.widgets.model_selector_actions import ModelSelectorActionMixin
from invincat_cli.widgets.model_selector_display import ModelSelectorDisplayMixin
from invincat_cli.widgets.model_selector_option import ModelOption
from invincat_cli.widgets.model_selector_style import (
    MODEL_SELECTOR_BINDINGS,
    MODEL_SELECTOR_CSS,
)

__all__ = [
    "ModelOption",
    "ModelRegisterScreen",
    "ModelSelectorScreen",
    "ModelTarget",
    "ProviderSelect",
]

logger = logging.getLogger(__name__)


class ModelSelectorScreen(
    ModelSelectorActionMixin, ModelSelectorDisplayMixin, ModalScreen[tuple[str, str, ModelTarget] | None]
):
    """Full-screen modal for model selection.

    Displays available models grouped by provider with keyboard navigation
    and search filtering. Current model is highlighted.

    Returns `(model_spec, provider, target)` on selection, or None on cancel.
    """

    BINDINGS = MODEL_SELECTOR_BINDINGS
    CSS = MODEL_SELECTOR_CSS

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
            self._current_memory_spec = (
                f"{current_memory_provider}:{current_memory_model}"
            )
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
            "\n"
            f" {glyphs.bullet} Ctrl+N {t('model.register_action')}"
            f" {glyphs.bullet} Ctrl+E {t('model.edit_action')}"
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
            yield Static(
                title, classes="model-selector-title", id="model-selector-title"
            )

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
            logger.debug(
                "Failed to clear model filter after target shortcut", exc_info=True
            )
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
        elif event.key == "ctrl+e":
            event.prevent_default()
            event.stop()
            await self.action_edit_model()
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
