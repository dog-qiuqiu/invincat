"""Interactive auto-memory configuration screen for /auto-memory command."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from textual.app import ComposeResult

from invincat_cli import theme
from invincat_cli.auto_memory import _read_auto_memory_config, save_auto_memory_config
from invincat_cli.config import get_glyphs, is_ascii_mode
from invincat_cli.i18n import t

logger = logging.getLogger(__name__)


@dataclass
class AutoMemoryConfig:
    """Auto-memory configuration state."""

    enabled: bool = True
    interval: int = 10
    on_exit: bool = True


_CONFIG_OPTIONS = ["enabled", "interval", "on_exit"]

_INTERVAL_CHOICES = [5, 10, 15, 20, 30]


class AutoMemoryConfigScreen(ModalScreen[AutoMemoryConfig | None]):
    """Modal dialog for auto-memory configuration.

    Displays configuration options in an `OptionList`. Returns the updated
    configuration on Enter, or `None` on Esc.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding(
            "left", "decrease", "Decrease", show=False
        ),
        Binding(
            "right", "increase", "Increase", show=False
        ),
    ]

    CSS = """
    AutoMemoryConfigScreen {
        align: center middle;
        background: transparent;
    }

    AutoMemoryConfigScreen > Vertical {
        width: 56;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }

    AutoMemoryConfigScreen .amc-title {
        text-style: bold;
        color: $primary;
        text-align: center;
        margin-bottom: 1;
    }

    AutoMemoryConfigScreen OptionList {
        height: auto;
        max-height: 16;
        background: $background;
    }

    AutoMemoryConfigScreen .amc-help {
        height: auto;
        color: $text-muted;
        text-style: italic;
        margin-top: 1;
        text-align: center;
    }
    """

    def __init__(self, config: AutoMemoryConfig | None = None) -> None:
        """Initialize the AutoMemoryConfigScreen.

        Args:
            config: Current auto-memory configuration. If None, reads from file.
        """
        super().__init__()
        if config is None:
            raw = _read_auto_memory_config()
            self._config = AutoMemoryConfig(
                enabled=raw["enabled"],
                interval=raw["interval"],
                on_exit=raw["on_exit"],
            )
        else:
            self._config = config

    def _build_options(self) -> tuple[list[Option], int]:
        """Build option list entries from current config.

        Returns:
            Tuple of (options list, highlight index for save).
        """
        options: list[Option] = []
        glyphs = get_glyphs()

        enabled_status = t("auto_memory.on") if self._config.enabled else t("auto_memory.off")
        options.append(
            Option(
                f"{t('auto_memory.enabled_label')}: {enabled_status}",
                id="enabled",
            )
        )

        options.append(
            Option(
                f"{t('auto_memory.interval')}: {self._config.interval}",
                id="interval",
            )
        )

        on_exit_status = t("auto_memory.on") if self._config.on_exit else t("auto_memory.off")
        options.append(
            Option(
                f"{t('auto_memory.on_exit_label')}: {on_exit_status}",
                id="on_exit",
            )
        )

        options.append(Option(f"── {t('auto_memory.save')} ──", id="save"))
        options.append(Option(f"── {t('auto_memory.cancel')} ──", id="cancel"))

        return options, 3

    def compose(self) -> ComposeResult:
        """Compose the screen layout.

        Yields:
            Widgets for the auto-memory config UI.
        """
        glyphs = get_glyphs()

        with Vertical():
            yield Static(t("auto_memory.title"), classes="amc-title")
            options, highlight_index = self._build_options()
            option_list = OptionList(*options, id="amc-options")
            option_list.highlighted = highlight_index
            yield option_list
            help_text = (
                f"{glyphs.arrow_up}/{glyphs.arrow_down} {t('auto_memory.interval')}"
                f" {glyphs.bullet} Enter {t('auto_memory.save')}"
                f" {glyphs.bullet} Esc {t('auto_memory.cancel')}"
            )
            yield Static(help_text, classes="amc-help")

    def on_mount(self) -> None:
        """Apply ASCII border if needed."""
        if is_ascii_mode():
            container = self.query_one(Vertical)
            colors = theme.get_theme_colors(self)
            container.styles.border = ("ascii", colors.success)

    def _refresh_options(self) -> None:
        """Rebuild the option list to reflect current config state."""
        option_list = self.query_one("#amc-options", OptionList)
        highlighted = option_list.highlighted
        option_list.clear_options()
        options, _ = self._build_options()
        for opt in options:
            option_list.add_option(opt)
        if highlighted is not None and highlighted < len(options):
            option_list.highlighted = highlighted

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle option selection.

        Args:
            event: The option selected event.
        """
        option_id = event.option.id
        if option_id is None:
            return

        if option_id == "enabled":
            self._config.enabled = not self._config.enabled
            self._refresh_options()
        elif option_id == "interval":
            current_idx = _INTERVAL_CHOICES.index(self._config.interval) if self._config.interval in _INTERVAL_CHOICES else 1
            next_idx = (current_idx + 1) % len(_INTERVAL_CHOICES)
            self._config.interval = _INTERVAL_CHOICES[next_idx]
            self._refresh_options()
        elif option_id == "on_exit":
            self._config.on_exit = not self._config.on_exit
            self._refresh_options()
        elif option_id == "save":
            ok = save_auto_memory_config(
                enabled=self._config.enabled,
                interval=self._config.interval,
                on_exit=self._config.on_exit,
            )
            if ok:
                self.app.notify(
                    t("auto_memory.saved"),
                    severity="information",
                    timeout=3,
                )
                self.dismiss(self._config)
            else:
                self.app.notify(
                    t("auto_memory.save_failed"),
                    severity="error",
                    timeout=5,
                )
        elif option_id == "cancel":
            self.dismiss(None)

    def action_decrease(self) -> None:
        """Decrease interval value when left arrow is pressed."""
        option_list = self.query_one("#amc-options", OptionList)
        if option_list.highlighted is None:
            return
        options = option_list.get_option_at_index(option_list.highlighted)
        option_id = options.id if options else None
        if option_id == "interval":
            current_idx = (
                _INTERVAL_CHOICES.index(self._config.interval)
                if self._config.interval in _INTERVAL_CHOICES
                else 1
            )
            prev_idx = (current_idx - 1) % len(_INTERVAL_CHOICES)
            self._config.interval = _INTERVAL_CHOICES[prev_idx]
            self._refresh_options()

    def action_increase(self) -> None:
        """Increase interval value when right arrow is pressed."""
        option_list = self.query_one("#amc-options", OptionList)
        if option_list.highlighted is None:
            return
        options = option_list.get_option_at_index(option_list.highlighted)
        option_id = options.id if options else None
        if option_id == "interval":
            current_idx = (
                _INTERVAL_CHOICES.index(self._config.interval)
                if self._config.interval in _INTERVAL_CHOICES
                else 1
            )
            next_idx = (current_idx + 1) % len(_INTERVAL_CHOICES)
            self._config.interval = _INTERVAL_CHOICES[next_idx]
            self._refresh_options()

    def action_cancel(self) -> None:
        """Cancel and dismiss without saving."""
        self.dismiss(None)
