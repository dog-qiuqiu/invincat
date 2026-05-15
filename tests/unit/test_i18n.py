from __future__ import annotations

from pathlib import Path

from invincat_cli import i18n
from invincat_cli.i18n import (
    DEFAULT_LANGUAGE,
    I18n,
    Language,
    get_i18n,
    load_language_from_config,
    save_language_to_config,
    set_language,
    t,
)


def test_i18n_translation_formatting_and_missing_keys() -> None:
    manager = I18n(Language.EN)

    assert manager.t("command.unknown", command="/bad") == "Unknown command: /bad"
    assert manager.t("command.unknown") == "Unknown command: {command}"
    assert manager.t("command.unknown", other="value") == "Unknown command: {command}"
    assert manager.t("missing.key") == "missing.key"

    manager._translations[Language.ZH].pop("test.fallback", None)
    manager._translations[Language.EN]["test.fallback"] = "fallback"
    manager.language = Language.ZH
    assert manager.t("test.fallback") == "fallback"


def test_i18n_rejects_invalid_language_and_names_unknown_language() -> None:
    manager = I18n(Language.EN)

    manager.language = "fr"  # type: ignore[assignment]

    assert manager.language == DEFAULT_LANGUAGE

    class CustomLanguage:
        value = "custom"

    assert manager.get_language_name(CustomLanguage()) == "custom"  # type: ignore[arg-type]


def test_i18n_tips_and_language_names() -> None:
    manager = I18n(Language.EN)

    assert manager.get_tip(1)
    assert len(manager.get_all_tips()) >= 11
    assert manager.get_language_name(Language.EN) == "English"
    assert manager.get_language_name(Language.ZH) == "中文 (Chinese)"


def test_global_i18n_helpers_reset_language(monkeypatch) -> None:
    monkeypatch.setattr(i18n, "_i18n_instance", None)

    assert get_i18n().language == DEFAULT_LANGUAGE
    set_language(Language.ZH)
    assert get_i18n().language == Language.ZH
    assert t("language.chinese") == "中文 (Chinese)"

    set_language(Language.EN)


def test_load_language_from_config_returns_default_for_missing_invalid_or_broken(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.toml"
    assert load_language_from_config(missing) == DEFAULT_LANGUAGE

    invalid = tmp_path / "invalid.toml"
    invalid.write_text("[general]\nlanguage = 'fr'\n")
    assert load_language_from_config(invalid) == DEFAULT_LANGUAGE

    broken = tmp_path / "broken.toml"
    broken.write_text("[general\n")
    assert load_language_from_config(broken) == DEFAULT_LANGUAGE


def test_load_language_from_config_reads_valid_language(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[general]\nlanguage = 'zh'\n")

    assert load_language_from_config(config_path) == Language.ZH


def test_load_language_from_config_handles_home_failure(monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: (_ for _ in ()).throw(RuntimeError()))

    assert load_language_from_config() == DEFAULT_LANGUAGE


def test_save_language_to_config_preserves_existing_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[model]\nname = 'existing'\n")

    assert save_language_to_config(Language.ZH, config_path)

    assert load_language_from_config(config_path) == Language.ZH
    assert 'name = "existing"' in config_path.read_text()


def test_save_language_to_config_overwrites_invalid_existing_config(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[general\n")

    assert save_language_to_config(Language.EN, config_path)
    assert load_language_from_config(config_path) == Language.EN


def test_save_language_to_config_handles_home_and_write_failures(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert not save_language_to_config(Language.EN)

    config_path = tmp_path / "config.toml"

    def fail_open(
        self: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        if "w" in mode:
            raise OSError("cannot write")
        return original_open(self, mode, *args, **kwargs)

    original_open = Path.open
    monkeypatch.setattr(Path, "open", fail_open)

    assert not save_language_to_config(Language.EN, config_path)
