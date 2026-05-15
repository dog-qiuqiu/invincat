from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from textual.content import Content
from textual.widgets import Static

from invincat_cli.widgets import approval as approval_mod
from invincat_cli.widgets.approval import ApprovalMenu


def _action_request() -> dict[str, object]:
    return {"name": "approve_plan", "args": {"todos": []}}


def test_approval_menu_without_auto_approve_maps_to_approve_and_reject() -> None:
    loop = asyncio.new_event_loop()
    try:
        menu = ApprovalMenu(_action_request(), allow_auto_approve=False)

        approve_future: asyncio.Future[dict[str, str]] = loop.create_future()
        menu.set_future(approve_future)
        menu._handle_selection(0)
        assert approve_future.result() == {"type": "approve"}

        reject_future: asyncio.Future[dict[str, str]] = loop.create_future()
        menu.set_future(reject_future)
        menu._handle_selection(1)
        assert reject_future.result() == {"type": "reject"}
    finally:
        loop.close()


def test_approval_menu_without_auto_approve_ignores_auto_shortcut() -> None:
    loop = asyncio.new_event_loop()
    try:
        menu = ApprovalMenu(_action_request(), allow_auto_approve=False)
        pending_future: asyncio.Future[dict[str, str]] = loop.create_future()
        menu.set_future(pending_future)
        menu.action_select_auto()
        assert not pending_future.done()
    finally:
        loop.close()


class _FakeOption:
    def __init__(self) -> None:
        self.value = ""
        self.classes: set[str] = set()

    def update(self, value: object) -> None:
        self.value = str(value)

    def add_class(self, *classes: str) -> None:
        self.classes.update(classes)

    def remove_class(self, *classes: str) -> None:
        self.classes.difference_update(classes)


class _FakeCommandWidget:
    def __init__(self) -> None:
        self.values: list[Content] = []

    def update(self, value: Content) -> None:
        self.values.append(value)


class _FakeContext:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def __enter__(self) -> _FakeContext:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _patch_approval_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        approval_mod,
        "get_glyphs",
        lambda: SimpleNamespace(
            cursor=">",
            ellipsis="...",
            box_horizontal="-",
            arrow_up="up",
            arrow_down="down",
            bullet="*",
        ),
    )
    monkeypatch.setattr(
        approval_mod,
        "t",
        lambda key, **kwargs: {
            "approval.decision_required": "needs approval",
            "approval.tool_call": "tool calls",
            "approval.warning_deceptive": "deceptive",
            "approval.more_warnings": f"{kwargs.get('count')} more",
            "approval.approve": "Approve",
            "approval.auto_approve": "Auto",
            "approval.reject": "Reject",
            "approval_menu.expand_short": "expand",
            "language.preview": "preview",
            "language.select": "select",
            "language.cancel": "cancel",
        }.get(key, key),
    )


def _static_text(widget: Static) -> str:
    return str(widget._Static__content)  # noqa: SLF001


def test_approval_menu_command_display_truncates_expands_and_marks_unicode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_approval_text(monkeypatch)
    command = "echo " + ("x" * 140)
    menu = ApprovalMenu({"name": "shell", "args": {"command": command}})

    assert menu._has_expandable_command is True
    collapsed = menu._get_command_display(expanded=False).plain
    expanded = menu._get_command_display(expanded=True).plain

    assert "press 'e' to expand" in collapsed
    assert "... (press 'e' to expand)" in collapsed
    assert "x" * 140 in expanded

    dangerous = ApprovalMenu(
        {"name": "shell", "args": {"command": "echo safe\u202ehidden"}}
    )
    warning = dangerous._get_command_display(expanded=True).plain
    assert "hidden chars detected" in warning
    assert "raw:" in warning


def test_approval_menu_command_warning_raw_preview_truncates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_approval_text(monkeypatch)
    command = "echo " + ("x" * 260) + "\u202ehidden"
    menu = ApprovalMenu({"name": "shell", "args": {"command": command}})

    collapsed = menu._get_command_display(expanded=False).plain

    assert "hidden chars detected" in collapsed
    assert collapsed.count("...") >= 2
    assert "raw: echo " in collapsed


def test_approval_menu_command_display_rejects_empty_requests() -> None:
    menu = ApprovalMenu([])

    with pytest.raises(RuntimeError, match="empty action_requests"):
        menu._get_command_display(expanded=False)


def test_approval_menu_options_actions_and_expand_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_approval_text(monkeypatch)
    menu = ApprovalMenu(
        [
            {"name": "write_file", "args": {"path": "a"}},
            {"name": "edit_file", "args": {"path": "b"}},
        ]
    )
    menu._option_widgets = [_FakeOption(), _FakeOption(), _FakeOption()]  # type: ignore[list-item]
    posted: list[dict[str, str]] = []
    monkeypatch.setattr(
        menu,
        "post_message",
        lambda message: posted.append(message.decision),
    )

    menu._update_options()
    assert menu._option_widgets[0].value == "> 1. Approve 2 (y)"
    assert "approval-option-selected" in menu._option_widgets[0].classes

    menu.action_move_down()
    assert menu._selected == 1
    menu.action_move_up()
    assert menu._selected == 0
    menu.action_select_auto()
    menu.action_select_reject()
    menu.action_select_approve()
    assert posted == [
        {"type": "auto_approve_all"},
        {"type": "reject"},
        {"type": "approve"},
    ]

    command_menu = ApprovalMenu({"name": "shell", "args": {"command": "x" * 130}})
    command_widget = _FakeCommandWidget()
    command_menu._command_widget = command_widget  # type: ignore[assignment]
    command_menu.action_toggle_expand()
    assert command_menu._command_expanded is True
    assert command_widget.values


def test_approval_menu_single_option_variants_action_select_and_toggle_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_approval_text(monkeypatch)

    auto_menu = ApprovalMenu({"name": "write_file", "args": {"path": "a"}})
    auto_menu._option_widgets = [_FakeOption(), _FakeOption(), _FakeOption()]  # type: ignore[list-item]
    auto_menu._update_options()
    assert auto_menu._option_widgets[0].value == "> 1. Approve (y)"
    assert auto_menu._option_widgets[1].value == "  2. Auto (a)"
    assert auto_menu._option_widgets[2].value == "  3. Reject (n)"

    no_auto_single = ApprovalMenu(
        {"name": "write_file", "args": {"path": "a"}},
        allow_auto_approve=False,
    )
    no_auto_single._option_widgets = [_FakeOption(), _FakeOption()]  # type: ignore[list-item]
    no_auto_single._update_options()
    assert no_auto_single._option_widgets[0].value == "> 1. Approve (y)"
    assert no_auto_single._option_widgets[1].value == "  2. Reject (n)"

    no_auto_batch = ApprovalMenu(
        [
            {"name": "write_file", "args": {"path": "a"}},
            {"name": "edit_file", "args": {"path": "b"}},
        ],
        allow_auto_approve=False,
    )
    no_auto_batch._option_widgets = [_FakeOption(), _FakeOption()]  # type: ignore[list-item]
    posted: list[dict[str, str]] = []
    monkeypatch.setattr(
        no_auto_batch,
        "post_message",
        lambda message: posted.append(message.decision),
    )
    no_auto_batch._update_options()
    assert no_auto_batch._option_widgets[0].value == "> 1. Approve 2 (y)"
    assert no_auto_batch._option_widgets[1].value == "  2. Reject 2 (n)"

    no_auto_batch._selected = 1
    no_auto_batch.action_select()
    assert posted == [{"type": "reject"}]

    before = no_auto_batch._command_expanded
    no_auto_batch.action_toggle_expand()
    assert no_auto_batch._command_expanded is before


def test_approval_menu_completed_future_is_not_overwritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_approval_text(monkeypatch)
    loop = asyncio.new_event_loop()
    try:
        menu = ApprovalMenu(_action_request())
        posted: list[dict[str, str]] = []
        future: asyncio.Future[dict[str, str]] = loop.create_future()
        future.set_result({"type": "already"})
        menu.set_future(future)
        monkeypatch.setattr(
            menu,
            "post_message",
            lambda message: posted.append(message.decision),
        )

        menu._handle_selection(0)

        assert future.result() == {"type": "already"}
        assert posted == [{"type": "approve"}]
    finally:
        loop.close()


def test_approval_menu_collects_security_warnings_for_nested_args() -> None:
    menu = ApprovalMenu(
        {
            "name": "fetch",
            "args": {
                "url": "https://раypal.com",
                "nested": {"text": "safe\u202ehidden"},
            },
        }
    )

    assert any(
        "fetch.nested.text: hidden Unicode" in warning
        for warning in menu._security_warnings
    )
    assert any("fetch.url:" in warning for warning in menu._security_warnings)


def test_approval_menu_security_warnings_skip_non_dict_and_safe_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    menu = ApprovalMenu(
        [
            {"name": "fetch", "args": "not-a-dict"},
            {"name": "fetch", "args": {"url": "https://example.com/path"}},
            {"name": "fetch", "args": {"url": "https://xn--pypal-4ve.com"}},
        ]
    )

    assert all("not-a-dict" not in warning for warning in menu._security_warnings)
    assert all("example.com/path" not in warning for warning in menu._security_warnings)
    assert any("decoded host:" in warning for warning in menu._security_warnings)


def test_approval_menu_compose_minimal_and_batch_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_approval_text(monkeypatch)
    monkeypatch.setattr(approval_mod, "Container", _FakeContext)
    monkeypatch.setattr(approval_mod, "VerticalScroll", _FakeContext)
    monkeypatch.setattr(approval_mod, "Vertical", _FakeContext)

    minimal = ApprovalMenu({"name": "shell", "args": {"command": "echo ok"}})
    minimal_children = list(minimal.compose())
    assert _static_text(minimal_children[0]) == ">>> shell needs approval <<<"
    assert any(
        "echo ok" in _static_text(child)
        for child in minimal_children
        if isinstance(child, Static)
    )
    assert len(minimal._option_widgets) == 3

    expandable = ApprovalMenu(
        {"name": "shell", "args": {"command": "echo " + ("x" * 140)}}
    )
    expandable_children = list(expandable.compose())
    assert any(
        "e expand" in _static_text(child)
        for child in expandable_children
        if isinstance(child, Static)
    )

    batch = ApprovalMenu(
        [
            {
                "name": "fetch",
                "args": {"url": "https://раypal.com"},
            },
            {
                "name": "write_file",
                "description": "writes a file",
                "args": {"path": "x"},
            },
        ],
        allow_auto_approve=False,
    )
    batch_children = list(batch.compose())
    assert _static_text(batch_children[0]) == ">>> 2 tool calls needs approval <<<"
    assert any(
        "deceptive" in _static_text(child)
        for child in batch_children
        if isinstance(child, Static)
    )
    assert batch._tool_info_container is not None
    assert len(batch._option_widgets) == 2


def test_approval_menu_compose_collapses_extra_security_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_approval_text(monkeypatch)
    monkeypatch.setattr(approval_mod, "Container", _FakeContext)
    monkeypatch.setattr(approval_mod, "VerticalScroll", _FakeContext)
    monkeypatch.setattr(approval_mod, "Vertical", _FakeContext)
    menu = ApprovalMenu(
        {
            "name": "fetch",
            "args": {
                "url": "https://раypal.com",
                "a": "one\u202etwo",
                "b": "three\u202efour",
                "c": "five\u202esix",
            },
        }
    )

    children = list(menu.compose())

    warning_text = "\n".join(
        _static_text(child) for child in children if isinstance(child, Static)
    )
    assert "1 more" in warning_text


def test_approval_menu_update_tool_info_mounts_headers_descriptions_and_widgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeApprovalWidget:
        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

    class FakeRenderer:
        def get_approval_widget(
            self, args: dict[str, object]
        ) -> tuple[type[FakeApprovalWidget], dict[str, object]]:
            return FakeApprovalWidget, {"seen": args.get("path")}

    class FakeToolInfo:
        def __init__(self) -> None:
            self.removed = 0
            self.mounted: list[object] = []

        async def remove_children(self) -> None:
            self.removed += 1

        async def mount(self, widget: object) -> None:
            self.mounted.append(widget)

    menu = ApprovalMenu(
        [
            {"name": "write_file", "description": "desc", "args": {"path": "a"}},
            {"name": "edit_file", "args": {"path": "b"}},
        ]
    )
    info = FakeToolInfo()
    menu._tool_info_container = info  # type: ignore[assignment]
    monkeypatch.setattr(approval_mod, "get_renderer", lambda _name: FakeRenderer())

    asyncio.run(menu._update_tool_info())

    assert info.removed == 1
    assert len(info.mounted) == 5
    assert isinstance(info.mounted[2], FakeApprovalWidget)
    assert info.mounted[2].data == {"seen": "a"}
    assert isinstance(info.mounted[-1], FakeApprovalWidget)
    assert info.mounted[-1].data == {"seen": "b"}


def test_approval_menu_update_tool_info_noops_without_container() -> None:
    menu = ApprovalMenu({"name": "write_file", "args": {"path": "a"}})

    asyncio.run(menu._update_tool_info())


def test_approval_menu_mount_applies_ascii_border_updates_and_focuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_approval_text(monkeypatch)
    menu = ApprovalMenu({"name": "shell", "args": {"command": "echo ok"}})
    menu._option_widgets = [_FakeOption(), _FakeOption(), _FakeOption()]  # type: ignore[list-item]
    calls: list[str] = []
    monkeypatch.setattr(approval_mod, "is_ascii_mode", lambda: True)
    monkeypatch.setattr(
        approval_mod.theme,
        "get_theme_colors",
        lambda _widget: SimpleNamespace(warning="yellow"),
    )
    monkeypatch.setattr(menu, "focus", lambda: calls.append("focus"))

    asyncio.run(menu.on_mount())

    assert menu.styles.border.top[0] == "ascii"
    assert menu._option_widgets[0].value == "> 1. Approve (y)"
    assert calls == ["focus"]


def test_approval_menu_mount_updates_tool_info_for_non_minimal_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_approval_text(monkeypatch)
    menu = ApprovalMenu({"name": "write_file", "args": {"path": "a"}})
    menu._option_widgets = [_FakeOption(), _FakeOption(), _FakeOption()]  # type: ignore[list-item]
    calls: list[str] = []

    async def fake_update_tool_info() -> None:
        calls.append("update")

    monkeypatch.setattr(approval_mod, "is_ascii_mode", lambda: False)
    monkeypatch.setattr(menu, "_update_tool_info", fake_update_tool_info)
    monkeypatch.setattr(menu, "focus", lambda: calls.append("focus"))

    asyncio.run(menu.on_mount())

    assert calls == ["update", "focus"]
    assert menu._option_widgets[0].value == "> 1. Approve (y)"


def test_approval_menu_blur_refocuses_after_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    menu = ApprovalMenu(_action_request())
    calls: list[object] = []
    monkeypatch.setattr(menu, "focus", lambda: calls.append("focus"))
    monkeypatch.setattr(
        menu,
        "call_after_refresh",
        lambda callback: calls.append(callback),
    )

    menu.on_blur(SimpleNamespace())  # type: ignore[arg-type]

    assert calls == [menu.focus]
