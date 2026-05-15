from __future__ import annotations

import builtins
import sys
from types import ModuleType, SimpleNamespace

from invincat_cli.io import clipboard


class DummyWidget:
    def __init__(
        self,
        text: str | None,
        *,
        end: object | None = object(),
        fail: bool = False,
    ) -> None:
        self.text_selection = SimpleNamespace(end=end)
        self._text = text
        self._fail = fail

    def get_selection(self, _selection: object) -> tuple[str | None, object]:
        if self._fail:
            raise ValueError("bad selection")
        return (self._text, None)


class DummyApp:
    def __init__(self, widgets: list[object]) -> None:
        self.widgets = widgets
        self.copied: list[str] = []
        self.notifications: list[tuple[str, dict[str, object]]] = []

    def query(self, _selector: str) -> list[object]:
        return self.widgets

    def copy_to_clipboard(self, text: str) -> None:
        self.copied.append(text)

    def notify(self, message: str, **kwargs: object) -> None:
        self.notifications.append((message, kwargs))


def test_copy_osc52_writes_tmux_wrapped_escape_sequence(monkeypatch) -> None:
    written: list[str] = []
    flushed: list[bool] = []

    class FakeTty:
        def __enter__(self) -> FakeTty:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def write(self, text: str) -> None:
            written.append(text)

        def flush(self) -> None:
            flushed.append(True)

    def fake_open(self, mode: str, *, encoding: str) -> FakeTty:
        assert str(self) == "/dev/tty"
        assert mode == "w"
        assert encoding == "utf-8"
        return FakeTty()

    monkeypatch.setenv("TMUX", "1")
    monkeypatch.setattr(clipboard.pathlib.Path, "open", fake_open)

    clipboard._copy_osc52("hello")

    assert written == ["\033Ptmux;\033\033]52;c;aGVsbG8=\a\033\\"]
    assert flushed == [True]


def test_shorten_preview_replaces_newlines_and_truncates(monkeypatch) -> None:
    monkeypatch.setattr(
        clipboard,
        "get_glyphs",
        lambda: SimpleNamespace(newline="|", ellipsis="..."),
    )

    preview = clipboard._shorten_preview(["a\nb", "x" * 50])

    assert "\n" not in preview
    assert preview.endswith("...")


def test_copy_selection_to_clipboard_prefers_pyperclip(monkeypatch) -> None:
    pyperclip = ModuleType("pyperclip")
    copied: list[str] = []
    pyperclip.copy = copied.append
    monkeypatch.setitem(sys.modules, "pyperclip", pyperclip)
    monkeypatch.setattr(
        clipboard,
        "get_glyphs",
        lambda: SimpleNamespace(newline=" ", ellipsis="..."),
    )

    app = DummyApp(
        [
            object(),
            DummyWidget("ignored", end=None),
            DummyWidget("also ignored", fail=True),
            SimpleNamespace(
                text_selection=SimpleNamespace(end=object()),
                get_selection=lambda _selection: None,
            ),
            DummyWidget("   "),
            DummyWidget("first"),
            DummyWidget("second"),
        ]
    )

    clipboard.copy_selection_to_clipboard(app)

    assert copied == ["first\nsecond"]
    assert app.copied == []
    assert app.notifications == [
        (
            '"first second" copied',
            {
                "severity": "information",
                "timeout": 2,
                "markup": False,
            },
        )
    ]


def test_copy_selection_to_clipboard_warns_when_all_methods_fail(monkeypatch) -> None:
    pyperclip = ModuleType("pyperclip")
    pyperclip.copy = lambda _text: (_ for _ in ()).throw(RuntimeError("no copy"))
    monkeypatch.setitem(sys.modules, "pyperclip", pyperclip)
    monkeypatch.setattr(
        clipboard,
        "_copy_osc52",
        lambda _text: (_ for _ in ()).throw(OSError("no tty")),
    )

    app = DummyApp([DummyWidget("selected")])
    app.copy_to_clipboard = lambda _text: (_ for _ in ()).throw(RuntimeError("no app"))

    clipboard.copy_selection_to_clipboard(app)

    assert app.notifications == [
        (
            "Failed to copy - no clipboard method available",
            {"severity": "warning", "timeout": 3},
        )
    ]


def test_copy_selection_to_clipboard_is_quiet_without_selection(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "pyperclip", ModuleType("pyperclip"))
    app = DummyApp([object(), DummyWidget("ignored", end=None)])

    clipboard.copy_selection_to_clipboard(app)

    assert app.notifications == []


def test_copy_selection_to_clipboard_uses_app_when_pyperclip_is_unavailable(
    monkeypatch,
) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object):
        if name == "pyperclip":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(
        clipboard,
        "get_glyphs",
        lambda: SimpleNamespace(newline=" ", ellipsis="..."),
    )

    app = DummyApp([DummyWidget("selected")])

    clipboard.copy_selection_to_clipboard(app)

    assert app.copied == ["selected"]
    assert app.notifications[0][0] == '"selected" copied'
