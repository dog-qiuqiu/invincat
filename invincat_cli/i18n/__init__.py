"""Internationalization (i18n) support for invincat-cli.

This module provides comprehensive language localization support, enabling
users to switch between English and Chinese languages throughout the application.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from invincat_cli.i18n.translations import TRANSLATIONS

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)


class Language(StrEnum):
    """Supported languages for the CLI interface."""

    EN = "en"
    """English (default)"""

    ZH = "zh"
    """Chinese (Simplified)"""


DEFAULT_LANGUAGE = Language.EN
"""Default language when not configured."""

class I18n:
    """Internationalization manager for the CLI.

    This class manages language preferences and provides translation services
    for all user-facing text in the application.

    Attributes:
        current_language: The currently active language.
    """

    def __init__(self, language: Language = DEFAULT_LANGUAGE) -> None:
        """Initialize the i18n manager.

        Args:
            language: The initial language to use.
        """
        self._language = language
        self._translations = TRANSLATIONS

    @property
    def language(self) -> Language:
        """Get the current language."""
        return self._language

    @language.setter
    def language(self, value: Language) -> None:
        """Set the current language.

        Args:
            value: The language to set.
        """
        if not isinstance(value, Language):
            logger.warning(
                "Invalid language '%s', falling back to default '%s'",
                value,
                DEFAULT_LANGUAGE,
            )
            value = DEFAULT_LANGUAGE
        self._language = value
        logger.debug("Language changed to: %s", value)

    def t(self, key: str, **kwargs: Any) -> str:
        """Translate a key to the current language.

        Args:
            key: The translation key (e.g., "welcome.ready").
            **kwargs: Format arguments for string interpolation.

        Returns:
            The translated string, or the key if not found.
        """
        translations = self._translations.get(self._language, {})
        text = translations.get(key)

        if text is None:
            translations = self._translations.get(DEFAULT_LANGUAGE, {})
            text = translations.get(key, key)
            if text == key:
                logger.warning("Translation key not found: %s", key)

        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, ValueError) as e:
                logger.warning(
                    "Failed to format translation key '%s' with args %s: %s",
                    key,
                    kwargs,
                    e,
                )
                return text

        return text

    def get_tip(self, index: int) -> str:
        """Get a welcome tip by index.

        Args:
            index: The tip index (1-13).

        Returns:
            The translated tip text.
        """
        return self.t(f"welcome.tips.{index}")

    def get_all_tips(self) -> list[str]:
        """Get all welcome tips.

        Returns:
            List of all translated tip texts.
        """
        language_tips = self._translations.get(self._language, {})
        fallback_tips = self._translations.get(DEFAULT_LANGUAGE, {})
        tips: list[str] = []
        for i in range(1, 13):
            key = f"welcome.tips.{i}"
            tip = language_tips.get(key) or fallback_tips.get(key)
            if tip:
                tips.append(tip)
        return tips

    def get_language_name(self, language: Language) -> str:
        """Get the display name for a language.

        Args:
            language: The language to get the name for.

        Returns:
            The display name of the language.
        """
        if language == Language.EN:
            return self.t("language.english")
        elif language == Language.ZH:
            return self.t("language.chinese")
        return language.value


_i18n_instance: I18n | None = None


def get_i18n() -> I18n:
    """Get the global i18n instance.

    Returns:
        The global I18n instance.
    """
    global _i18n_instance
    if _i18n_instance is None:
        _i18n_instance = I18n()
    return _i18n_instance


def set_language(language: Language) -> None:
    """Set the global language.

    Args:
        language: The language to set.
    """
    i18n = get_i18n()
    i18n.language = language


def t(key: str, **kwargs: Any) -> str:
    """Translate a key using the global i18n instance.

    This is a convenience function that wraps get_i18n().t().

    Args:
        key: The translation key.
        **kwargs: Format arguments for string interpolation.

    Returns:
        The translated string.
    """
    return get_i18n().t(key, **kwargs)


def load_language_from_config(config_path: Path | None = None) -> Language:
    """Load language preference from config file.

    Args:
        config_path: Path to config file. Defaults to ~/.invincat/config.toml.

    Returns:
        The configured language, or default if not configured.
    """
    import tomllib

    if config_path is None:
        try:
            config_path = Path.home() / ".invincat" / "config.toml"
        except RuntimeError:
            logger.debug("Could not determine home directory")
            return DEFAULT_LANGUAGE

    if not config_path.exists():
        logger.debug("Config file not found at %s, using default language", config_path)
        return DEFAULT_LANGUAGE

    try:
        with config_path.open("rb") as f:
            data = tomllib.load(f)

        lang_value = data.get("general", {}).get("language")
        if lang_value:
            try:
                return Language(lang_value)
            except ValueError:
                logger.warning(
                    "Invalid language value '%s' in config, using default",
                    lang_value,
                )
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("Failed to read language from config: %s", e)

    return DEFAULT_LANGUAGE


def save_language_to_config(
    language: Language, config_path: Path | None = None
) -> bool:
    """Save language preference to config file.

    Args:
        language: The language to save.
        config_path: Path to config file. Defaults to ~/.invincat/config.toml.

    Returns:
        True if save succeeded, False otherwise.
    """
    import tomllib

    import tomli_w

    if config_path is None:
        try:
            config_path = Path.home() / ".invincat" / "config.toml"
        except RuntimeError:
            logger.error("Could not determine home directory for config path")
            return False

    config_path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {}
    if config_path.exists():
        try:
            with config_path.open("rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as e:
            logger.warning("Failed to read existing config, will overwrite: %s", e)
            data = {}

    if "general" not in data:
        data["general"] = {}

    data["general"]["language"] = language.value

    try:
        with config_path.open("wb") as f:
            tomli_w.dump(data, f)
        logger.debug("Saved language preference to %s", config_path)
        return True
    except OSError as e:
        logger.error("Failed to save language preference: %s", e)
        return False
