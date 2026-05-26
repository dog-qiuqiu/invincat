from __future__ import annotations

import asyncio

from textual.content import Content

from invincat_cli.config import get_glyphs
from invincat_cli.i18n import Language
from invincat_cli.widgets import model_register as model_register_mod
from invincat_cli.widgets.language_selector import LanguageSelectorScreen
from invincat_cli.widgets.mcp_viewer import MCPToolItem, MCPViewerScreen
from invincat_cli.widgets.model_register import ModelRegisterScreen
from invincat_cli.widgets.model_selector_display import ModelSelectorDisplayMixin
from invincat_cli.widgets.model_selector_option import ModelOption
from invincat_cli.widgets.theme_selector import ThemeSelectorScreen


def test_model_option_stores_selection_metadata() -> None:
    option = ModelOption(
        "DeepSeek Chat",
        model_spec="deepseek:deepseek-chat",
        provider="deepseek",
        index=2,
        has_creds=False,
        classes="model-option",
    )
    message = ModelOption.Clicked(option.model_spec, option.provider, option.index)

    assert option.model_spec == "deepseek:deepseek-chat"
    assert option.provider == "deepseek"
    assert option.index == 2
    assert option.has_creds is False
    assert message.model_spec == "deepseek:deepseek-chat"
    assert message.provider == "deepseek"
    assert message.index == 2


def test_model_selector_display_formats_labels_and_footer() -> None:
    selected = ModelSelectorDisplayMixin._format_option_label(
        "openai:gpt-4.1",
        selected=True,
        current=True,
        has_creds=True,
        status=None,
    )
    deprecated = ModelSelectorDisplayMixin._format_option_label(
        "openai:old-model",
        selected=False,
        current=False,
        has_creds=False,
        status="deprecated",
    )
    footer = ModelSelectorDisplayMixin._format_footer(
        {"profile": {"max_input_tokens": 128000}, "overridden_keys": frozenset()},
        get_glyphs(),
    )

    assert isinstance(selected, Content)
    assert "openai:gpt-4.1" in selected.plain
    assert "current" in selected.plain.lower()
    assert "openai:old-model" in deprecated.plain
    assert "deprecated" in deprecated.plain
    assert "Context:" in footer.plain


def test_mcp_tool_item_collapsed_expanded_toggle() -> None:
    item = MCPToolItem(
        name="search",
        description="Search remote documentation.",
        index=0,
        classes="mcp-tool-item",
    )

    collapsed = item._format_collapsed(item.tool_name, item.tool_description)
    expanded = item._format_expanded(item.tool_name, item.tool_description)
    item.toggle_expand()

    assert "Search remote documentation." in collapsed.plain
    assert "Search remote documentation." in expanded.plain
    assert item._expanded is True
    assert str(item.styles.height) == "auto"

    item.toggle_expand()

    assert item._expanded is False
    assert str(item.styles.height) == "1"


def test_mcp_viewer_navigation_handles_empty_and_wrapped_selection() -> None:
    screen = MCPViewerScreen(server_info=[])
    screen._move_selection(1)
    screen.action_toggle_expand()

    assert screen._selected_index == 0

    first = MCPToolItem("one", "", 0)
    second = MCPToolItem("two", "", 1)
    first.add_class = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    first.remove_class = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    first.scroll_visible = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    second.add_class = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    second.remove_class = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    second.scroll_visible = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    screen._tool_widgets = [first, second]

    screen._move_selection(-1)

    assert screen._selected_index == 1


def test_language_selector_handles_invalid_selection_and_cancel(monkeypatch) -> None:
    screen = LanguageSelectorScreen(Language.EN)
    dismissed: list[Language | None] = []
    event = type("Event", (), {"option": type("Option", (), {"id": "invalid"})()})()
    monkeypatch.setattr(screen, "dismiss", dismissed.append)

    screen.on_option_list_option_highlighted(event)
    screen.on_option_list_option_selected(event)
    screen.action_cancel()
    screen._refresh_ui_language()

    assert screen._current_language == Language.EN
    assert dismissed == [None, None]


def test_theme_selector_handles_invalid_selection(monkeypatch) -> None:
    screen = ThemeSelectorScreen("invincat")
    dismissed: list[str | None] = []
    event = type("Event", (), {"option": type("Option", (), {"id": "missing"})()})()
    monkeypatch.setattr(screen, "dismiss", dismissed.append)

    screen.on_option_list_option_highlighted(event)
    screen.on_option_list_option_selected(event)

    assert screen._current_theme == "invincat"
    assert dismissed == [None]


class _RegisterField:
    def __init__(self, value: str = "") -> None:
        self.value = value
        self.display = True
        self.focused = False
        self.text = ""

    def focus(self) -> None:
        self.focused = True

    def update(self, value: object) -> None:
        self.text = str(value)


def _install_register_fields(
    monkeypatch,
    screen: ModelRegisterScreen,
    *,
    provider: str = "openai",
    model: str = "deepseek-v4-flash",
    api_key_env: str = "",
    base_url: str = "https://api.deepseek.com",
    max_input_tokens: str = "128000",
    thinking: str = "enabled",
    effort: str = "high",
) -> dict[str, _RegisterField]:
    fields = {
        "reg-provider": _RegisterField(provider),
        "reg-model": _RegisterField(model),
        "reg-api-key-env": _RegisterField(api_key_env),
        "reg-base-url": _RegisterField(base_url),
        "reg-max-input-tokens": _RegisterField(max_input_tokens),
        "reg-deepseek-title": _RegisterField(),
        "reg-deepseek-thinking-label": _RegisterField(),
        "reg-deepseek-thinking": _RegisterField(thinking),
        "reg-deepseek-effort-label": _RegisterField(),
        "reg-deepseek-effort": _RegisterField(effort),
        "reg-error": _RegisterField(),
    }

    def fake_query_one(selector: str, *_args: object) -> _RegisterField:
        return fields[selector.removeprefix("#")]

    monkeypatch.setattr(screen, "query_one", fake_query_one)
    return fields


def test_model_register_deepseek_visibility(monkeypatch) -> None:
    screen = ModelRegisterScreen()
    fields = _install_register_fields(monkeypatch, screen)

    assert screen._deepseek_options_enabled() is True

    fields["reg-base-url"].value = "https://api.openai.com/v1"
    screen._update_deepseek_options_visibility()

    assert screen._deepseek_options_enabled() is False
    for widget_id in screen._DEEPSEEK_OPTION_IDS:
        assert fields[widget_id].display is False


def test_model_register_submit_persists_model_and_target_params(
    monkeypatch,
) -> None:
    screen = ModelRegisterScreen(target="memory")
    _install_register_fields(monkeypatch, screen)
    registered: list[tuple[object, ...]] = []
    saved_params: list[tuple[object, ...]] = []
    dismissed: list[tuple[str, str]] = []

    def fake_register_provider_model(*args: object, **kwargs: object) -> bool:
        registered.append((*args, kwargs))
        return True

    def fake_save_target_model_params(*args: object, **kwargs: object) -> bool:
        saved_params.append((*args, kwargs))
        return True

    monkeypatch.setattr(
        model_register_mod,
        "register_provider_model",
        fake_register_provider_model,
    )
    monkeypatch.setattr(
        model_register_mod,
        "save_target_model_params",
        fake_save_target_model_params,
    )
    monkeypatch.setattr(screen, "dismiss", dismissed.append)

    asyncio.run(screen.action_submit())

    assert dismissed == [("openai", "deepseek-v4-flash")]
    assert registered == [
        (
            "openai",
            "deepseek-v4-flash",
            {
                "api_key_env": "OPENAI_API_KEY",
                "base_url": "https://api.deepseek.com",
                "max_input_tokens": 128000,
            },
        )
    ]
    assert saved_params == [
        (
            "memory",
            "openai:deepseek-v4-flash",
            {
                "extra_body": {"thinking": {"type": "enabled"}},
                "reasoning_effort": "high",
            },
            {},
        )
    ]
