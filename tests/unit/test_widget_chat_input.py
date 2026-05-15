from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.content import Content
from textual.css.query import NoMatches
from textual.widgets import Static

from invincat_cli.io.input import MediaTracker, ParsedPastedPathPayload
from invincat_cli.io.media_utils import ImageData, VideoData
from invincat_cli.widgets import chat_input as chat_input_module
from invincat_cli.widgets.autocomplete import CompletionResult
from invincat_cli.widgets.chat_input import (
    ChatInput,
    ChatTextArea,
    CompletionOption,
    CompletionPopup,
    _CompletionViewAdapter,
)


class _FakeTextArea:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.cursor_location = (0, 0)
        self.disabled = False
        self.focused = 0
        self.blurred = 0
        self.completion_active: list[bool] = []
        self.app_focus: list[bool] = []
        self.cleared = 0
        self.moves: list[tuple[int, int]] = []
        self.inserted: list[str] = []
        self._skip_history_change_events = 0
        self._in_history = False

    def move_cursor(self, location: tuple[int, int]) -> None:
        self.cursor_location = location
        self.moves.append(location)

    def focus(self) -> None:
        self.focused += 1

    def blur(self) -> None:
        self.blurred += 1

    def set_completion_active(self, *, active: bool) -> None:
        self.completion_active.append(active)

    def set_app_focus(self, *, has_focus: bool) -> None:
        self.app_focus.append(has_focus)

    def clear_text(self) -> None:
        self.cleared += 1
        self.text = ""

    def insert(self, value: str) -> None:
        self.inserted.append(value)
        self.text += value

    def set_text_from_history(self, text: str) -> None:
        self._skip_history_change_events += 1
        self.text = text


class _FakePopup:
    def __init__(self) -> None:
        self.updated: list[tuple[list[tuple[str, str]], int]] = []
        self.selections: list[int] = []
        self.hidden = 0

    def update_suggestions(
        self, suggestions: list[tuple[str, str]], selected_index: int
    ) -> None:
        self.updated.append((list(suggestions), selected_index))

    def update_selection(self, selected_index: int) -> None:
        self.selections.append(selected_index)

    def hide(self) -> None:
        self.hidden += 1


class _FakeManager:
    def __init__(self) -> None:
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1


class _FakeHistory:
    def __init__(self) -> None:
        self.added: list[str] = []

    def add(self, value: str) -> None:
        self.added.append(value)


class _FakeTracker:
    def __init__(self) -> None:
        self.synced: list[str] = []

    def sync_to_text(self, text: str) -> None:
        self.synced.append(text)


class _FakeEvent:
    def __init__(
        self,
        *,
        key: str = "",
        character: str | None = None,
        is_printable: bool = False,
        text: str = "",
    ) -> None:
        self.key = key
        self.character = character
        self.is_printable = is_printable
        self.text = text
        self.prevented = 0
        self.stopped = 0

    def prevent_default(self) -> None:
        self.prevented += 1

    def stop(self) -> None:
        self.stopped += 1


def _chat(tmp_path: Path, *, text: str = "hello") -> tuple[ChatInput, _FakeTextArea]:
    widget = ChatInput(history_file=tmp_path / "history.jsonl")
    text_area = _FakeTextArea(text)
    widget._text_area = text_area  # type: ignore[assignment]
    widget._file_controller = SimpleNamespace()
    widget._shell_controller = SimpleNamespace()
    widget._slash_controller = SimpleNamespace()
    widget.call_after_refresh = lambda _callback: None  # type: ignore[method-assign]
    widget.post_message = lambda _message: None  # type: ignore[method-assign]
    return widget, text_area


def test_default_history_path_and_chat_input_default_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    default_path = tmp_path / ".invincat" / "history.jsonl"
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert chat_input_module._default_history_path() == default_path

    called: list[Path] = []

    class History:
        def __init__(self, path: Path) -> None:
            called.append(path)

    monkeypatch.setattr(chat_input_module, "HistoryManager", History)
    widget = ChatInput()

    assert widget._history is not None
    assert called == [default_path]


class _PopupOption:
    def __init__(self) -> None:
        self.content: list[tuple[str, str, int, bool]] = []
        self.selected: list[bool] = []
        self.removed = 0
        self.scrolled = 0

    def set_content(
        self, label: str, description: str, index: int, *, is_selected: bool
    ) -> None:
        self.content.append((label, description, index, is_selected))

    def set_selected(self, *, selected: bool) -> None:
        self.selected.append(selected)

    async def remove(self) -> None:
        self.removed += 1

    def scroll_visible(self) -> None:
        self.scrolled += 1


def test_completion_option_display_selection_and_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    option = CompletionOption("/help", "Show help", 2, is_selected=False)
    posted: list[int] = []
    monkeypatch.setattr(
        option, "post_message", lambda message: posted.append(message.index)
    )

    option._update_display()
    assert isinstance(option._Static__content, Content)  # noqa: SLF001
    assert option._Static__content.plain == "help  Show help"  # noqa: SLF001

    option.set_selected(selected=True)
    assert "completion-option-selected" in option.classes
    option.set_content("@README.md", "md", 3, is_selected=False)
    assert option._Static__content.plain == "@README.md  md"  # noqa: SLF001

    event = SimpleNamespace(stopped=False, stop=lambda: setattr(event, "stopped", True))
    option.on_click(event)  # type: ignore[arg-type]
    assert event.stopped is True
    assert posted == [3]


def test_completion_option_mount_no_description_and_same_selection() -> None:
    option = CompletionOption("/clear", "", 0, is_selected=True)

    option.on_mount()
    assert option._Static__content.plain == "clear"  # noqa: SLF001
    assert "completion-option-selected" in option.classes

    option.set_selected(selected=True)
    assert option._Static__content.plain == "clear"  # noqa: SLF001


def test_completion_popup_selection_hide_and_show() -> None:
    popup = CompletionPopup()
    options = [
        SimpleNamespace(
            selected=[],
            scrolled=0,
            set_selected=lambda *, selected: options[0].selected.append(selected),
            scroll_visible=lambda: setattr(
                options[0], "scrolled", options[0].scrolled + 1
            ),
        ),
        SimpleNamespace(
            selected=[],
            scrolled=0,
            set_selected=lambda *, selected: options[1].selected.append(selected),
            scroll_visible=lambda: setattr(
                options[1], "scrolled", options[1].scrolled + 1
            ),
        ),
    ]
    popup._options = options  # type: ignore[assignment]
    popup._selected_index = 0

    popup.update_selection(1)
    assert options[0].selected == [False]
    assert options[1].selected == [True]
    assert options[1].scrolled == 1

    popup.hide()
    assert popup._pending_suggestions == []
    assert popup.styles.display == "none"
    popup.show()
    assert popup.styles.display == "block"


def test_completion_popup_rebuild_empty_and_same_selection_noop() -> None:
    popup = CompletionPopup()
    option = _PopupOption()
    popup._options = [option]  # type: ignore[list-item]
    popup._selected_index = 0
    popup._pending_suggestions = []
    popup._rebuild_generation = 1

    asyncio.run(popup._rebuild_options(1))
    assert popup.styles.display == "none"

    popup.update_selection(0)
    assert option.selected == []


def test_completion_popup_click_reposts_option_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popup = CompletionPopup()
    posted: list[int] = []
    monkeypatch.setattr(
        popup,
        "post_message",
        lambda message: posted.append(message.index),
    )
    event = SimpleNamespace(index=4, stopped=False)
    event.stop = lambda: setattr(event, "stopped", True)

    popup.on_completion_option_clicked(event)  # type: ignore[arg-type]

    assert event.stopped is True
    assert posted == [4]


def test_completion_popup_update_suggestions_defers_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popup = CompletionPopup()
    callbacks = []
    monkeypatch.setattr(popup, "call_after_refresh", callbacks.append)

    popup.update_suggestions([("/help", "Show help")], 0)

    assert popup._pending_suggestions == [("/help", "Show help")]
    assert popup._pending_selected == 0
    assert popup._rebuild_generation == 1
    assert len(callbacks) == 1

    popup.update_suggestions([], 0)
    assert popup.styles.display == "none"


def test_completion_popup_rebuild_options_reuses_trims_and_mounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popup = CompletionPopup()
    first = _PopupOption()
    second = _PopupOption()
    popup._options = [first, second]  # type: ignore[list-item]
    removed_children = 0

    async def fake_mount(*widgets: CompletionOption) -> None:
        mounted.extend(widgets)

    async def fake_remove_children() -> None:
        nonlocal removed_children
        removed_children += 1

    mounted: list[CompletionOption] = []
    monkeypatch.setattr(popup, "mount", fake_mount)
    monkeypatch.setattr(popup, "remove_children", fake_remove_children)

    popup._rebuild_generation = 2
    popup._pending_suggestions = [("/only", "one")]
    popup._pending_selected = 0
    asyncio.run(popup._rebuild_options(1))
    assert first.content == []

    asyncio.run(popup._rebuild_options(2))
    assert first.content == [("/only", "one", 0, True)]
    assert second.removed == 1
    assert popup._options == [first]
    assert first.scrolled == 1
    assert popup.styles.display == "block"

    popup._rebuild_generation = 3
    popup._pending_suggestions = [("/one", "1"), ("/two", "2")]
    popup._pending_selected = 0
    asyncio.run(popup._rebuild_options(3))
    assert len(mounted) == 1
    assert popup._options[0] is first

    async def broken_mount(*_widgets: CompletionOption) -> None:
        raise RuntimeError("mount failed")

    monkeypatch.setattr(popup, "mount", broken_mount)
    popup._options = []  # type: ignore[assignment]
    popup._rebuild_generation = 4
    popup._pending_suggestions = [("/boom", "bad")]
    asyncio.run(popup._rebuild_options(4))
    assert removed_children == 1
    assert popup.styles.display == "none"


def test_chat_text_area_placeholder_span_and_buffer_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    area = ChatTextArea()
    stopped: list[str] = []
    monkeypatch.setattr(
        area, "_cancel_paste_burst_timer", lambda: stopped.append("stop")
    )

    area.text = "[image 1] [video 2] "
    assert area._find_image_placeholder_span(9, backwards=True) == (0, 9)
    assert area._find_image_placeholder_span(10, backwards=True) == (0, 10)
    assert area._find_image_placeholder_span(10, backwards=False) == (10, 19)
    assert area._find_image_placeholder_span(999, backwards=True) is None

    area._paste_burst_buffer = "'/tmp/a.png'"
    area._paste_burst_last_char_time = 1.0
    area._backslash_pending_time = 2.0
    area.set_text_from_history("one\ntwo")
    assert area.text == "one\ntwo"
    assert area.cursor_location == (1, 3)
    assert area._skip_history_change_events == 1
    assert area._paste_burst_buffer == ""
    assert area._backslash_pending_time is None
    assert stopped == ["stop"]

    area._in_history = True
    area.clear_text()
    assert area.text == ""
    assert area.cursor_location == (0, 0)
    assert area._in_history is False
    assert area._skip_history_change_events == 2


def test_chat_text_area_delete_placeholder_branches() -> None:
    area = ChatTextArea()
    assert area._delete_image_placeholder(backwards=True) is False

    area.text = "plain"
    area.move_cursor((0, 3))
    assert area._delete_image_placeholder(backwards=True) is False

    area.text = "keep [image 1] tail"
    area.move_cursor((0, 14))
    area.selection = area.selection.__class__((0, 0), (0, 1))
    assert area._delete_image_placeholder(backwards=True) is False

    area.selection = area.selection.__class__((0, 14), (0, 14))
    assert area._delete_image_placeholder(backwards=True) is True
    assert area.text == "keep  tail"
    assert area.cursor_location == (0, 5)

    area.text = "[video 2] tail"
    area.move_cursor((0, 0))
    assert area._delete_image_placeholder(backwards=False) is True
    assert area.text == " tail"


def test_chat_text_area_paste_burst_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    area = ChatTextArea()
    scheduled: list[str] = []
    monkeypatch.setattr(
        area, "_schedule_paste_burst_flush", lambda: scheduled.append("scheduled")
    )

    area.text = ""
    area.move_cursor((0, 0))
    assert area._should_start_paste_burst("'") is True
    assert area._should_start_paste_burst("a") is False

    area.text = "existing"
    assert area._should_start_paste_burst("'") is False

    area._append_paste_burst("'", 10.0)
    area._append_paste_burst("/tmp/a.png", 10.01)
    assert area._paste_burst_buffer == "'/tmp/a.png"
    assert area._paste_burst_last_char_time == 10.01
    assert scheduled == ["scheduled", "scheduled"]


def test_chat_text_area_focus_timer_flush_and_backslash_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    area = ChatTextArea()
    inserted: list[str] = []
    focus_callbacks: list[object] = []
    stopped: list[str] = []
    timers: list[float] = []

    monkeypatch.setattr(area, "insert", inserted.append)
    monkeypatch.setattr(area, "call_after_refresh", focus_callbacks.append)
    area._backslash_pending_time = 1.0
    area.set_app_focus(has_focus=True)
    assert area._backslash_pending_time is None
    assert len(focus_callbacks) == 1

    area.set_completion_active(active=True)
    assert area._completion_active is True
    area.action_insert_newline()
    assert inserted == ["\n"]

    area._paste_burst_timer = SimpleNamespace(stop=lambda: stopped.append("stopped"))
    area._cancel_paste_burst_timer()
    assert stopped == ["stopped"]
    assert area._paste_burst_timer is None

    monkeypatch.setattr(
        area,
        "set_timer",
        lambda delay, callback: (
            timers.append(delay)
            or SimpleNamespace(stop=lambda: stopped.append("scheduled-stopped"))
        ),
    )
    area._schedule_paste_burst_flush()
    assert timers == [chat_input_module._PASTE_BURST_FLUSH_DELAY_SECONDS]

    area._paste_burst_buffer = ""
    asyncio.run(area._flush_paste_burst())

    async def broken_to_thread(_func, _payload: str):
        raise RuntimeError("parse failed")

    monkeypatch.setattr(chat_input_module.asyncio, "to_thread", broken_to_thread)
    area._paste_burst_buffer = "raw"
    asyncio.run(area._flush_paste_burst())
    assert inserted[-1] == "raw"

    area = ChatTextArea()
    area.text = "a\\"
    area.move_cursor((0, 2))
    assert area._delete_preceding_backslash() is True
    assert area.text == "a"

    area.text = "abc\\\n"
    area.move_cursor((1, 0))
    assert area._delete_preceding_backslash() is True
    assert area.text == "abc"

    area.move_cursor((0, 0))
    assert area._delete_preceding_backslash() is False


def test_chat_text_area_flush_paste_burst_posts_paths_or_inserts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    area = ChatTextArea()
    path = tmp_path / "image.png"
    posted: list[ChatTextArea.PastedPaths] = []
    inserted: list[str] = []
    monkeypatch.setattr(area, "post_message", posted.append)
    monkeypatch.setattr(area, "insert", inserted.append)
    monkeypatch.setattr(area, "_cancel_paste_burst_timer", lambda: None)

    async def parsed_to_thread(_func, _payload: str):
        return ParsedPastedPathPayload([path])

    monkeypatch.setattr(chat_input_module.asyncio, "to_thread", parsed_to_thread)
    area._paste_burst_buffer = "'payload'"
    asyncio.run(area._flush_paste_burst())
    assert posted[0].raw_text == "'payload'"
    assert posted[0].paths == [path]
    assert inserted == []

    async def none_to_thread(_func, _payload: str):
        return None

    monkeypatch.setattr(chat_input_module.asyncio, "to_thread", none_to_thread)
    area._paste_burst_buffer = "plain"
    asyncio.run(area._flush_paste_burst())
    assert inserted == ["plain"]


def test_chat_text_area_paste_event_posts_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    area = ChatTextArea()
    path = tmp_path / "image.png"
    posted: list[ChatTextArea.PastedPaths] = []
    monkeypatch.setattr(area, "post_message", posted.append)

    async def parsed_to_thread(_func, _payload: str):
        return ParsedPastedPathPayload([path])

    monkeypatch.setattr(chat_input_module.asyncio, "to_thread", parsed_to_thread)
    event = _FakeEvent(text="'payload'")
    asyncio.run(area._on_paste(event))  # type: ignore[arg-type]
    assert event.prevented == 1
    assert event.stopped == 1
    assert posted[0].paths == [path]

    flushed: list[str] = []

    async def fake_flush() -> None:
        flushed.append("flush")

    monkeypatch.setattr(area, "_flush_paste_burst", fake_flush)
    area._paste_burst_buffer = "'buffered'"
    event = _FakeEvent(text="'payload'")
    asyncio.run(area._on_paste(event))  # type: ignore[arg-type]
    assert flushed == ["flush"]

    async def broken_to_thread(_func, _payload: str):
        raise RuntimeError("parse failed")

    monkeypatch.setattr(chat_input_module.asyncio, "to_thread", broken_to_thread)
    event = _FakeEvent(text="plain")
    asyncio.run(area._on_paste(event))  # type: ignore[arg-type]
    assert event.prevented == 0
    assert event.stopped == 0


def test_chat_text_area_key_shortcuts_and_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    area = ChatTextArea()
    inserted: list[str] = []
    posted: list[object] = []
    monkeypatch.setattr(area, "insert", inserted.append)
    monkeypatch.setattr(area, "post_message", posted.append)
    monkeypatch.setattr(chat_input_module.time, "monotonic", lambda: 100.0)

    event = _FakeEvent(key="space", character=None)
    asyncio.run(area._on_key(event))  # type: ignore[arg-type]
    assert inserted == [" "]
    assert event.prevented == 1
    assert event.stopped == 1
    assert isinstance(posted[-1], ChatTextArea.Typing)

    event = _FakeEvent(key="ctrl+j")
    asyncio.run(area._on_key(event))  # type: ignore[arg-type]
    assert inserted[-1] == "\n"
    assert event.prevented == 1
    assert event.stopped == 1

    area.text = "submit"
    event = _FakeEvent(key="enter")
    asyncio.run(area._on_key(event))  # type: ignore[arg-type]
    assert event.prevented == 1
    assert event.stopped == 1
    assert isinstance(posted[-1], ChatTextArea.Submitted)
    assert posted[-1].value == "submit"

    area.text = "line"
    area.move_cursor((0, 0))
    event = _FakeEvent(key="up")
    asyncio.run(area._on_key(event))  # type: ignore[arg-type]
    assert event.prevented == 1
    assert event.stopped == 1
    assert isinstance(posted[-1], ChatTextArea.HistoryPrevious)


def test_chat_text_area_key_paste_burst_backslash_and_completion_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    area = ChatTextArea()
    appended: list[str] = []
    flushed: list[str] = []
    started: list[tuple[str, float]] = []
    inserted: list[str] = []
    posted: list[object] = []
    now = 100.0

    async def fake_flush() -> None:
        flushed.append("flush")

    monkeypatch.setattr(chat_input_module.time, "monotonic", lambda: now)
    monkeypatch.setattr(
        area,
        "_append_paste_burst",
        lambda text, when: appended.append(text),
    )
    monkeypatch.setattr(
        area, "_start_paste_burst", lambda char, when: started.append((char, when))
    )
    monkeypatch.setattr(area, "_flush_paste_burst", fake_flush)
    monkeypatch.setattr(area, "_delete_preceding_backslash", lambda: True)
    monkeypatch.setattr(area, "insert", inserted.append)
    monkeypatch.setattr(area, "post_message", posted.append)

    area._paste_burst_buffer = "'"
    event = _FakeEvent(key="enter")
    asyncio.run(area._on_key(event))  # type: ignore[arg-type]
    assert appended == ["\n"]
    assert event.prevented == 1
    assert event.stopped == 1

    area._paste_burst_buffer = "'"
    area._paste_burst_last_char_time = now
    event = _FakeEvent(key="a", character="a", is_printable=True)
    asyncio.run(area._on_key(event))  # type: ignore[arg-type]
    assert appended[-1] == "a"
    assert event.prevented == 1
    assert event.stopped == 1

    area._paste_burst_buffer = "'"
    area._paste_burst_last_char_time = now - 1
    event = _FakeEvent(key="x", character="x", is_printable=True)
    asyncio.run(area._on_key(event))  # type: ignore[arg-type]
    assert flushed == ["flush"]

    area._paste_burst_buffer = ""
    area.text = ""
    area.move_cursor((0, 0))
    event = _FakeEvent(key="'", character="'", is_printable=True)
    asyncio.run(area._on_key(event))  # type: ignore[arg-type]
    assert started[-1] == ("'", now)
    assert event.prevented == 1
    assert event.stopped == 1

    area._completion_active = False
    area._backslash_pending_time = now
    event = _FakeEvent(key="enter")
    asyncio.run(area._on_key(event))  # type: ignore[arg-type]
    assert inserted[-1] == "\n"
    assert event.prevented == 1
    assert event.stopped == 1

    area._completion_active = True
    event = _FakeEvent(key="up")
    asyncio.run(area._on_key(event))  # type: ignore[arg-type]
    assert event.prevented == 1
    assert event.stopped == 0

    area._completion_active = False
    area.text = "line"
    area.move_cursor((0, 4))
    event = _FakeEvent(key="down")
    asyncio.run(area._on_key(event))  # type: ignore[arg-type]
    assert event.prevented == 1
    assert event.stopped == 1
    assert isinstance(posted[-1], ChatTextArea.HistoryNext)


def test_chat_text_area_key_sets_backslash_pending_and_deletes_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    area = ChatTextArea()
    now = 123.0
    monkeypatch.setattr(chat_input_module.time, "monotonic", lambda: now)

    event = _FakeEvent(key="backslash", character="\\")
    asyncio.run(area._on_key(event))  # type: ignore[arg-type]
    assert area._backslash_pending_time == now

    monkeypatch.setattr(area, "_delete_image_placeholder", lambda *, backwards: True)
    event = _FakeEvent(key="backspace")
    asyncio.run(area._on_key(event))  # type: ignore[arg-type]
    assert event.prevented == 1
    assert event.stopped == 1

    event = _FakeEvent(key="delete")
    asyncio.run(area._on_key(event))  # type: ignore[arg-type]
    assert event.prevented == 1
    assert event.stopped == 1

    monkeypatch.setattr(area, "_delete_image_placeholder", lambda *, backwards: False)
    event = _FakeEvent(key="backspace")
    asyncio.run(area._on_key(event))  # type: ignore[arg-type]
    assert event.prevented == 0


def test_chat_input_compose_yields_expected_widgets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    widget = ChatInput(history_file=tmp_path / "history.jsonl")

    class Horizontal:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> Horizontal:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(chat_input_module, "Horizontal", Horizontal)

    composed = list(widget.compose())

    assert len(composed) == 3
    assert isinstance(composed[1], ChatTextArea)
    assert isinstance(composed[2], CompletionPopup)


def test_chat_input_completion_mapping_and_prefix_stripping(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    widget, text_area = _chat(tmp_path, text="abc\ndef")
    text_area.cursor_location = (1, 2)

    assert widget._get_cursor_offset() == 6
    widget.mode = "shell"
    assert widget._completion_text_and_cursor() == ("!abc\ndef", 7)
    assert widget._completion_index_to_text_index(1) == 0
    assert widget._completion_index_to_text_index(100) == len(text_area.text)
    assert "clamping" in caplog.text

    text_area.text = "cmd"
    text_area.cursor_location = (0, 2)
    widget._strip_mode_prefix()
    assert text_area.text == "md"
    assert text_area.cursor_location == (0, 1)
    assert widget._stripping_prefix is True


def test_chat_input_mapping_and_prefix_helpers_without_text_area(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    widget, _text_area = _chat(tmp_path)
    widget._text_area = None

    assert widget._completion_text_and_cursor() == ("", 0)
    assert widget._completion_index_to_text_index(10) == 0
    widget._strip_mode_prefix()
    assert widget._get_cursor_offset() == 0
    assert widget.value == ""
    widget.value = "ignored"
    widget.focus_input()
    widget.set_disabled(disabled=True)

    widget, text_area = _chat(tmp_path, text="")
    widget._strip_mode_prefix()
    assert text_area.text == ""

    text_area.text = "!run"
    widget._stripping_prefix = True
    widget._strip_mode_prefix()
    assert "Previous _stripping_prefix guard" in caplog.text


def test_chat_input_path_payload_parsing_and_command_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    widget, _text_area = _chat(tmp_path)
    widget.watch_mode = lambda _mode: None  # type: ignore[method-assign]
    path = tmp_path / "image.png"

    def fake_parse(
        text: str, *, allow_leading_path: bool = False
    ) -> ParsedPastedPathPayload | None:
        if text == str(path) and allow_leading_path:
            return ParsedPastedPathPayload([path], token_end=len(str(path)))
        return None

    monkeypatch.setattr(
        ChatInput, "_parse_dropped_path_payload", staticmethod(fake_parse)
    )

    widget.mode = "command"
    candidate, parsed = widget._parse_dropped_path_payload_with_command_recovery(
        str(path).lstrip("/"), allow_leading_path=True
    )
    assert candidate == str(path)
    assert parsed == ParsedPastedPathPayload([path], token_end=len(str(path)))
    assert widget.mode == "normal"

    widget.mode = "command"
    monkeypatch.setattr(
        ChatInput,
        "_is_existing_path_payload",
        staticmethod(lambda text: text == str(path)),
    )
    assert widget._is_dropped_path_payload(str(path).lstrip("/")) is True

    def fake_extract(text: str) -> tuple[Path, int] | None:
        if text == str(path):
            return path, len(str(path))
        return None

    monkeypatch.setattr(
        "invincat_cli.io.input.extract_leading_pasted_file_path", fake_extract
    )
    candidate, leading_match = (
        widget._extract_leading_dropped_path_with_command_recovery(
            str(path).lstrip("/")
        )
    )
    assert candidate == str(path)
    assert leading_match == (path, len(str(path)))
    assert widget.mode == "normal"


def test_chat_input_real_path_payload_detection(
    tmp_path: Path,
) -> None:
    widget, _text_area = _chat(tmp_path)
    path = tmp_path / "note.txt"
    path.write_text("hello")

    assert ChatInput._is_existing_path_payload(str(path)) is True
    assert widget._is_dropped_path_payload("") is False
    assert widget._is_dropped_path_payload("plain") is False


def test_chat_input_path_payload_recovery_guard_branches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    widget, _text_area = _chat(tmp_path)
    path = tmp_path / "image.png"

    monkeypatch.setattr(
        ChatInput,
        "_parse_dropped_path_payload",
        staticmethod(
            lambda text, *, allow_leading_path=False: (
                ParsedPastedPathPayload([path]) if text == "parsed" else None
            )
        ),
    )
    assert widget._parse_dropped_path_payload_with_command_recovery("parsed") == (
        "parsed",
        ParsedPastedPathPayload([path]),
    )

    widget.mode = "normal"
    assert widget._parse_dropped_path_payload_with_command_recovery("missing") == (
        "missing",
        None,
    )

    widget.mode = "command"
    assert widget._parse_dropped_path_payload_with_command_recovery("missing") == (
        "missing",
        None,
    )

    monkeypatch.setattr(
        "invincat_cli.io.input.extract_leading_pasted_file_path",
        lambda text: (path, len(str(path))) if text == "parsed" else None,
    )
    assert widget._extract_leading_dropped_path_with_command_recovery("parsed") == (
        "parsed",
        (path, len(str(path))),
    )

    widget.mode = "normal"
    assert widget._extract_leading_dropped_path_with_command_recovery("missing") == (
        "missing",
        None,
    )

    widget.mode = "command"
    assert widget._extract_leading_dropped_path_with_command_recovery("missing") == (
        "missing",
        None,
    )

    assert ChatInput._is_existing_path_payload("/") is False
    monkeypatch.setattr(
        ChatInput, "_is_existing_path_payload", staticmethod(lambda text: text == "hit")
    )
    widget.mode = "normal"
    assert widget._is_dropped_path_payload("hit") is True
    widget.mode = "command"
    assert widget._is_dropped_path_payload("miss") is False


def test_chat_input_submit_value_history_prefix_and_mode_reset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    widget, text_area = _chat(tmp_path, text="status")
    manager = _FakeManager()
    history = _FakeHistory()
    posted: list[tuple[str, str]] = []
    widget._completion_manager = manager  # type: ignore[assignment]
    widget._history = history  # type: ignore[assignment]
    monkeypatch.setattr(
        widget, "_replace_submitted_paths_with_images", lambda value: value
    )
    monkeypatch.setattr(
        widget,
        "post_message",
        lambda message: (
            posted.append((message.value, message.mode))
            if hasattr(message, "value")
            else None
        ),
    )

    widget.mode = "command"
    widget._completion_manager = manager  # type: ignore[assignment]
    widget._submit_value("help")
    assert history.added == ["/help"]
    assert posted == [("/help", "command")]
    assert text_area.cleared == 1
    assert widget.mode == "normal"
    assert manager.resets == 1

    widget.mode = "shell"
    widget._completion_manager = manager  # type: ignore[assignment]
    widget._submit_value("pwd")
    assert history.added[-1] == "!pwd"
    assert widget.mode == "shell"

    widget.mode = "command"
    widget._submit_value("/already")
    assert history.added[-1] == "/already"


def test_chat_input_submit_empty_value_is_ignored(tmp_path: Path) -> None:
    widget, text_area = _chat(tmp_path)
    history = _FakeHistory()
    widget._history = history  # type: ignore[assignment]

    widget._submit_value("")

    assert history.added == []
    assert text_area.cleared == 0


def test_chat_input_media_sync_value_focus_and_disabled(tmp_path: Path) -> None:
    widget, text_area = _chat(tmp_path, text="hello")
    tracker = _FakeTracker()
    manager = _FakeManager()
    widget._image_tracker = tracker  # type: ignore[assignment]
    widget._completion_manager = manager  # type: ignore[assignment]

    widget._sync_media_tracker_to_text("keep")
    assert tracker.synced == ["keep"]

    widget._skip_media_sync_events = 1
    widget._sync_media_tracker_to_text("skip")
    assert tracker.synced == ["keep"]
    assert widget._skip_media_sync_events == 0

    assert widget.value == "hello"
    widget.value = "new"
    assert text_area.text == "new"
    widget.focus_input()
    assert text_area.focused == 1
    widget.set_cursor_active(active=False)
    assert text_area.app_focus == [False]
    widget.set_disabled(disabled=True)
    assert text_area.disabled is True
    assert text_area.blurred == 1
    assert manager.resets == 1

    widget._skip_media_sync_events = -1
    widget._sync_media_tracker_to_text("reset")
    assert widget._skip_media_sync_events == 0
    assert tracker.synced == ["keep"]


def test_chat_input_typing_submitted_and_guard_handlers(
    tmp_path: Path,
) -> None:
    widget, _text_area = _chat(tmp_path)
    posted: list[object] = []
    submitted: list[str] = []
    widget.post_message = posted.append  # type: ignore[method-assign]
    widget._submit_value = submitted.append  # type: ignore[method-assign]

    widget.on_chat_text_area_typing(ChatTextArea.Typing())
    widget.on_chat_text_area_submitted(ChatTextArea.Submitted("run"))
    assert isinstance(posted[0], ChatInput.Typing)
    assert submitted == ["run"]

    widget._text_area = None
    widget.on_chat_text_area_pasted_paths(
        ChatTextArea.PastedPaths(raw_text="raw", paths=[tmp_path / "one.png"])
    )
    assert widget._apply_inline_dropped_path_replacement("raw") is False
    widget._insert_pasted_paths("raw", [tmp_path / "one.png"])
    assert widget.input_widget is None


def test_chat_input_pasted_paths_and_external_paste(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    widget, text_area = _chat(tmp_path)
    path = tmp_path / "image.png"
    inserted_paths: list[tuple[str, list[Path]]] = []

    monkeypatch.setattr(
        widget,
        "_insert_pasted_paths",
        lambda raw_text, paths: inserted_paths.append((raw_text, paths)),
    )

    widget.on_chat_text_area_pasted_paths(
        ChatTextArea.PastedPaths(raw_text="raw", paths=[path])
    )
    assert inserted_paths == [("raw", [path])]

    monkeypatch.setattr(widget, "_parse_dropped_path_payload", lambda _text: None)
    assert widget.handle_external_paste("plain") is True
    assert text_area.inserted == ["plain"]
    assert text_area.focused == 1

    monkeypatch.setattr(
        widget,
        "_parse_dropped_path_payload",
        lambda _text: ParsedPastedPathPayload([path]),
    )
    assert widget.handle_external_paste("path payload") is True
    assert inserted_paths[-1] == ("path payload", [path])

    widget._text_area = None
    assert widget.handle_external_paste("plain") is False


def test_chat_input_path_replacements_attach_media(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    widget, text_area = _chat(tmp_path)
    tracker = MediaTracker()
    image_path = tmp_path / "one.png"
    video_path = tmp_path / "two.mp4"
    other_path = tmp_path / "note.txt"
    widget._image_tracker = tracker

    def fake_get_media(path: Path) -> ImageData | VideoData | None:
        if path == image_path:
            return ImageData("img", "png", "")
        if path == video_path:
            return VideoData("vid", "mp4", "")
        return None

    monkeypatch.setattr(
        "invincat_cli.io.media_utils.get_media_from_path", fake_get_media
    )

    replacement, attached = widget._build_path_replacement(
        f"{image_path} {video_path} {other_path}",
        [image_path, video_path, other_path],
        add_trailing_space=True,
    )
    assert attached is True
    assert replacement == f"[image 1] [video 1] {other_path} "
    assert len(tracker.images) == 1
    assert len(tracker.videos) == 1

    widget._insert_pasted_paths("raw", [image_path])
    assert text_area.inserted[-1] == "[image 2] "

    widget._image_tracker = None
    assert widget._build_path_replacement(
        "raw", [image_path], add_trailing_space=True
    ) == (
        "raw",
        False,
    )


def test_chat_input_path_replacement_warns_for_invalid_media(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    widget, _text_area = _chat(tmp_path)
    tracker = MediaTracker()
    widget._image_tracker = tracker
    notified: list[str] = []
    existing = tmp_path / "bad.png"
    existing.write_bytes(b"not really an image")
    missing = tmp_path / "missing.mp4"

    monkeypatch.setattr(
        "invincat_cli.io.media_utils.get_media_from_path", lambda _path: None
    )
    monkeypatch.setattr(
        ChatInput,
        "app",
        property(
            lambda _self: SimpleNamespace(
                notify=lambda message, **_kwargs: notified.append(message)
            )
        ),
    )

    replacement, attached = widget._build_path_replacement(
        f"{existing}\n{missing}",
        [existing, missing],
        add_trailing_space=False,
    )

    assert attached is False
    assert replacement == f"{existing}\n{missing}"
    assert notified[0] == f"Could not attach image: {existing.name}"
    assert missing.name in notified[1]

    monkeypatch.setattr("invincat_cli.io.media_utils.MAX_MEDIA_BYTES", 1)
    notified.clear()
    replacement, attached = widget._build_path_replacement(
        str(existing),
        [existing],
        add_trailing_space=False,
    )
    assert attached is False
    assert replacement == str(existing)
    assert "too large" in notified[0]


def test_chat_input_inline_and_submitted_path_replacements(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    widget, text_area = _chat(tmp_path, text="payload")
    image_path = tmp_path / "one.png"

    monkeypatch.setattr(
        widget,
        "_parse_dropped_path_payload",
        lambda text: (
            ParsedPastedPathPayload([image_path]) if text == "payload" else None
        ),
    )
    monkeypatch.setattr(
        widget,
        "_build_path_replacement",
        lambda _raw, _paths, add_trailing_space: ("[image 1] ", True),
    )

    assert widget._apply_inline_dropped_path_replacement("payload") is True
    assert text_area.text == "[image 1] "
    assert text_area.moves[-1] == (0, 10)
    assert widget._applying_inline_path_replacement is True

    monkeypatch.setattr(
        widget,
        "_parse_dropped_path_payload_with_command_recovery",
        lambda value, allow_leading_path: (
            f"{image_path} describe",
            ParsedPastedPathPayload([image_path], token_end=len(str(image_path))),
        ),
    )
    assert (
        widget._replace_submitted_paths_with_images("ignored") == "[image 1] describe"
    )

    monkeypatch.setattr(
        widget,
        "_parse_dropped_path_payload_with_command_recovery",
        lambda value, allow_leading_path: (
            str(image_path),
            ParsedPastedPathPayload([image_path], token_end=len(str(image_path))),
        ),
    )
    assert widget._replace_submitted_paths_with_images("ignored") == "[image 1]"

    monkeypatch.setattr(
        widget,
        "_parse_dropped_path_payload_with_command_recovery",
        lambda value, allow_leading_path: (
            str(image_path),
            ParsedPastedPathPayload([image_path]),
        ),
    )
    assert widget._replace_submitted_paths_with_images("ignored") == "[image 1]"


def test_chat_input_path_replacement_noop_and_raw_insert_guards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    widget, text_area = _chat(tmp_path, text="payload")
    path = tmp_path / "note.txt"

    monkeypatch.setattr(
        widget,
        "_parse_dropped_path_payload",
        lambda _text: ParsedPastedPathPayload([path]),
    )
    monkeypatch.setattr(
        widget,
        "_build_path_replacement",
        lambda raw, _paths, add_trailing_space: (raw, True),
    )
    assert widget._apply_inline_dropped_path_replacement("payload") is False

    monkeypatch.setattr(
        widget,
        "_build_path_replacement",
        lambda raw, _paths, add_trailing_space: (raw, False),
    )
    widget._insert_pasted_paths("raw", [path])
    assert text_area.inserted[-1] == "raw"


def test_chat_input_submitted_path_replacement_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    widget, _text_area = _chat(tmp_path)
    image_path = tmp_path / "one.png"

    monkeypatch.setattr(
        widget,
        "_parse_dropped_path_payload_with_command_recovery",
        lambda value, allow_leading_path: (value, None),
    )
    assert widget._replace_submitted_paths_with_images("plain") == "plain"

    monkeypatch.setattr(
        widget,
        "_parse_dropped_path_payload_with_command_recovery",
        lambda value, allow_leading_path: (
            str(image_path),
            ParsedPastedPathPayload([image_path]),
        ),
    )
    monkeypatch.setattr(
        widget,
        "_extract_leading_dropped_path_with_command_recovery",
        lambda _value: (str(image_path), None),
    )
    monkeypatch.setattr(
        widget,
        "_build_path_replacement",
        lambda raw, _paths, add_trailing_space: (raw, False),
    )
    assert widget._replace_submitted_paths_with_images("ignored") == "ignored"

    monkeypatch.setattr(
        widget,
        "_extract_leading_dropped_path_with_command_recovery",
        lambda _value: (f"{image_path} suffix", (image_path, len(str(image_path)))),
    )
    assert widget._replace_submitted_paths_with_images("ignored") == "ignored"


def test_chat_input_completion_render_clear_click_and_replace(
    tmp_path: Path,
) -> None:
    widget, text_area = _chat(tmp_path, text="hello @REA\nnext")
    text_area.cursor_location = (0, 10)
    popup = _FakePopup()
    manager = _FakeManager()
    widget._popup = popup  # type: ignore[assignment]
    widget._completion_manager = manager  # type: ignore[assignment]

    suggestions = [("@README.md", "md"), ("@src/app.py", "py")]
    widget.render_completion_suggestions(suggestions, 0)
    assert popup.updated == [(suggestions, 0)]
    assert text_area.completion_active == [True]

    widget.render_completion_suggestions(suggestions, 1)
    assert popup.selections == [1]

    widget._current_suggestions = suggestions
    widget._current_selected_index = 1
    widget.on_completion_popup_option_clicked(SimpleNamespace(index=0))
    assert text_area.text == "hello @README.md\nnext"
    assert manager.resets == 1
    assert text_area.focused == 1

    widget.clear_completion_suggestions()
    assert popup.hidden == 1
    assert text_area.completion_active[-1] is False

    widget.replace_completion_range(0, 5, "line1\nline2")
    assert text_area.text.startswith("line1\nline2")
    assert text_area.moves[-1] == (1, 5)


def test_chat_input_completion_click_guards_and_replace_without_text_area(
    tmp_path: Path,
) -> None:
    widget, text_area = _chat(tmp_path, text="hello")
    manager = _FakeManager()
    widget._completion_manager = manager  # type: ignore[assignment]

    widget._current_suggestions = []
    widget.on_completion_popup_option_clicked(SimpleNamespace(index=0))
    assert manager.resets == 0

    widget._current_suggestions = [("@README.md", "md")]
    widget.on_completion_popup_option_clicked(SimpleNamespace(index=5))
    widget.on_completion_popup_option_clicked(SimpleNamespace(index=-1))
    assert text_area.text == "hello"

    text_area.cursor_location = (0, 5)
    widget.on_completion_popup_option_clicked(SimpleNamespace(index=0))
    assert text_area.text == "hello"
    assert manager.resets == 1
    assert text_area.focused == 1

    widget._text_area = None
    widget.on_completion_popup_option_clicked(SimpleNamespace(index=0))
    widget.replace_completion_range(0, 1, "x")


def test_chat_input_slash_completion_click_uses_view_adapter(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    widget, text_area = _chat(tmp_path, text="help")
    widget.watch_mode = lambda _mode: None  # type: ignore[method-assign]
    text_area.cursor_location = (0, 4)
    manager = _FakeManager()
    widget._completion_manager = manager  # type: ignore[assignment]
    widget._current_suggestions = [("/status", "Show status")]

    widget.on_completion_popup_option_clicked(SimpleNamespace(index=0))
    assert "not initialized" in caplog.text
    assert text_area.text == "help"

    widget._completion_view = _CompletionViewAdapter(widget)
    widget.mode = "command"
    widget._completion_prefix_len = 1
    widget.on_completion_popup_option_clicked(SimpleNamespace(index=0))
    assert text_area.text == "/status"
    assert manager.resets == 1


def test_chat_input_text_change_mode_completion_and_guards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    widget, text_area = _chat(tmp_path, text="/help")
    widget.watch_mode = lambda _mode: None  # type: ignore[method-assign]
    calls: list[tuple[str, int] | str] = []

    class Manager(_FakeManager):
        def on_text_changed(self, text: str, cursor: int) -> None:
            calls.append((text, cursor))

    manager = Manager()
    widget._completion_manager = manager  # type: ignore[assignment]
    text_area.cursor_location = (0, 5)
    monkeypatch.setattr(widget, "scroll_visible", lambda: calls.append("scroll"))
    monkeypatch.setattr(widget, "_is_dropped_path_payload", lambda _text: False)

    widget.on_text_area_changed(SimpleNamespace(text_area=text_area))

    assert widget.mode == "command"
    assert text_area.text == "help"
    assert calls == [("/help", 5), "scroll"]

    widget._applying_completion = True
    text_area.text = "ignored"
    widget.on_text_area_changed(SimpleNamespace(text_area=text_area))
    assert widget._applying_completion is False
    assert calls == [("/help", 5), "scroll"]

    text_area._skip_history_change_events = 1
    widget.on_text_area_changed(SimpleNamespace(text_area=text_area))
    assert text_area._skip_history_change_events == 0
    assert manager.resets == 1


def test_chat_input_text_change_edge_guards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    widget, text_area = _chat(tmp_path, text="plain")
    widget.watch_mode = lambda _mode: None  # type: ignore[method-assign]
    calls: list[tuple[str, int] | str] = []

    class Manager(_FakeManager):
        def on_text_changed(self, text: str, cursor: int) -> None:
            calls.append((text, cursor))

    manager = Manager()
    widget._completion_manager = manager  # type: ignore[assignment]
    monkeypatch.setattr(widget, "scroll_visible", lambda: calls.append("scroll"))
    monkeypatch.setattr(widget, "_is_dropped_path_payload", lambda _text: False)

    text_area._skip_history_change_events = -1
    widget.on_text_area_changed(SimpleNamespace(text_area=text_area))
    assert text_area._skip_history_change_events == 0
    assert "negative" in caplog.text

    widget._applying_inline_path_replacement = True
    widget.on_text_area_changed(SimpleNamespace(text_area=text_area))
    assert widget._applying_inline_path_replacement is False

    monkeypatch.setattr(
        widget, "_apply_inline_dropped_path_replacement", lambda _text: True
    )
    before = list(calls)
    widget.on_text_area_changed(SimpleNamespace(text_area=text_area))
    assert calls == before

    monkeypatch.setattr(
        widget, "_apply_inline_dropped_path_replacement", lambda _text: False
    )
    widget._stripping_prefix = True
    widget.on_text_area_changed(SimpleNamespace(text_area=text_area))
    assert widget._stripping_prefix is False

    text_area.text = "/tmp/image.png"
    widget.mode = "command"
    monkeypatch.setattr(widget, "_is_dropped_path_payload", lambda _text: True)
    widget.on_text_area_changed(SimpleNamespace(text_area=text_area))
    assert widget.mode == "normal"
    assert manager.resets == 1


def test_chat_input_mount_and_update_slash_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    widget = ChatInput(history_file=tmp_path / "history.jsonl")
    text_area = _FakeTextArea()
    popup = _FakePopup()
    workers: list[dict[str, bool]] = []
    updated: list[list[tuple[str, str, str]]] = []

    class FileController:
        def __init__(self, _view: object, *, cwd: Path) -> None:
            self.cwd = cwd

        async def warm_cache(self) -> None:
            return None

    class ShellController(FileController):
        pass

    class SlashController:
        def __init__(self, commands: list[tuple[str, str, str]], _view: object) -> None:
            self.commands = commands

        def update_commands(self, commands: list[tuple[str, str, str]]) -> None:
            updated.append(commands)

    class Manager:
        def __init__(self, controllers: list[object]) -> None:
            self.controllers = controllers

    monkeypatch.setattr(chat_input_module, "is_ascii_mode", lambda: True)
    monkeypatch.setattr(
        chat_input_module.theme,
        "get_theme_colors",
        lambda _widget: SimpleNamespace(primary="red"),
    )
    monkeypatch.setattr(chat_input_module, "FuzzyFileController", FileController)
    monkeypatch.setattr(chat_input_module, "ShellCompletionController", ShellController)
    monkeypatch.setattr(chat_input_module, "SlashCommandController", SlashController)
    monkeypatch.setattr(chat_input_module, "MultiCompletionManager", Manager)

    def fake_query(selector: str, _widget_type: object) -> object:
        if selector == "#chat-input":
            return text_area
        if selector == "#completion-popup":
            return popup
        raise NoMatches(selector)

    def fake_run_worker(coro: object, **kwargs: bool) -> None:
        close = getattr(coro, "close", None)
        if close:
            close()
        workers.append(kwargs)

    monkeypatch.setattr(widget, "query_one", fake_query)
    monkeypatch.setattr(widget, "run_worker", fake_run_worker)

    widget.update_slash_commands([("/pre", "", "")])
    assert "not initialized" in caplog.text

    widget.on_mount()
    assert widget._text_area is text_area
    assert widget._popup is popup
    assert text_area.focused == 1
    assert workers == [
        {"exclusive": False, "exit_on_error": False},
        {"exclusive": False, "exit_on_error": False},
    ]

    commands = [("/help", "Show help", "")]
    widget.update_slash_commands(commands)
    assert updated == [commands]


def test_chat_input_watch_mode_prompt_and_missing_prompt(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    widget, _text_area = _chat(tmp_path)
    prompt = Static(">")
    posted: list[str] = []
    widget.call_after_refresh = lambda callback: callback()  # type: ignore[method-assign]
    widget.post_message = lambda message: posted.append(message.mode)  # type: ignore[method-assign]
    widget.query_one = lambda *_args, **_kwargs: prompt  # type: ignore[method-assign]

    widget.watch_mode("shell")
    assert posted == ["shell"]
    assert prompt._Static__content == "$"  # noqa: SLF001

    widget.query_one = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (_ for _ in ()).throw(NoMatches("#prompt"))
    )
    widget.watch_mode("unknown")
    assert posted[-1] == "unknown"
    assert "No display glyph" in caplog.text
    assert "#prompt widget not found" in caplog.text


def test_chat_input_history_navigation_handlers(tmp_path: Path) -> None:
    widget, text_area = _chat(tmp_path)

    class History:
        def __init__(self) -> None:
            self.in_history = False

        def get_previous(self, current_text: str, *, query: str) -> str | None:
            assert current_text == "current"
            assert query == "current"
            self.in_history = True
            return "!pwd"

        def get_next(self) -> str | None:
            self.in_history = False
            return "/help"

    history = History()
    widget._history = history  # type: ignore[assignment]

    widget.on_chat_text_area_history_previous(ChatTextArea.HistoryPrevious("current"))
    assert widget.mode == "shell"
    assert text_area.text == "pwd"
    assert text_area._in_history is True

    widget.on_chat_text_area_history_next(ChatTextArea.HistoryNext())
    assert widget.mode == "command"
    assert text_area.text == "help"
    assert text_area._in_history is False


def test_chat_input_on_key_completion_and_mode_exits(
    tmp_path: Path,
) -> None:
    widget, text_area = _chat(tmp_path, text="run")
    widget.watch_mode = lambda _mode: None  # type: ignore[method-assign]
    submitted: list[str] = []
    resets: list[str] = []

    class Manager:
        def __init__(self, result: CompletionResult) -> None:
            self.result = result

        def reset(self) -> None:
            resets.append("reset")

        def on_key(
            self, _event: _FakeEvent, _text: str, _cursor: int
        ) -> CompletionResult:
            return self.result

    widget._submit_value = lambda value: submitted.append(value)  # type: ignore[method-assign]
    widget._completion_manager = Manager(CompletionResult.HANDLED)  # type: ignore[assignment]

    event = _FakeEvent(key="tab")
    asyncio.run(widget.on_key(event))  # type: ignore[arg-type]
    assert event.prevented == 1
    assert event.stopped == 1

    widget._completion_manager = Manager(CompletionResult.SUBMIT)  # type: ignore[assignment]
    event = _FakeEvent(key="enter")
    asyncio.run(widget.on_key(event))  # type: ignore[arg-type]
    assert submitted == ["run"]

    widget._completion_manager = Manager(CompletionResult.IGNORED)  # type: ignore[assignment]
    event = _FakeEvent(key="enter")
    asyncio.run(widget.on_key(event))  # type: ignore[arg-type]
    assert submitted == ["run", "run"]

    widget.mode = "command"
    text_area.text = ""
    text_area.cursor_location = (0, 0)
    widget.call_after_refresh = lambda callback: callback()  # type: ignore[method-assign]
    event = _FakeEvent(key="backspace")
    asyncio.run(widget.on_key(event))  # type: ignore[arg-type]
    assert widget.mode == "normal"
    assert resets[-1] == "reset"

    widget.mode = "shell"
    event = _FakeEvent(key="escape")
    asyncio.run(widget.on_key(event))  # type: ignore[arg-type]
    assert widget.mode == "normal"

    widget._completion_manager = None
    event = _FakeEvent(key="enter")
    asyncio.run(widget.on_key(event))  # type: ignore[arg-type]
    assert event.prevented == 0


def test_chat_input_on_key_ignored_enter_empty_value(tmp_path: Path) -> None:
    widget, text_area = _chat(tmp_path, text="   ")
    submitted: list[str] = []

    class Manager:
        def on_key(
            self, _event: _FakeEvent, _text: str, _cursor: int
        ) -> CompletionResult:
            return CompletionResult.IGNORED

    widget._completion_manager = Manager()  # type: ignore[assignment]
    widget._submit_value = submitted.append  # type: ignore[method-assign]
    event = _FakeEvent(key="enter")

    asyncio.run(widget.on_key(event))  # type: ignore[arg-type]

    assert submitted == []
    assert event.prevented == 0
    assert text_area.text == "   "


def test_completion_view_adapter_translates_completion_indices(tmp_path: Path) -> None:
    widget, text_area = _chat(tmp_path, text="help")
    adapter = _CompletionViewAdapter(widget)
    widget.mode = "command"
    widget._completion_prefix_len = 1
    widget._current_suggestions = []

    adapter.render_completion_suggestions([("/help", "Show help")], 0)
    adapter.replace_completion_range(0, 5, "/help")

    assert widget._current_suggestions == [("/help", "Show help")]
    assert text_area.text == "/help"
    adapter.clear_completion_suggestions()
    assert widget._current_suggestions == []


def test_history_entry_mode_and_exit_dismiss(tmp_path: Path) -> None:
    widget, _text_area = _chat(tmp_path)
    manager = _FakeManager()
    popup = _FakePopup()
    widget._completion_manager = manager  # type: ignore[assignment]
    widget._popup = popup  # type: ignore[assignment]

    assert ChatInput._history_entry_mode_and_text("!pwd") == ("shell", "pwd")
    assert ChatInput._history_entry_mode_and_text("/help") == ("command", "help")
    assert ChatInput._history_entry_mode_and_text("plain") == ("normal", "plain")

    widget.watch_mode = lambda _mode: None  # type: ignore[method-assign]
    widget.mode = "command"
    widget._completion_manager = manager  # type: ignore[assignment]
    assert widget.exit_mode() is True
    assert widget.mode == "normal"
    assert manager.resets == 1
    assert popup.hidden == 1
    assert widget.exit_mode() is False

    widget._current_suggestions = [("/help", "Show help")]
    assert widget.dismiss_completion() is True
    assert manager.resets == 2
    assert widget.dismiss_completion() is False
