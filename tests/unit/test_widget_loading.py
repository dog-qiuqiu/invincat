from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from textual.content import Content
from textual.widgets import Static

import invincat_cli.widgets.loading as loading_mod
from invincat_cli.widgets.loading import LoadingWidget, Spinner


@pytest.fixture(autouse=True)
def stable_loading_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        loading_mod,
        "get_glyphs",
        lambda: SimpleNamespace(spinner_frames=("a", "b"), pause="P"),
    )

    def fake_t(key: str) -> str:
        return {
            "status.thinking": "Thinking",
            "status.awaiting_decision": "Awaiting",
            "loading.hint": "elapsed {duration}",
            "loading.paused_at": "paused {duration}",
        }.get(key, key)

    monkeypatch.setattr(loading_mod, "t", fake_t)


def _static_text(widget: Static) -> str:
    return str(widget._Static__content)  # noqa: SLF001


def test_spinner_advances_and_wraps() -> None:
    spinner = Spinner()

    assert spinner.current_frame() == "a"
    assert spinner.next_frame() == "a"
    assert spinner.current_frame() == "b"
    assert spinner.next_frame() == "b"
    assert spinner.current_frame() == "a"


def test_loading_widget_compose_builds_spinner_status_and_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered: list[dict[str, Any]] = []

    class FakeHorizontal:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def __enter__(self) -> FakeHorizontal:
            entered.append(self.kwargs)
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(loading_mod, "Horizontal", FakeHorizontal)

    widget = LoadingWidget()
    children = list(widget.compose())

    assert entered == [{"classes": "loading-container"}]
    assert [type(child) for child in children] == [Static, Static, Static]
    assert _static_text(children[0]) == "a"
    assert _static_text(children[1]) == " Thinking... "
    assert _static_text(children[2]) == "elapsed 0s"
    assert widget._spinner_widget is children[0]
    assert widget._status_widget is children[1]
    assert widget._hint_widget is children[2]


def test_loading_widget_mount_and_animation_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget = LoadingWidget("Booting")
    widget._spinner_widget = Static("old")
    widget._hint_widget = Static("old")
    callbacks: list[tuple[float, object]] = []
    now = 100.0
    monkeypatch.setattr(loading_mod, "time", lambda: now)
    monkeypatch.setattr(
        widget,
        "set_interval",
        lambda interval, callback: callbacks.append((interval, callback)),
    )

    widget.on_mount()

    assert widget._start_time == 100.0
    assert callbacks == [(0.1, widget._update_animation)]

    now = 165.0
    widget._update_animation()

    assert _static_text(widget._spinner_widget) == "a"
    assert _static_text(widget._hint_widget) == "elapsed 1m 5s"


def test_loading_widget_status_pause_resume_and_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 20.0
    monkeypatch.setattr(loading_mod, "time", lambda: now)
    widget = LoadingWidget()
    widget._start_time = 10.0
    widget._spinner_widget = Static("a")
    widget._status_widget = Static("old")
    widget._hint_widget = Static("old")

    widget.set_status("Working")
    assert _static_text(widget._status_widget) == " Working... "

    widget.pause()
    assert widget._paused is True
    assert widget._paused_elapsed == 10
    assert _static_text(widget._status_widget) == " Awaiting... "
    assert _static_text(widget._hint_widget) == "paused 10s"
    spinner_content = widget._spinner_widget._Static__content  # noqa: SLF001
    assert isinstance(spinner_content, Content)
    assert str(spinner_content) == "P"

    widget._update_animation()
    assert str(widget._spinner_widget._Static__content) == "P"  # noqa: SLF001

    widget.resume()
    assert widget._paused is False
    assert _static_text(widget._status_widget) == " Thinking... "
    assert widget.stop() is None


def test_loading_widget_methods_tolerate_uncomposed_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loading_mod, "time", lambda: 42.0)
    widget = LoadingWidget("Custom")

    widget._update_animation()
    widget.set_status("Still custom")
    widget.pause("Waiting")
    widget.resume()

    assert widget._paused is False
    assert widget._status == "Thinking"
