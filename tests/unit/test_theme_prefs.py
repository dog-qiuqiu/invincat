"""Tests for theme preference persistence."""

from __future__ import annotations

import tomllib
from pathlib import Path

from invincat_cli import theme
from invincat_cli.app_runtime import theme_prefs


def test_load_theme_preference_returns_default_when_config_missing(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(theme_prefs, "DEFAULT_CONFIG_PATH", tmp_path / "config.toml")

    assert theme_prefs.load_theme_preference() == theme.DEFAULT_THEME


def test_load_theme_preference_reads_known_theme(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    known_theme = next(iter(theme.ThemeEntry.REGISTRY))
    config_path.write_text(f"[ui]\ntheme = {known_theme!r}\n")
    monkeypatch.setattr(theme_prefs, "DEFAULT_CONFIG_PATH", config_path)

    assert theme_prefs.load_theme_preference() == known_theme


def test_load_theme_preference_falls_back_for_unknown_theme(
    monkeypatch, tmp_path
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[ui]\ntheme = 'missing-theme'\n")
    monkeypatch.setattr(theme_prefs, "DEFAULT_CONFIG_PATH", config_path)

    assert theme_prefs.load_theme_preference() == theme.DEFAULT_THEME


def test_load_theme_preference_falls_back_for_invalid_or_non_string_theme(
    monkeypatch,
    tmp_path,
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(theme_prefs, "DEFAULT_CONFIG_PATH", config_path)

    config_path.write_text("[ui\n")
    assert theme_prefs.load_theme_preference() == theme.DEFAULT_THEME

    config_path.write_text("[ui]\ntheme = 123\n")
    assert theme_prefs.load_theme_preference() == theme.DEFAULT_THEME


def test_save_theme_preference_writes_known_theme(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    known_theme = next(iter(theme.ThemeEntry.REGISTRY))
    monkeypatch.setattr(theme_prefs, "DEFAULT_CONFIG_PATH", config_path)

    assert theme_prefs.save_theme_preference(known_theme)
    assert theme_prefs.load_theme_preference() == known_theme


def test_save_theme_preference_preserves_existing_config(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    known_theme = next(iter(theme.ThemeEntry.REGISTRY))
    config_path.write_text("[model]\nname = 'existing'\n")
    monkeypatch.setattr(theme_prefs, "DEFAULT_CONFIG_PATH", config_path)

    assert theme_prefs.save_theme_preference(known_theme)

    data = tomllib.loads(config_path.read_text())
    assert data["model"]["name"] == "existing"
    assert data["ui"]["theme"] == known_theme


def test_save_theme_preference_rejects_unknown_theme(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(theme_prefs, "DEFAULT_CONFIG_PATH", config_path)

    assert not theme_prefs.save_theme_preference("missing-theme")
    assert not config_path.exists()


def test_save_theme_preference_returns_false_when_config_read_fails(
    monkeypatch,
    tmp_path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.mkdir()
    known_theme = next(iter(theme.ThemeEntry.REGISTRY))
    monkeypatch.setattr(theme_prefs, "DEFAULT_CONFIG_PATH", config_path)

    assert not theme_prefs.save_theme_preference(known_theme)


def test_save_theme_preference_cleans_temp_file_when_write_fails(
    monkeypatch,
    tmp_path,
) -> None:
    config_path = tmp_path / "config.toml"
    known_theme = next(iter(theme.ThemeEntry.REGISTRY))
    monkeypatch.setattr(theme_prefs, "DEFAULT_CONFIG_PATH", config_path)

    tmp_file = tmp_path / "theme.tmp"
    fd = 123
    monkeypatch.setattr(
        theme_prefs.tempfile, "mkstemp", lambda **_kwargs: (fd, tmp_file)
    )
    monkeypatch.setattr(
        theme_prefs.os,
        "fdopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write failed")),
    )
    unlinked: list[Path] = []
    monkeypatch.setattr(Path, "unlink", lambda self: unlinked.append(self))

    assert not theme_prefs.save_theme_preference(known_theme)
    assert unlinked == [tmp_file]
