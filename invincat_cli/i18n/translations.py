"""Translation catalog for Invincat CLI."""

from __future__ import annotations

from invincat_cli.i18n.catalog.en import EN_TRANSLATIONS
from invincat_cli.i18n.catalog.zh import ZH_TRANSLATIONS

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": EN_TRANSLATIONS,
    "zh": ZH_TRANSLATIONS,
}
