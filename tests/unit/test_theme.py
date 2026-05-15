from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from invincat_cli import theme


def _theme_values(**overrides: str) -> dict[str, str]:
    values = {
        field.name: getattr(theme.DARK_COLORS, field.name)
        for field in theme.fields(theme.ThemeColors)
    }
    values.update(overrides)
    return values


def test_theme_colors_validation_and_merge() -> None:
    with pytest.raises(ValueError, match="ThemeColors.primary"):
        theme.ThemeColors(**_theme_values(primary="blue"))

    merged = theme.ThemeColors.merged(
        theme.DARK_COLORS,
        {"primary": "#123456", "unknown": "#FFFFFF"},
    )

    assert merged.primary == "#123456"
    assert merged.secondary == theme.DARK_COLORS.secondary
    assert not hasattr(merged, "unknown")


def test_theme_entry_requires_non_empty_label() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        theme.ThemeEntry(label="  ", dark=True, colors=theme.DARK_COLORS)


def test_load_user_themes_merges_overrides_and_custom_sections(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[themes]
not_a_table = 123

[themes.langchain]
primary = "#123456"
unknown = "#FFFFFF"
warning = 99

[themes.custom-dark]
label = "Custom Dark"
dark = true
primary = "#654321"

[themes.light-default]
label = "Light Default"
dark = "yes"
accent = "#ABCDEF"

[themes.missing-label]
primary = "#000000"

[themes.bad-color]
label = "Bad Color"
primary = "red"
""",
        encoding="utf-8",
    )
    builtins = theme._builtin_themes()

    theme._load_user_themes(builtins, config_path=config_path)

    assert builtins["langchain"].label == "LangChain Dark"
    assert builtins["langchain"].dark is True
    assert builtins["langchain"].colors.primary == "#123456"
    assert builtins["langchain"].colors.warning == theme.DARK_COLORS.warning
    assert builtins["custom-dark"].label == "Custom Dark"
    assert builtins["custom-dark"].dark is True
    assert builtins["custom-dark"].custom is True
    assert builtins["custom-dark"].colors.primary == "#654321"
    assert builtins["light-default"].dark is False
    assert builtins["light-default"].colors.accent == "#ABCDEF"
    assert builtins["light-default"].colors.background == theme.LIGHT_COLORS.background
    assert "missing-label" not in builtins
    assert "bad-color" not in builtins
    assert "unknown color field" in caplog.text
    assert "must be a string" in caplog.text
    assert "non-table" in caplog.text


def test_load_user_themes_handles_missing_bad_and_home_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    builtins = theme._builtin_themes()
    theme._load_user_themes(builtins, config_path=tmp_path / "missing.toml")

    bad = tmp_path / "bad.toml"
    bad.write_text("[themes\n", encoding="utf-8")
    theme._load_user_themes(builtins, config_path=bad)
    assert "Could not read" in caplog.text

    monkeypatch.setattr(
        Path,
        "home",
        lambda: (_ for _ in ()).throw(RuntimeError("no home")),
    )
    theme._load_user_themes(builtins, config_path=None)


def test_load_user_themes_handles_builtin_override_edge_cases(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[themes.langchain]

[themes.langchain-light]
primary = "not-a-hex"

[themes.textual-dark]
primary = "#222222"
""",
        encoding="utf-8",
    )
    builtins = theme._builtin_themes()
    builtins.pop("textual-dark")

    original_dark = builtins["langchain"].colors.primary
    original_light = builtins["langchain-light"].colors.primary

    theme._load_user_themes(builtins, config_path=config_path)

    assert builtins["langchain"].colors.primary == original_dark
    assert builtins["langchain-light"].colors.primary == original_light
    assert "Built-in theme 'textual-dark' not in builtins" in caplog.text
    assert "color override invalid" in caplog.text


def test_build_and_reload_registry_from_user_theme(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[themes.custom]
label = "Custom"
dark = true
primary = "#101010"
""",
        encoding="utf-8",
    )

    registry = theme._build_registry(config_path=config_path)
    assert registry["custom"].colors.primary == "#101010"
    with pytest.raises(TypeError):
        registry["other"] = registry["custom"]  # type: ignore[index]

    monkeypatch.setattr(theme, "_build_registry", lambda: registry)
    assert theme.reload_registry() is registry
    assert theme.ThemeEntry.REGISTRY is registry


def test_css_variable_defaults_use_selected_palette() -> None:
    assert theme.get_css_variable_defaults(dark=True)["mode-bash"] == (
        theme.DARK_COLORS.mode_bash
    )
    assert theme.get_css_variable_defaults(dark=False)["mode-bash"] == (
        theme.LIGHT_COLORS.mode_bash
    )
    custom = theme.ThemeColors.merged(theme.DARK_COLORS, {"tool": "#111111"})
    assert theme.get_css_variable_defaults(colors=custom)["tool"] == "#111111"


def test_resolve_app_accepts_widget_or_app() -> None:
    app = SimpleNamespace(theme="langchain")

    class Widget:
        @property
        def app(self):
            return app

    assert theme._resolve_app(app) is app
    assert theme._resolve_app(Widget()) is app


def test_colors_from_textual_theme_uses_hex_and_base_fallbacks() -> None:
    current_theme = SimpleNamespace(
        dark=False,
        primary="#010203",
        secondary="ansi_magenta",
        accent="#030405",
        panel=None,
        success="#040506",
        warning="#050607",
        error="#060708",
        foreground="#070809",
        background="#08090A",
        surface="#090A0B",
    )
    app = SimpleNamespace(current_theme=current_theme)

    colors = theme._colors_from_textual_theme(app)

    assert colors.primary == "#010203"
    assert colors.secondary == theme.LIGHT_COLORS.secondary
    assert colors.panel == theme.LIGHT_COLORS.panel
    assert colors.mode_bash == "#060708"
    assert colors.mode_command == theme.LIGHT_COLORS.mode_command
    assert colors.tool == "#050607"
    assert colors.skill == theme.LIGHT_COLORS.skill


def test_get_theme_colors_registry_dynamic_and_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    custom = theme.ThemeEntry(
        label="Custom",
        dark=True,
        colors=theme.ThemeColors.merged(theme.DARK_COLORS, {"primary": "#222222"}),
        custom=True,
    )
    builtin = theme.ThemeEntry(
        label="Builtin",
        dark=False,
        colors=theme.LIGHT_COLORS,
        custom=False,
    )
    monkeypatch.setattr(
        theme.ThemeEntry,
        "REGISTRY",
        {"custom": custom, "builtin": builtin},
    )

    assert theme.get_theme_colors(SimpleNamespace(theme="custom")) is custom.colors

    resolved = theme.ThemeColors.merged(theme.LIGHT_COLORS, {"primary": "#333333"})
    monkeypatch.setattr(theme, "_colors_from_textual_theme", lambda _app: resolved)
    assert theme.get_theme_colors(SimpleNamespace(theme="builtin")) is resolved
    assert theme.get_theme_colors(SimpleNamespace(theme="missing")) is resolved

    def fail_dynamic(_app: object) -> theme.ThemeColors:
        raise RuntimeError("no current theme")

    monkeypatch.setattr(theme, "_colors_from_textual_theme", fail_dynamic)
    assert theme.get_theme_colors(SimpleNamespace(theme="builtin")) is builtin.colors
    assert theme.get_theme_colors(SimpleNamespace(theme="missing")) is theme.DARK_COLORS
    assert "Could not resolve theme colors dynamically" in caplog.text

    assert theme.get_theme_colors(None) is theme.DARK_COLORS
