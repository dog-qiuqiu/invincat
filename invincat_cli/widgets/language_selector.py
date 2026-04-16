"""Interactive language selector screen for /language command."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from textual.app import ComposeResult

from invincat_cli import theme
from invincat_cli.config import get_glyphs, is_ascii_mode
from invincat_cli.i18n import Language, get_i18n, t

logger = logging.getLogger(__name__)


class LanguageSelectorScreen(ModalScreen[Language | None]):
    """Modal dialog for language selection.

    Displays available languages in an `OptionList`. Returns the selected
    language on Enter, or `None` on Esc.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    CSS = """
    LanguageSelectorScreen {
        align: center middle;
        background: transparent;
    }

    LanguageSelectorScreen > Vertical {
        width: 50;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }

    LanguageSelectorScreen .language-selector-title {
        text-style: bold;
        color: $primary;
        text-align: center;
        margin-bottom: 1;
    }

    LanguageSelectorScreen OptionList {
        height: auto;
        max-height: 16;
        background: $background;
    }

    LanguageSelectorScreen .language-selector-help {
        height: 1;
        color: $text-muted;
        text-style: italic;
        margin-top: 1;
        text-align: center;
    }

    LanguageSelectorScreen .language-selector-current {
        height: 1;
        color: $text-muted;
        text-align: center;
        margin-top: 1;
    }
    """

    def __init__(self, current_language: Language) -> None:
        """Initialize the LanguageSelectorScreen.

        Args:
            current_language: The currently active language (to highlight).
        """
        super().__init__()
        self._current_language = current_language
        self._original_language = current_language

    def compose(self) -> ComposeResult:
        """Compose the screen layout.

        Yields:
            Widgets for the language selector UI.
        """
        glyphs = get_glyphs()
        i18n = get_i18n()
        options: list[Option] = []
        highlight_index = 0

        for i, lang in enumerate(Language):
            label = i18n.get_language_name(lang)
            if lang == self._current_language:
                label = f"{label} ({t('language.current')})"
                highlight_index = i
            options.append(Option(label, id=lang.value))

        with Vertical():
            yield Static(t("language.select_title"), classes="language-selector-title")
            option_list = OptionList(*options, id="language-options")
            option_list.highlighted = highlight_index
            yield option_list
            help_text = (
                f"{glyphs.arrow_up}/{glyphs.arrow_down} {t('language.preview')}"
                f" {glyphs.bullet} Enter {t('language.select')}"
                f" {glyphs.bullet} Esc {t('language.cancel')}"
            )
            yield Static(help_text, classes="language-selector-help")

    def on_mount(self) -> None:
        """Apply ASCII border if needed."""
        if is_ascii_mode():
            container = self.query_one(Vertical)
            colors = theme.get_theme_colors(self)
            container.styles.border = ("ascii", colors.success)

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        """Live-preview the highlighted language.

        Args:
            event: The option highlighted event.
        """
        lang_id = event.option.id
        if lang_id is not None:
            try:
                lang = Language(lang_id)
                from invincat_cli.i18n import set_language

                set_language(lang)
                self._refresh_ui_language()
            except ValueError:
                logger.warning("Invalid language id: %s", lang_id)
            except Exception:
                logger.warning("Failed to preview language '%s'", lang_id, exc_info=True)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Commit the selected language.

        Args:
            event: The option selected event.
        """
        lang_id = event.option.id
        if lang_id is not None:
            try:
                lang = Language(lang_id)
                from invincat_cli.i18n import save_language_to_config, set_language

                set_language(lang)
                if save_language_to_config(lang):
                    self._refresh_ui_language()
                    self.dismiss(lang)
                else:
                    logger.error("Failed to save language preference")
                    self.dismiss(None)
            except ValueError:
                logger.warning("Invalid language id: %s", lang_id)
                self.dismiss(None)

    def action_cancel(self) -> None:
        """Cancel and restore original language."""
        from invincat_cli.i18n import set_language

        set_language(self._original_language)
        self._refresh_ui_language()
        self.dismiss(None)

    def _refresh_ui_language(self) -> None:
        """Refresh UI elements to reflect language change."""
        try:
            title = self.query_one(".language-selector-title", Static)
            title.update(t("language.select_title"))
        except Exception:
            pass

        try:
            help_widget = self.query_one(".language-selector-help", Static)
            glyphs = get_glyphs()
            help_text = (
                f"{glyphs.arrow_up}/{glyphs.arrow_down} {t('language.preview')}"
                f" {glyphs.bullet} Enter {t('language.select')}"
                f" {glyphs.bullet} Esc {t('language.cancel')}"
            )
            help_widget.update(help_text)
        except Exception:
            pass
