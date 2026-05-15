"""Model registration and editing form."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from textual import events
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Input, Select, Static

from invincat_cli.config import get_glyphs
from invincat_cli.i18n import t
from invincat_cli.model_config import (
    PROVIDER_API_KEY_ENV,
    register_provider_model,
    save_target_model_params,
)
from invincat_cli.widgets.model_register_style import (
    MODEL_REGISTER_BINDINGS,
    MODEL_REGISTER_CSS,
    PROVIDER_SELECT_BINDINGS,
)

if TYPE_CHECKING:
    from textual.app import ComposeResult

ModelTarget = Literal["primary", "memory"]


class ProviderSelect(Select[str]):
    """Provider select that lets up/down move between registration fields."""

    BINDINGS = PROVIDER_SELECT_BINDINGS


class ModelRegisterScreen(ModalScreen[tuple[str, str] | None]):
    """Modal screen for registering a new model provider.

    Provides a form with fields for provider name, model name, API key env,
    base URL, and max input tokens.  On submit, writes the configuration to
    ``~/.invincat/config.toml`` and returns ``(provider, model)``.

    Returns ``None`` on cancel.
    """

    BINDINGS = MODEL_REGISTER_BINDINGS
    CSS = MODEL_REGISTER_CSS

    _FIELD_IDS = [
        "reg-provider",
        "reg-model",
        "reg-api-key-env",
        "reg-base-url",
        "reg-max-input-tokens",
        "reg-deepseek-thinking",
        "reg-deepseek-effort",
    ]
    PROVIDER_OPTIONS: ClassVar[tuple[str, ...]] = (
        "anthropic",
        "google_genai",
        "openai",
    )
    _DEEPSEEK_OPTION_IDS: ClassVar[tuple[str, ...]] = (
        "reg-deepseek-title",
        "reg-deepseek-thinking-label",
        "reg-deepseek-thinking",
        "reg-deepseek-effort-label",
        "reg-deepseek-effort",
    )

    def __init__(
        self,
        *,
        initial_values: dict[str, str] | None = None,
        edit_mode: bool = False,
        target: ModelTarget = "primary",
    ) -> None:
        """Initialize the registration form.

        Args:
            initial_values: Optional field values for editing an existing model.
            edit_mode: Whether the screen is updating an existing model.
            target: Model target whose runtime params should be edited.
        """
        super().__init__()
        self._initial_values = initial_values or {}
        self._edit_mode = edit_mode
        self._target = target

    def compose(self) -> ComposeResult:
        """Compose the registration form layout."""
        with Vertical():
            title_key = (
                "model.edit_title" if self._edit_mode else "model.register_title"
            )
            yield Static(t(title_key), classes="register-title")

            yield Static(
                t("model.register_provider_label"), classes="register-field-label"
            )
            yield ProviderSelect(
                [(provider, provider) for provider in self.PROVIDER_OPTIONS],
                value=self._initial_values.get("provider") or self.PROVIDER_OPTIONS[0],
                allow_blank=False,
                id="reg-provider",
                classes="register-input",
            )

            yield Static(
                t("model.register_model_label"), classes="register-field-label"
            )
            yield Input(
                placeholder=t("model.register_model_placeholder"),
                value=self._initial_values.get("model", ""),
                id="reg-model",
                classes="register-input",
            )

            yield Static(
                t("model.register_apikey_label"), classes="register-field-label"
            )
            yield Static(t("model.register_apikey_hint"), classes="register-field-hint")
            yield Input(
                placeholder=t("model.register_apikey_placeholder"),
                value=self._initial_values.get("api_key_env", ""),
                id="reg-api-key-env",
                classes="register-input",
            )

            yield Static(
                t("model.register_baseurl_label"), classes="register-field-label"
            )
            yield Input(
                placeholder=t("model.register_baseurl_placeholder"),
                value=self._initial_values.get("base_url", ""),
                id="reg-base-url",
                classes="register-input",
            )

            yield Static(
                t("model.register_max_input_tokens_label"),
                classes="register-field-label",
            )
            yield Input(
                placeholder=t("model.register_max_input_tokens_placeholder"),
                value=self._initial_values.get("max_input_tokens", ""),
                id="reg-max-input-tokens",
                classes="register-input",
            )

            yield Static(
                t("model.register_deepseek_title"),
                id="reg-deepseek-title",
                classes="register-field-label",
            )
            yield Static(
                t("model.register_deepseek_thinking_label"),
                id="reg-deepseek-thinking-label",
                classes="register-field-label",
            )
            yield ProviderSelect(
                [
                    (t("model.register_deepseek_enabled"), "enabled"),
                    (t("model.register_deepseek_disabled"), "disabled"),
                ],
                value=self._initial_values.get("deepseek_thinking") or "enabled",
                allow_blank=False,
                id="reg-deepseek-thinking",
                classes="register-input",
            )
            yield Static(
                t("model.register_deepseek_effort_label"),
                id="reg-deepseek-effort-label",
                classes="register-field-label",
            )
            yield ProviderSelect(
                [
                    (t("model.register_deepseek_effort_low"), "low"),
                    (t("model.register_deepseek_effort_medium"), "medium"),
                    (t("model.register_deepseek_effort_high"), "high"),
                ],
                value=self._initial_values.get("deepseek_effort") or "high",
                allow_blank=False,
                id="reg-deepseek-effort",
                classes="register-input",
            )

            yield Static("", id="reg-error", classes="register-error")
            yield Static(
                f"Tab {t('model.register_next_field')}"
                f" {get_glyphs().bullet} Ctrl+S {t('model.register_submit')}"
                f" {get_glyphs().bullet} Esc {t('model.cancel_action')}",
                classes="register-help",
            )

    def on_mount(self) -> None:
        """Focus the first input field on mount."""
        self._update_deepseek_options_visibility()
        self._focus_field("reg-provider")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key from any input field — submit the form."""
        event.stop()
        event.prevent_default()
        self.run_worker(self.action_submit(), exclusive=True)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Refresh conditional registration fields when inputs change."""
        if event.input.id == "reg-base-url":
            self._update_deepseek_options_visibility()

    def on_select_changed(self, event: Select.Changed) -> None:
        """Refresh conditional registration fields when provider changes."""
        if event.select.id == "reg-provider":
            self._update_deepseek_options_visibility()

    def on_key(self, event: events.Key) -> None:
        """Handle Escape key to cancel."""
        if event.key == "escape":
            event.prevent_default()
            event.stop()
            self.action_cancel()
        elif event.key in {"down", "up"}:
            if any(select.expanded for select in self.query(ProviderSelect)):
                return
            event.prevent_default()
            event.stop()
            self._cycle_field(1 if event.key == "down" else -1)

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
        if not isinstance(focused, Widget):
            self._focus_field("reg-provider")
            return

        try:
            idx = self._FIELD_IDS.index(focused.id)
        except ValueError:
            self._focus_field("reg-provider")
            return

        for step in range(1, len(self._FIELD_IDS) + 1):
            next_idx = (idx + (delta * step)) % len(self._FIELD_IDS)
            next_id = self._FIELD_IDS[next_idx]
            if self._field_visible(next_id):
                self._focus_field(next_id)
                return

    def _focus_field(self, field_id: str) -> None:
        """Focus a registration field by id."""
        self.query_one(f"#{field_id}", Widget).focus()

    def _field_visible(self, field_id: str) -> bool:
        """Return whether a registration field can be focused."""
        try:
            return bool(self.query_one(f"#{field_id}", Widget).display)
        except NoMatches:
            return False

    def _deepseek_options_enabled(self) -> bool:
        """Return whether DeepSeek-specific options should be shown."""
        provider_value = self.query_one("#reg-provider", ProviderSelect).value
        provider = provider_value if isinstance(provider_value, str) else ""
        base_url = self.query_one("#reg-base-url", Input).value.strip().lower()
        return provider == "openai" and "api.deepseek.com" in base_url

    def _update_deepseek_options_visibility(self) -> None:
        """Show DeepSeek options only for the OpenAI-compatible DeepSeek API."""
        visible = self._deepseek_options_enabled()
        for widget_id in self._DEEPSEEK_OPTION_IDS:
            try:
                self.query_one(f"#{widget_id}", Widget).display = visible
            except NoMatches:
                continue

    async def action_submit(self) -> None:
        """Validate and submit the registration form."""
        provider_value = self.query_one("#reg-provider", ProviderSelect).value
        provider = provider_value if isinstance(provider_value, str) else ""
        model_name = self.query_one("#reg-model", Input).value.strip()
        api_key_env = self.query_one("#reg-api-key-env", Input).value.strip() or None
        base_url = self.query_one("#reg-base-url", Input).value.strip()
        max_input_tokens_str = self.query_one(
            "#reg-max-input-tokens", Input
        ).value.strip()
        extra_params: dict[str, Any] = {}
        if self._deepseek_options_enabled():
            thinking_value = self.query_one(
                "#reg-deepseek-thinking", ProviderSelect
            ).value
            effort_value = self.query_one("#reg-deepseek-effort", ProviderSelect).value
            if isinstance(thinking_value, str) and thinking_value:
                extra_params.setdefault("extra_body", {})["thinking"] = {
                    "type": thinking_value
                }
            if isinstance(effort_value, str) and effort_value:
                extra_params["reasoning_effort"] = effort_value

        error_widget = self.query_one("#reg-error", Static)

        if not provider:
            error_widget.update(t("model.register_error_provider"))
            self._focus_field("reg-provider")
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
            )
            if success:
                success = await asyncio.to_thread(
                    save_target_model_params,
                    self._target,
                    f"{provider}:{model_name}",
                    extra_params or None,
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
