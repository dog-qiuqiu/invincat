from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from textual.widgets import Static

from invincat_cli.widgets import thread_selector as selector_mod
from invincat_cli.widgets.thread_selector import (
    DeleteThreadConfirmScreen,
    ThreadOption,
    ThreadSelectorScreen,
    _active_sort_key,
    _apply_column_width,
    _collapse_whitespace,
    _format_column_value,
    _format_header_label,
    _get_column_labels,
    _get_column_toggle_labels,
    _get_format_fns,
    _header_cell_classes,
    _truncate_value,
    _visible_column_keys,
)


def _thread(
    thread_id: str,
    *,
    created_at: str = "2026-01-01T00:00:00Z",
    updated_at: str = "2026-01-02T00:00:00Z",
    checkpoint: str | None = "cp",
    prompt: str = "hello",
) -> dict[str, object]:
    return {
        "thread_id": thread_id,
        "agent_name": "agent",
        "message_count": 3,
        "created_at": created_at,
        "updated_at": updated_at,
        "git_branch": "main",
        "cwd": "/repo/project",
        "initial_prompt": prompt,
        "latest_checkpoint_id": checkpoint,
    }


class _BatchApp:
    def batch_update(self) -> _BatchApp:
        return self

    def __enter__(self) -> _BatchApp:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _AsyncContainer:
    def __init__(self) -> None:
        self.children: list[object] = []
        self.removed_children = False
        self.removed = False

    async def remove_children(self) -> None:
        self.removed_children = True
        self.children.clear()

    async def remove(self) -> None:
        self.removed = True

    async def mount(self, *children: object, **_kwargs: object) -> None:
        self.children.extend(children)


class _FakeContextContainer(_AsyncContainer):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__()
        self.args = args
        self.kwargs = kwargs

    def __enter__(self) -> _FakeContextContainer:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def mount(self, *children: object, **_kwargs: object) -> object:
        self.children.extend(children)

        class _Awaitable:
            def __await__(self) -> object:
                if False:
                    yield None
                return None

        return _Awaitable()


class _FakeStatic:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        self.styles = SimpleNamespace(width=None, min_width=None)

    def update(self, value: object) -> None:
        self.args = (value,)


@pytest.fixture(autouse=True)
def stable_thread_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    selector_mod._format_fns_cache = None
    selector_mod._column_widths_cache = None
    labels = {
        key: key.replace("_", " ").title() for key in selector_mod._COLUMN_LABELS_KEYS
    }
    monkeypatch.setattr(selector_mod, "_COLUMN_LABELS", labels)
    monkeypatch.setattr(
        selector_mod,
        "get_glyphs",
        lambda: SimpleNamespace(
            ellipsis="...",
            cursor=">",
            arrow_up="up",
            arrow_down="down",
            bullet="*",
        ),
    )
    monkeypatch.setattr(
        selector_mod,
        "t",
        lambda key, **kwargs: {
            "thread.title": "Threads",
            "thread.current_thread": f"current {kwargs.get('thread_id', '')}",
            "thread.navigate": "navigate",
            "thread.select_action": "select",
            "thread.focus_options": "filters",
            "thread.toggle_option": "toggle",
            "thread.delete_action": "delete",
            "thread.cancel_action": "cancel",
            "thread.showing_limit": f"showing {kwargs.get('limit')}",
            "thread.sort_updated": "updated",
            "thread.sort_created": "created",
            "thread.sort_by": f"Sort by {kwargs.get('field')}",
        }.get(key, key),
    )
    monkeypatch.setattr(
        selector_mod,
        "_get_format_fns",
        lambda: (
            lambda value: f"path:{value}" if value else "",
            lambda value: f"rel:{value}" if value else "",
            lambda value: f"abs:{value}" if value else "",
        ),
    )
    monkeypatch.setattr(
        "invincat_cli.model_config.load_thread_config",
        lambda: SimpleNamespace(
            columns={
                "thread_id": True,
                "agent_name": True,
                "messages": True,
                "created_at": True,
                "updated_at": True,
                "git_branch": True,
                "cwd": True,
                "initial_prompt": True,
            },
            relative_time=False,
            sort_order="updated_at",
        ),
    )


def test_thread_selector_formatting_helpers() -> None:
    selector_mod._format_fns_cache = (
        lambda value: f"cached-path:{value}",
        lambda value: f"cached-rel:{value}",
        lambda value: f"cached-abs:{value}",
    )
    assert _get_format_fns() is selector_mod._format_fns_cache
    selector_mod._format_fns_cache = None
    assert len(_get_format_fns()) == 3

    selector_mod._COLUMN_LABELS["thread_id"] = "Thread identifier"
    assert _format_header_label("thread_id").endswith("...")
    assert _get_column_labels()["thread_id"] == "thread.column_thread_id"
    assert _get_column_toggle_labels()["thread_id"] == "thread.column_thread_id"

    columns = {"updated_at": True, "thread_id": True, "cwd": False}
    assert _active_sort_key(True) == "updated_at"
    assert _active_sort_key(False) == "created_at"
    assert _visible_column_keys(columns) == ["thread_id", "updated_at"]
    assert _collapse_whitespace(" a\n b\t c ") == "a b c"
    assert _truncate_value("abcdef", None) == "abcdef"
    assert _truncate_value("abcdef", 4) == "a..."
    assert _truncate_value("abcdef", 2) == "ab"
    assert _header_cell_classes("updated_at", sort_key="updated_at").endswith(
        "thread-cell-sorted"
    )

    thread = _thread("abc-def", prompt="hello\nworld")
    assert _format_column_value(thread, "thread_id") == "abcdef"
    assert _format_column_value(thread, "agent_name") == "agent"
    assert _format_column_value(thread, "messages") == "3"
    no_count = _thread("abc")
    no_count.pop("message_count")
    assert _format_column_value(no_count, "messages") == "..."
    assert _format_column_value(thread, "created_at") == "abs:2026-01-01T00:00:00Z"
    assert _format_column_value(thread, "updated_at", relative_time=True) == (
        "rel:2026-01-02T00:00:00Z"
    )
    assert _format_column_value(thread, "cwd") == "path:/repo/project"
    assert _format_column_value(thread, "initial_prompt") == "hello world"
    assert _format_column_value(thread, "unknown") == ""


def test_apply_column_width_sets_fixed_and_min_width() -> None:
    cell = Static("")
    _apply_column_width(cell, "agent_name", {"agent_name": 9})
    assert cell.styles.width.value == 9
    assert cell.styles.min_width.value == 9

    flex = Static("")
    _apply_column_width(flex, "initial_prompt", {"initial_prompt": None})
    assert flex.styles.width is None


def test_thread_option_compose_selection_and_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread = _thread("thread-1")
    option = ThreadOption(
        thread,
        2,
        columns={"thread_id": True, "agent_name": True},
        column_widths={"thread_id": 10, "agent_name": 8},
        selected=True,
        current=False,
    )
    cells = list(option.compose())
    assert [str(cell._Static__content) for cell in cells] == [">", "thread1", "agent"]  # noqa: SLF001

    cached = ThreadOption(
        thread,
        0,
        columns={"thread_id": True},
        column_widths={"thread_id": 10},
        selected=False,
        current=False,
        cell_text={("thread-1", "thread_id"): "cached"},
    )
    assert str(list(cached.compose())[1]._Static__content) == "cached"  # noqa: SLF001

    cursor = SimpleNamespace(
        updated=[], update=lambda value: cursor.updated.append(value)
    )
    monkeypatch.setattr(option, "query_one", lambda *_args: cursor)
    option.set_selected(False)
    assert cursor.updated[-1] == ""
    option.set_selected(True)
    assert cursor.updated[-1] == ">"

    monkeypatch.setattr(
        option,
        "query_one",
        lambda *_args: (_ for _ in ()).throw(selector_mod.NoMatches("missing")),
    )
    option.set_selected(False)

    posted: list[tuple[str, int]] = []
    monkeypatch.setattr(
        option,
        "post_message",
        lambda message: posted.append((message.thread_id, message.index)),
    )
    event = SimpleNamespace(stopped=False, stop=lambda: setattr(event, "stopped", True))
    option.on_click(event)  # type: ignore[arg-type]
    assert event.stopped is True
    assert posted == [("thread-1", 2)]


def test_delete_confirm_screen_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    screen = DeleteThreadConfirmScreen("thread-1")
    dismissed: list[bool] = []
    monkeypatch.setattr(screen, "dismiss", lambda value: dismissed.append(value))

    screen.action_confirm()
    screen.action_cancel()

    assert dismissed == [True, False]


def test_delete_confirm_screen_compose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(selector_mod, "Vertical", _FakeContextContainer)

    screen = DeleteThreadConfirmScreen("thread-1")

    composed = list(screen.compose())

    assert len(composed) == 2
    assert all(isinstance(item, Static) for item in composed)


def test_thread_selector_init_title_help_and_column_width_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("invincat_cli.sessions.get_thread_limit", lambda: 2)
    threads = [
        _thread("b", updated_at="2026-01-03"),
        _thread("a", updated_at="2026-01-02"),
    ]
    screen = ThreadSelectorScreen(
        current_thread="a",
        thread_limit=None,
        initial_threads=threads,
    )

    assert [thread["thread_id"] for thread in screen._filtered_threads] == ["b", "a"]
    assert screen._selected_index == 1
    assert screen._switch_id("cwd") == "thread-column-cwd"
    assert screen._switch_column_key("thread-column-cwd") == "cwd"
    assert screen._switch_column_key("other") is None
    assert screen._build_title() == "Threads (current a)"
    monkeypatch.setattr(
        selector_mod.theme,
        "get_theme_colors",
        lambda _screen: SimpleNamespace(primary="#ffffff"),
    )
    linked_title = screen._build_title("https://example.test/thread/a")
    assert isinstance(linked_title, selector_mod.Content)
    assert "showing 2" in screen._build_help_text()
    assert screen._format_sort_toggle_label() == "Sort by updated"
    screen._sort_by_updated = False
    assert screen._format_sort_toggle_label() == "Sort by created"
    screen._thread_limit = 99
    assert screen._effective_thread_limit() == 99

    widths = screen._compute_column_widths()
    assert widths["agent_name"] is not None
    assert screen._cell_text
    cell_text = dict(screen._cell_text)
    assert screen._compute_column_widths() == widths
    assert screen._cell_text == cell_text

    no_current = ThreadSelectorScreen(initial_threads=[_thread("a")])
    assert no_current._build_title() == "Threads"

    monkeypatch.setattr(
        "invincat_cli.model_config.load_thread_config",
        lambda: SimpleNamespace(
            columns={
                "thread_id": True,
                "agent_name": True,
                "messages": True,
                "created_at": True,
                "updated_at": True,
                "git_branch": True,
                "cwd": True,
                "initial_prompt": True,
            },
            relative_time=False,
            sort_order="created_at",
        ),
    )
    created_sorted = ThreadSelectorScreen(
        initial_threads=[
            _thread("older", created_at="2026-01-01"),
            _thread("newer", created_at="2026-01-02"),
        ]
    )
    assert [thread["thread_id"] for thread in created_sorted._filtered_threads] == [
        "newer",
        "older",
    ]


def test_thread_selector_cached_filter_widgets(monkeypatch: pytest.MonkeyPatch) -> None:
    screen = ThreadSelectorScreen(initial_threads=[_thread("a")])
    filter_input = SimpleNamespace(id="thread-filter")
    sort = SimpleNamespace(id=selector_mod._SORT_SWITCH_ID)
    relative = SimpleNamespace(id=selector_mod._RELATIVE_TIME_SWITCH_ID)
    columns = {
        f"{selector_mod._SWITCH_ID_PREFIX}{key}": SimpleNamespace(id=key)
        for key in selector_mod._COLUMN_ORDER
    }

    def fake_query(selector: str, _cls: object) -> object:
        if selector == "#thread-filter":
            return filter_input
        if selector == f"#{selector_mod._SORT_SWITCH_ID}":
            return sort
        if selector == f"#{selector_mod._RELATIVE_TIME_SWITCH_ID}":
            return relative
        return columns[selector.removeprefix("#")]

    monkeypatch.setattr(screen, "query_one", fake_query)

    assert screen._get_filter_input() is filter_input
    assert screen._get_filter_input() is filter_input
    order = screen._filter_focus_order()
    assert order[0] is filter_input
    assert order[1] is sort
    assert order[2] is relative
    assert screen._filter_focus_order() is order


def test_thread_selector_compose_initial_empty_and_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(selector_mod, "Vertical", _FakeContextContainer)
    monkeypatch.setattr(selector_mod, "Horizontal", _FakeContextContainer)
    monkeypatch.setattr(selector_mod, "VerticalScroll", _FakeContextContainer)

    populated = ThreadSelectorScreen(initial_threads=[_thread("a")])
    populated_widgets = list(populated.compose())
    assert populated_widgets

    empty = ThreadSelectorScreen(initial_threads=[])
    empty_widgets = list(empty.compose())
    assert empty_widgets

    loading = ThreadSelectorScreen(initial_threads=None)
    loading_widgets = list(loading.compose())
    assert loading_widgets


def test_thread_selector_filter_sort_search_and_checkpoint_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = [_thread("a", checkpoint="1"), _thread("b", checkpoint="2")]
    same = [_thread("a", checkpoint="1"), _thread("b", checkpoint="2")]
    changed = [_thread("a", checkpoint="x"), _thread("b", checkpoint="2")]
    assert ThreadSelectorScreen._threads_match(old, same)
    assert not ThreadSelectorScreen._threads_match(old, changed)
    assert not ThreadSelectorScreen._threads_match(old, same[:1])
    assert not ThreadSelectorScreen._threads_match(
        old, [_thread("x", checkpoint="1"), _thread("b", checkpoint="2")]
    )

    long_prompt = "x" * 500
    assert (
        len(ThreadSelectorScreen._get_search_text(_thread("tid", prompt=long_prompt)))
        == 200
    )

    threads = [
        _thread(
            "older", updated_at="2026-01-01", created_at="2026-01-01", prompt="alpha"
        ),
        _thread(
            "newer", updated_at="2026-01-03", created_at="2026-01-03", prompt="beta"
        ),
    ]
    assert [
        t["thread_id"]
        for t in ThreadSelectorScreen._compute_filtered("", threads, True)
    ] == [
        "newer",
        "older",
    ]
    assert [
        t["thread_id"]
        for t in ThreadSelectorScreen._compute_filtered("alpha", threads, True)
    ] == ["older"]

    screen = ThreadSelectorScreen(initial_threads=[{"thread_id": "x"}])
    screen._columns["messages"] = True
    screen._columns["initial_prompt"] = True
    assert screen._pending_checkpoint_fields() == (True, True)
    screen._threads[0]["message_count"] = 0
    screen._threads[0]["initial_prompt"] = ""
    assert screen._pending_checkpoint_fields() == (False, False)

    workers: list[object] = []
    screen._schedule_checkpoint_enrichment()
    assert workers == []
    monkeypatch.setattr(
        screen,
        "run_worker",
        lambda worker, **_kwargs: workers.append(worker),
    )
    screen._columns["messages"] = True
    screen._threads[0].pop("message_count")
    screen._schedule_list_rebuild()
    screen._schedule_checkpoint_enrichment()
    assert workers == [screen._build_list, screen._load_checkpoint_details]

    class BrokenMatcher:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("matcher failed")

    monkeypatch.setattr(selector_mod, "Matcher", BrokenMatcher)
    fallback = ThreadSelectorScreen._compute_filtered("alpha", threads, True)
    assert [thread["thread_id"] for thread in fallback] == ["newer", "older"]

    screen._threads = threads
    screen._filter_text = "alpha"
    screen._update_filtered_list()
    assert [thread["thread_id"] for thread in screen._filtered_threads] == [
        "newer",
        "older",
    ]


def test_thread_selector_input_key_and_focus_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = ThreadSelectorScreen(initial_threads=[_thread("a")], thread_limit=3)
    workers: list[object] = []
    timers: list[tuple[float, object]] = []
    monkeypatch.setattr(
        screen,
        "run_worker",
        lambda worker, **_kwargs: workers.append(worker),
    )

    screen.on_input_changed(SimpleNamespace(value="alpha"))
    assert screen._filter_text == "alpha"
    assert workers == [screen._filter_and_build]

    selected: list[str] = []
    monkeypatch.setattr(screen, "action_select", lambda: selected.append("select"))
    submitted = SimpleNamespace(stopped=False)
    submitted.stop = lambda: setattr(submitted, "stopped", True)
    screen.on_input_submitted(submitted)
    assert submitted.stopped is True
    assert selected == ["select"]

    class FakeSelection:
        @classmethod
        def cursor(cls, value: int) -> tuple[str, int]:
            return ("cursor", value)

    filter_input = SimpleNamespace(
        has_focus=False,
        focused=0,
        inserted=[],
        value="ab",
        selection=FakeSelection(),
        focus=lambda: setattr(filter_input, "focused", filter_input.focused + 1),
        insert_text_at_cursor=lambda value: filter_input.inserted.append(value),
    )
    monkeypatch.setattr(screen, "_get_filter_input", lambda: filter_input)
    monkeypatch.setattr(
        screen, "set_timer", lambda delay, callback: timers.append((delay, callback))
    )
    key_event = SimpleNamespace(character="x", stopped=False)
    key_event.stop = lambda: setattr(key_event, "stopped", True)
    screen.on_key(key_event)
    assert filter_input.focused == 1
    assert filter_input.inserted == ["x"]
    assert key_event.stopped is True
    assert timers[0][0] == 0.01

    screen._collapse_search_selection()
    assert filter_input.selection == ("cursor", 2)

    screen._confirming_delete = True
    blocked = SimpleNamespace(character="y", stopped=False)
    blocked.stop = lambda: setattr(blocked, "stopped", True)
    screen.on_key(blocked)
    assert blocked.stopped is False
    screen._confirming_delete = False

    filter_input.has_focus = True
    focused_event = SimpleNamespace(character="z", stopped=False)
    focused_event.stop = lambda: setattr(focused_event, "stopped", True)
    screen.on_key(focused_event)
    assert focused_event.stopped is False

    filter_input.has_focus = False
    non_alpha = SimpleNamespace(character="1", stopped=False)
    non_alpha.stop = lambda: setattr(non_alpha, "stopped", True)
    screen.on_key(non_alpha)
    assert non_alpha.stopped is False

    focused: list[str] = []
    controls = [
        SimpleNamespace(name="search", focus=lambda: focused.append("search")),
        SimpleNamespace(name="sort", focus=lambda: focused.append("sort")),
        SimpleNamespace(name="rel", focus=lambda: focused.append("rel")),
    ]
    monkeypatch.setattr(screen, "_filter_focus_order", lambda: controls)
    monkeypatch.setattr(ThreadSelectorScreen, "focused", property(lambda _self: None))
    screen.action_focus_next_filter()
    screen.action_focus_previous_filter()
    assert focused == ["search", "rel"]
    screen._confirming_delete = True
    screen.action_focus_next_filter()
    screen.action_focus_previous_filter()
    assert focused == ["search", "rel"]
    screen._confirming_delete = False

    monkeypatch.setattr(
        ThreadSelectorScreen, "focused", property(lambda _self: controls[0])
    )
    screen.action_focus_next_filter()
    assert focused[-1] == "sort"
    screen.action_focus_previous_filter()
    assert focused[-1] == "rel"


def test_thread_selector_checkbox_routes_and_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = ThreadSelectorScreen(initial_threads=[_thread("a"), _thread("b")])
    workers: list[object] = []
    calls: list[str] = []
    monkeypatch.setattr(
        screen, "run_worker", lambda worker, **_kwargs: workers.append(worker)
    )
    monkeypatch.setattr(screen, "_update_help_widgets", lambda: calls.append("help"))
    monkeypatch.setattr(screen, "_schedule_list_rebuild", lambda: calls.append("list"))
    monkeypatch.setattr(
        screen,
        "_schedule_checkpoint_enrichment",
        lambda: calls.append("checkpoints"),
    )
    monkeypatch.setattr(
        screen, "_persist_sort_order", lambda order: calls.append(order)
    )

    screen.on_checkbox_changed(
        SimpleNamespace(
            checkbox=SimpleNamespace(id=selector_mod._SORT_SWITCH_ID),
            value=True,
        )
    )
    assert calls == []

    screen.on_checkbox_changed(
        SimpleNamespace(
            checkbox=SimpleNamespace(id=selector_mod._SORT_SWITCH_ID),
            value=False,
        )
    )
    assert screen._sort_by_updated is False
    assert calls[:3] == ["help", "list", "created_at"]

    screen.on_checkbox_changed(
        SimpleNamespace(
            checkbox=SimpleNamespace(id=selector_mod._RELATIVE_TIME_SWITCH_ID),
            value=True,
        )
    )
    assert screen._relative_time is True
    assert calls[-1] == "list"
    assert workers
    workers.pop().close()

    screen.on_checkbox_changed(
        SimpleNamespace(
            checkbox=SimpleNamespace(id=selector_mod._RELATIVE_TIME_SWITCH_ID),
            value=True,
        )
    )
    assert calls[-1] == "list"

    screen.on_checkbox_changed(
        SimpleNamespace(checkbox=SimpleNamespace(id="not-a-column"), value=True)
    )
    assert calls[-1] == "list"

    screen._columns["messages"] = False
    screen.on_checkbox_changed(
        SimpleNamespace(
            checkbox=SimpleNamespace(id=screen._switch_id("messages")),
            value=True,
        )
    )
    assert screen._columns["messages"] is True
    assert "checkpoints" in calls
    assert calls[-1] == "list"
    assert workers
    workers.pop().close()

    screen.on_checkbox_changed(
        SimpleNamespace(
            checkbox=SimpleNamespace(id=screen._switch_id("messages")),
            value=True,
        )
    )
    assert calls[-1] == "list"


def test_thread_selector_selection_actions_and_delete_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = ThreadSelectorScreen(
        initial_threads=[_thread("a"), _thread("b"), _thread("c")],
    )
    selected: list[bool] = []
    widgets = [
        SimpleNamespace(
            set_selected=lambda value, idx=idx: selected.append((idx, value)),
            scroll_visible=lambda: None,
        )
        for idx in range(3)
    ]
    screen._option_widgets = widgets  # type: ignore[assignment]
    screen._selected_index = 0
    scroll = SimpleNamespace(
        home=0,
        size=SimpleNamespace(height=2),
        scroll_home=lambda *, animate: setattr(scroll, "home", scroll.home + 1),
    )
    monkeypatch.setattr(screen, "query_one", lambda *_args: scroll)

    empty_screen = ThreadSelectorScreen(initial_threads=[])
    empty_screen._move_selection(1)
    assert empty_screen._selected_index == 0

    screen._selected_index = 1
    screen.action_move_up()
    assert screen._selected_index == 0
    screen.action_move_down()
    assert screen._selected_index == 1
    screen.action_page_down()
    assert screen._selected_index == 2
    screen.action_page_up()
    assert screen._selected_index == 0
    assert scroll.home == 2

    dismissed: list[str] = []
    monkeypatch.setattr(screen, "dismiss", lambda value: dismissed.append(value))
    screen.action_select()
    assert dismissed == ["a"]

    screen._confirming_delete = True
    screen.action_move_up()
    screen.action_move_down()
    screen.action_page_up()
    screen.action_page_down()
    screen.action_select()
    assert dismissed == ["a"]
    screen._confirming_delete = False

    calls: list[str] = []
    monkeypatch.setattr(screen, "_update_help_widgets", lambda: calls.append("help"))
    monkeypatch.setattr(screen, "_schedule_list_rebuild", lambda: calls.append("list"))
    monkeypatch.setattr(
        screen, "_persist_sort_order", lambda order: calls.append(order)
    )
    screen.action_toggle_sort()
    assert calls == ["help", "list", "created_at"]
    screen._confirming_delete = True
    screen.action_toggle_sort()
    assert calls == ["help", "list", "created_at"]
    screen._confirming_delete = False

    zero_scroll = SimpleNamespace(size=SimpleNamespace(height=0))
    monkeypatch.setattr(screen, "query_one", lambda *_args: zero_scroll)
    assert screen._visible_page_size() == 10
    monkeypatch.setattr(
        screen,
        "query_one",
        lambda *_args: (_ for _ in ()).throw(selector_mod.NoMatches("missing")),
    )
    assert screen._visible_page_size() == 10

    pushed: list[tuple[str, object]] = []
    app = SimpleNamespace(
        exit_called=0,
        push_screen=lambda modal, callback: pushed.append(
            (modal._delete_thread_id, callback)
        ),
        exit=lambda: setattr(app, "exit_called", app.exit_called + 1),
    )
    monkeypatch.setattr(ThreadSelectorScreen, "app", property(lambda _self: app))

    screen.action_delete_thread()
    assert screen.is_delete_confirmation_open is True
    assert pushed[0][0] == "a"
    screen.action_delete_thread()
    assert len(pushed) == 1

    workers: list[object] = []
    focused = SimpleNamespace(
        focused=0, focus=lambda: setattr(focused, "focused", focused.focused + 1)
    )
    monkeypatch.setattr(
        screen, "run_worker", lambda coro, **_kwargs: workers.append(coro)
    )
    monkeypatch.setattr(screen, "_get_filter_input", lambda: focused)
    screen._on_delete_confirmed("a", False)
    assert focused.focused == 1
    screen._on_delete_confirmed("a", True)
    assert screen.is_delete_confirmation_open is False
    assert workers
    workers[0].close()

    empty = ThreadSelectorScreen(initial_threads=[])
    empty.action_delete_thread()
    assert app.exit_called == 1


def test_thread_selector_filter_and_build_async(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = ThreadSelectorScreen(
        current_thread="b",
        initial_threads=[
            _thread("a", updated_at="2026-01-01", prompt="alpha"),
            _thread("b", updated_at="2026-01-02", prompt="beta"),
        ],
    )
    built: list[bool] = []

    async def fake_to_thread(func, *args):
        return func(*args)

    async def fake_build_list(*, recompute_widths: bool = True) -> None:
        built.append(recompute_widths)

    monkeypatch.setattr(selector_mod.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(screen, "_build_list", fake_build_list)

    screen._filter_text = "alpha"
    asyncio.run(screen._filter_and_build())
    assert [thread["thread_id"] for thread in screen._filtered_threads] == ["a"]
    assert screen._selected_index == 0
    assert built == [False]

    screen._filter_text = ""
    asyncio.run(screen._filter_and_build())
    assert [thread["thread_id"] for thread in screen._filtered_threads] == ["b", "a"]
    assert screen._selected_index == 0
    assert built == [False, False]


def test_thread_selector_checkpoint_loading_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = ThreadSelectorScreen(
        initial_threads=[
            {"thread_id": "a", "latest_checkpoint_id": "1"},
            {"thread_id": "b", "latest_checkpoint_id": "2"},
        ],
    )
    screen._columns["messages"] = True
    screen._columns["initial_prompt"] = True
    calls: list[str] = []

    async def fake_populate(
        threads,
        *,
        include_message_count: bool,
        include_initial_prompt: bool,
    ) -> None:
        calls.append(f"{include_message_count}:{include_initial_prompt}")
        for thread in threads:
            thread["message_count"] = 2
            thread["initial_prompt"] = f"prompt {thread['thread_id']}"

    monkeypatch.setattr(
        "invincat_cli.sessions.populate_thread_checkpoint_details",
        fake_populate,
    )

    assert asyncio.run(screen._populate_visible_checkpoint_details()) == (True, True)
    assert calls == ["True:True"]
    assert screen._threads[0]["message_count"] == 2
    assert asyncio.run(screen._populate_visible_checkpoint_details()) == (
        False,
        False,
    )

    async def fake_populate_visible() -> tuple[bool, bool]:
        return False, True

    monkeypatch.setattr(
        screen, "_populate_visible_checkpoint_details", fake_populate_visible
    )
    screen._filter_text = "prompt"
    screen._filtered_threads = list(screen._threads)
    screen._selected_index = 1
    rebuilt: list[str] = []
    monkeypatch.setattr(
        screen, "_schedule_list_rebuild", lambda: rebuilt.append("list")
    )
    asyncio.run(screen._load_checkpoint_details())
    assert rebuilt == ["list"]

    screen._threads = []
    asyncio.run(screen._load_checkpoint_details())

    screen._threads = [_thread("x")]

    async def oserror_populate() -> tuple[bool, bool]:
        raise OSError("checkpoint db down")

    monkeypatch.setattr(
        screen, "_populate_visible_checkpoint_details", oserror_populate
    )
    asyncio.run(screen._load_checkpoint_details())

    async def generic_populate() -> tuple[bool, bool]:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(
        screen, "_populate_visible_checkpoint_details", generic_populate
    )
    asyncio.run(screen._load_checkpoint_details())

    refreshed: list[str] = []

    async def no_prompt_populate() -> tuple[bool, bool]:
        return True, False

    monkeypatch.setattr(
        screen, "_populate_visible_checkpoint_details", no_prompt_populate
    )
    monkeypatch.setattr(
        screen, "_refresh_cell_labels", lambda: refreshed.append("cells")
    )
    asyncio.run(screen._load_checkpoint_details())
    assert refreshed == ["cells"]


def test_thread_selector_load_threads_success_and_error_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.sessions as sessions

    async def list_threads_success(**kwargs: object) -> list[dict[str, object]]:
        assert kwargs["include_message_count"] is False
        return [_thread("loaded", updated_at="2026-02-01")]

    monkeypatch.setattr(sessions, "list_threads", list_threads_success)
    monkeypatch.setattr(sessions, "get_thread_limit", lambda: 5)
    monkeypatch.setattr(
        sessions,
        "apply_cached_thread_message_counts",
        lambda threads: threads[0].update({"message_count": 7}),
    )
    monkeypatch.setattr(
        sessions,
        "apply_cached_thread_initial_prompts",
        lambda threads: threads[0].update({"initial_prompt": "cached"}),
    )

    screen = ThreadSelectorScreen(current_thread="loaded", initial_threads=None)
    calls: list[str] = []

    async def populate() -> tuple[bool, bool]:
        calls.append("populate")
        return False, False

    async def build_table() -> None:
        calls.append("table")

    monkeypatch.setattr(screen, "_populate_visible_checkpoint_details", populate)
    monkeypatch.setattr(screen, "_build_table_pane", build_table)
    monkeypatch.setattr(
        screen, "_schedule_checkpoint_enrichment", lambda: calls.append("enrich")
    )
    monkeypatch.setattr(screen, "_resolve_thread_url", lambda: calls.append("url"))

    asyncio.run(screen._load_threads())

    assert screen._has_initial_threads is True
    assert screen._threads[0]["message_count"] == 7
    assert calls == ["populate", "table", "enrich", "url"]

    initial = ThreadSelectorScreen(initial_threads=[_thread("same", checkpoint="1")])
    initial._option_widgets = [
        SimpleNamespace(
            thread_id="same",
            thread=_thread("same", checkpoint="1"),
            query_one=lambda *_args: (_ for _ in ()).throw(
                selector_mod.NoMatches("missing")
            ),
        )
    ]  # type: ignore[assignment]

    async def list_threads_same(**_kwargs: object) -> list[dict[str, object]]:
        return [_thread("same", checkpoint="1")]

    monkeypatch.setattr(sessions, "list_threads", list_threads_same)
    refreshed: list[str] = []
    monkeypatch.setattr(initial, "_refresh_cell_labels", lambda: refreshed.append("r"))
    monkeypatch.setattr(initial, "_schedule_checkpoint_enrichment", lambda: None)
    asyncio.run(initial._load_threads())
    assert refreshed == ["r"]

    changed = ThreadSelectorScreen(initial_threads=[_thread("old")])
    built: list[str] = []

    async def list_threads_changed(**_kwargs: object) -> list[dict[str, object]]:
        return [_thread("new", checkpoint="2")]

    monkeypatch.setattr(sessions, "list_threads", list_threads_changed)

    async def build_list() -> None:
        built.append("list")

    monkeypatch.setattr(changed, "_build_list", build_list)
    monkeypatch.setattr(changed, "_schedule_checkpoint_enrichment", lambda: None)
    asyncio.run(changed._load_threads())
    assert built == ["list"]

    error_screen = ThreadSelectorScreen(initial_threads=[])
    shown: list[str] = []

    async def show_mount_error(detail: str) -> None:
        shown.append(detail)

    monkeypatch.setattr(error_screen, "_show_mount_error", show_mount_error)

    async def broken_os(**_kwargs: object) -> list[dict[str, object]]:
        raise OSError("db down")

    monkeypatch.setattr(sessions, "list_threads", broken_os)
    asyncio.run(error_screen._load_threads())
    assert shown == ["db down"]

    async def broken_generic(**_kwargs: object) -> list[dict[str, object]]:
        raise RuntimeError("boom")

    monkeypatch.setattr(sessions, "list_threads", broken_generic)
    asyncio.run(error_screen._load_threads())
    assert shown[-1] == "boom"

    preload_error = ThreadSelectorScreen(initial_threads=None)
    calls.clear()
    monkeypatch.setattr(sessions, "list_threads", list_threads_success)

    async def preload_oserror() -> tuple[bool, bool]:
        raise OSError("checkpoint unavailable")

    monkeypatch.setattr(
        preload_error, "_populate_visible_checkpoint_details", preload_oserror
    )
    monkeypatch.setattr(preload_error, "_build_table_pane", build_table)
    monkeypatch.setattr(preload_error, "_schedule_checkpoint_enrichment", lambda: None)
    asyncio.run(preload_error._load_threads())
    assert calls == ["table"]

    preload_generic = ThreadSelectorScreen(initial_threads=None)
    calls.clear()

    async def preload_runtime_error() -> tuple[bool, bool]:
        raise RuntimeError("checkpoint failed")

    monkeypatch.setattr(
        preload_generic,
        "_populate_visible_checkpoint_details",
        preload_runtime_error,
    )
    monkeypatch.setattr(preload_generic, "_build_table_pane", build_table)
    monkeypatch.setattr(
        preload_generic, "_schedule_checkpoint_enrichment", lambda: None
    )
    asyncio.run(preload_generic._load_threads())
    assert calls == ["table"]


def test_thread_selector_mount_and_missing_dom_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    colors = SimpleNamespace(success="#00ff00")
    monkeypatch.setattr(selector_mod.theme, "get_theme_colors", lambda _screen: colors)
    monkeypatch.setattr(selector_mod, "is_ascii_mode", lambda: True)

    shell = SimpleNamespace(styles=SimpleNamespace(border=None))
    filter_input = SimpleNamespace(focused=0)
    filter_input.focus = lambda: setattr(
        filter_input, "focused", filter_input.focused + 1
    )
    initial = ThreadSelectorScreen(current_thread="a", initial_threads=[_thread("a")])
    after_refresh: list[object] = []
    resolved: list[str] = []

    monkeypatch.setattr(
        initial,
        "query_one",
        lambda selector, *_args: (
            shell if selector == "#thread-selector-shell" else None
        ),
    )
    monkeypatch.setattr(initial, "_get_filter_input", lambda: filter_input)
    monkeypatch.setattr(initial, "_filter_focus_order", lambda: [])
    monkeypatch.setattr(initial, "_resolve_thread_url", lambda: resolved.append("url"))
    monkeypatch.setattr(
        initial, "call_after_refresh", lambda callback: after_refresh.append(callback)
    )
    asyncio.run(initial.on_mount())

    assert shell.styles.border[0] == "ascii"
    assert filter_input.focused == 1
    assert after_refresh == [
        initial._scroll_selected_into_view,
        initial._start_thread_load,
    ]
    assert resolved == ["url"]

    no_initial = ThreadSelectorScreen(initial_threads=None)
    workers: list[object] = []
    monkeypatch.setattr(no_initial, "_get_filter_input", lambda: filter_input)
    monkeypatch.setattr(no_initial, "_filter_focus_order", lambda: [])
    monkeypatch.setattr(
        no_initial,
        "query_one",
        lambda selector, *_args: (
            shell if selector == "#thread-selector-shell" else None
        ),
    )
    monkeypatch.setattr(
        no_initial, "run_worker", lambda worker, **_kwargs: workers.append(worker)
    )
    asyncio.run(no_initial.on_mount())
    assert workers == [no_initial._load_threads]

    monkeypatch.setattr(
        ThreadSelectorScreen, "is_attached", property(lambda _self: False)
    )
    no_initial._start_thread_load()
    assert workers == [no_initial._load_threads]
    monkeypatch.setattr(
        ThreadSelectorScreen, "is_attached", property(lambda _self: True)
    )
    no_initial._start_thread_load()
    assert workers == [no_initial._load_threads, no_initial._load_threads]


def test_thread_selector_mount_error_and_early_return_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = ThreadSelectorScreen(initial_threads=[_thread("a")])
    focused: list[str] = []
    monkeypatch.setattr(screen, "focus", lambda: focused.append("focus"))

    scroll = _AsyncContainer()
    monkeypatch.setattr(screen, "query_one", lambda *_args: scroll)
    asyncio.run(screen._show_mount_error("db down"))
    assert scroll.removed_children is True
    assert scroll.children
    assert focused == ["focus"]

    overlay = _AsyncContainer()

    def query_overlay(selector: str, *_args: object) -> object:
        if selector == ".thread-list":
            raise selector_mod.NoMatches("missing list")
        if selector == ".thread-loading-overlay":
            return overlay
        raise selector_mod.NoMatches("missing")

    monkeypatch.setattr(screen, "query_one", query_overlay)
    asyncio.run(screen._show_mount_error("load down"))
    assert overlay.children

    monkeypatch.setattr(
        screen,
        "query_one",
        lambda *_args: (_ for _ in ()).throw(selector_mod.NoMatches("missing")),
    )
    asyncio.run(screen._show_mount_error("hidden"))

    monkeypatch.setattr(
        screen,
        "query_one",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("dom failed")),
    )
    asyncio.run(screen._show_mount_error("broken"))

    worker_calls: list[object] = []
    monkeypatch.setattr(
        screen, "run_worker", lambda worker, **_kwargs: worker_calls.append(worker)
    )
    screen._resolve_thread_url()
    assert worker_calls == [screen._fetch_thread_url]

    monkeypatch.setattr(
        screen,
        "query_one",
        lambda *_args: (_ for _ in ()).throw(selector_mod.NoMatches("missing")),
    )
    asyncio.run(screen._build_list())
    asyncio.run(screen._rebuild_header())

    help_calls: list[str] = []
    monkeypatch.setattr(
        screen, "_schedule_header_rebuild", lambda: help_calls.append("header")
    )
    screen._update_help_widgets()
    assert help_calls == ["header"]


def test_thread_selector_build_table_pane_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(selector_mod, "Vertical", _FakeContextContainer)
    monkeypatch.setattr(selector_mod, "Horizontal", _FakeContextContainer)
    monkeypatch.setattr(selector_mod, "VerticalScroll", _FakeContextContainer)
    monkeypatch.setattr(selector_mod, "Static", _FakeStatic)

    missing_body = ThreadSelectorScreen(initial_threads=[])
    monkeypatch.setattr(
        missing_body,
        "query_one",
        lambda *_args: (_ for _ in ()).throw(selector_mod.NoMatches("missing")),
    )
    asyncio.run(missing_body._build_table_pane())

    body = _AsyncContainer()
    loading = _AsyncContainer()
    empty = ThreadSelectorScreen(initial_threads=[])

    def query_empty(selector: str, *_args: object) -> object:
        if selector == ".thread-selector-body":
            return body
        if selector == "#thread-loading-container":
            return loading
        raise selector_mod.NoMatches("missing")

    monkeypatch.setattr(empty, "query_one", query_empty)
    asyncio.run(empty._build_table_pane())
    assert loading.removed is True
    assert body.children
    table_pane = body.children[0]
    assert isinstance(table_pane, _FakeContextContainer)
    scroll = table_pane.children[-1]
    assert isinstance(scroll, _FakeContextContainer)
    assert screen_has_empty_thread_label(scroll)

    populated = ThreadSelectorScreen(initial_threads=[_thread("a")])
    body = _AsyncContainer()
    loading = _AsyncContainer()
    after_refresh: list[object] = []
    monkeypatch.setattr(populated, "query_one", query_empty)
    monkeypatch.setattr(
        populated, "call_after_refresh", lambda callback: after_refresh.append(callback)
    )
    asyncio.run(populated._build_table_pane())
    assert after_refresh == [populated._scroll_selected_into_view]

    no_loading = ThreadSelectorScreen(initial_threads=[])
    body = _AsyncContainer()
    monkeypatch.setattr(
        no_loading,
        "query_one",
        lambda selector, *_args: (
            body
            if selector == ".thread-selector-body"
            else (_ for _ in ()).throw(selector_mod.NoMatches("missing"))
        ),
    )
    asyncio.run(no_loading._build_table_pane())
    assert body.children


def screen_has_empty_thread_label(scroll: _FakeContextContainer) -> bool:
    return any(
        isinstance(child, _FakeStatic) and child.kwargs.get("classes") == "thread-empty"
        for child in scroll.children
    )


def test_thread_selector_fetch_url_and_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = ThreadSelectorScreen(
        current_thread="thread-1", initial_threads=[_thread("a")]
    )
    updated: list[object] = []
    clicked: list[object] = []
    title = SimpleNamespace(update=lambda value: updated.append(value))

    async def fake_to_thread(func, thread_id: str):
        assert func is selector_mod.build_langsmith_thread_url
        assert thread_id == "thread-1"
        return "https://example.test/thread-1"

    monkeypatch.setattr(
        selector_mod.theme,
        "get_theme_colors",
        lambda _screen: SimpleNamespace(primary="#ffffff"),
    )
    monkeypatch.setattr(selector_mod.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(screen, "query_one", lambda *_args: title)
    asyncio.run(screen._fetch_thread_url())
    assert updated

    monkeypatch.setattr(selector_mod, "open_style_link", clicked.append)
    event = SimpleNamespace()
    screen.on_click(event)
    assert clicked == [event]

    no_current = ThreadSelectorScreen(initial_threads=[_thread("a")])
    asyncio.run(no_current._fetch_thread_url())

    async def timeout_to_thread(*_args: object, **_kwargs: object) -> str:
        raise TimeoutError

    monkeypatch.setattr(selector_mod.asyncio, "to_thread", timeout_to_thread)
    asyncio.run(screen._fetch_thread_url())

    async def generic_to_thread(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("url failed")

    monkeypatch.setattr(selector_mod.asyncio, "to_thread", generic_to_thread)
    asyncio.run(screen._fetch_thread_url())

    async def url_to_thread(*_args: object, **_kwargs: object) -> str:
        return "https://example.test/missing-title"

    monkeypatch.setattr(selector_mod.asyncio, "to_thread", url_to_thread)
    monkeypatch.setattr(
        screen,
        "query_one",
        lambda *_args: (_ for _ in ()).throw(selector_mod.NoMatches("missing")),
    )
    asyncio.run(screen._fetch_thread_url())


def test_thread_selector_refresh_build_and_header_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = ThreadSelectorScreen(
        current_thread="b",
        initial_threads=[_thread("a"), _thread("b")],
    )
    screen._selected_index = 1
    screen._column_widths = {"thread_id": 10, "agent_name": 8}
    screen._columns = {
        "thread_id": True,
        "agent_name": True,
        "messages": False,
        "created_at": False,
        "updated_at": False,
        "git_branch": False,
        "cwd": False,
        "initial_prompt": False,
    }
    widgets, selected = screen._create_option_widgets()
    assert len(widgets) == 2
    assert selected is widgets[1]
    assert "thread-option-current" in selected.classes

    home_scroll = SimpleNamespace(
        home_calls=0,
        scroll_home=lambda *, animate: setattr(
            home_scroll, "home_calls", home_scroll.home_calls + 1
        ),
    )
    screen._option_widgets = widgets
    screen._selected_index = 0
    monkeypatch.setattr(screen, "query_one", lambda *_args: home_scroll)
    screen._scroll_selected_into_view()
    assert home_scroll.home_calls == 1

    scrolled: list[bool] = []
    screen._selected_index = 1
    widgets[1].scroll_visible = lambda *, animate: scrolled.append(animate)  # type: ignore[method-assign]
    screen._scroll_selected_into_view()
    assert scrolled == [False]

    screen._option_widgets = []
    screen._scroll_selected_into_view()
    screen._option_widgets = widgets
    screen._selected_index = 99
    screen._scroll_selected_into_view()
    monkeypatch.setattr(
        screen,
        "query_one",
        lambda *_args: (_ for _ in ()).throw(selector_mod.NoMatches("missing")),
    )
    screen._selected_index = 0
    screen._scroll_selected_into_view()

    cell = SimpleNamespace(updated=[], update=lambda value: cell.updated.append(value))
    option = SimpleNamespace(
        thread_id="a",
        query_one=lambda selector, _cls: (
            cell
            if selector == ".thread-cell-thread_id"
            else (_ for _ in ()).throw(selector_mod.NoMatches("missing"))
        ),
    )
    screen._filtered_threads = [_thread("a")]
    screen._option_widgets = [option]  # type: ignore[assignment]
    screen._refresh_cell_labels()
    assert cell.updated == ["a"]

    workers: list[object] = []
    help_widget = SimpleNamespace(
        updated=[], update=lambda value: help_widget.updated.append(value)
    )
    sort_checkbox = SimpleNamespace(label="", value=False)

    def query_for_help(selector: str, *_args: object) -> object:
        if selector == "#thread-help":
            return help_widget
        if selector == f"#{selector_mod._SORT_SWITCH_ID}":
            return sort_checkbox
        raise selector_mod.NoMatches("missing")

    monkeypatch.setattr(screen, "query_one", query_for_help)
    monkeypatch.setattr(
        screen, "run_worker", lambda worker, **_kwargs: workers.append(worker)
    )
    screen._sort_by_updated = True
    screen._update_help_widgets()
    assert workers == [screen._rebuild_header]
    assert help_widget.updated
    assert sort_checkbox.value is True

    header = _AsyncContainer()
    monkeypatch.setattr(
        ThreadSelectorScreen, "app", property(lambda _self: _BatchApp())
    )
    monkeypatch.setattr(screen, "query_one", lambda *_args: header)
    asyncio.run(screen._rebuild_header())
    assert header.removed_children is True
    assert header.children

    scroll = _AsyncContainer()
    monkeypatch.setattr(screen, "query_one", lambda *_args: scroll)
    calls: list[str] = []
    monkeypatch.setattr(screen, "_update_help_widgets", lambda: calls.append("help"))
    screen._filtered_threads = []
    asyncio.run(screen._build_list())
    assert scroll.removed_children is True
    assert calls == ["help"]
    assert screen._option_widgets == []

    screen._filtered_threads = [_thread("a")]
    screen._selected_index = 0
    after_refresh: list[object] = []
    monkeypatch.setattr(
        screen, "call_after_refresh", lambda callback: after_refresh.append(callback)
    )
    asyncio.run(screen._build_list(recompute_widths=False))
    assert len(scroll.children) == 1
    assert after_refresh == [screen._scroll_selected_into_view]


def test_thread_selector_delete_confirm_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = ThreadSelectorScreen(
        initial_threads=[_thread("a"), _thread("b"), _thread("c")]
    )
    screen._selected_index = 1
    built: list[str] = []
    focused = SimpleNamespace(focused=0)
    focused.focus = lambda: setattr(focused, "focused", focused.focused + 1)

    async def fake_build_list() -> None:
        built.append("build")

    monkeypatch.setattr(screen, "_build_list", fake_build_list)
    monkeypatch.setattr(screen, "query_one", lambda *_args: focused)

    async def fake_delete_thread(thread_id: str) -> None:
        assert thread_id == "b"

    monkeypatch.setattr("invincat_cli.sessions.delete_thread", fake_delete_thread)
    asyncio.run(screen._handle_delete_confirm("b"))
    assert [thread["thread_id"] for thread in screen._threads] == ["a", "c"]
    assert screen._filtered_threads[screen._selected_index]["thread_id"] == "c"
    assert built == ["build"]
    assert focused.focused == 1

    notified: list[tuple[str, str]] = []
    app = SimpleNamespace(
        notify=lambda message, **kwargs: notified.append((message, kwargs["severity"]))
    )
    monkeypatch.setattr(ThreadSelectorScreen, "app", property(lambda _self: app))

    async def broken_delete_thread(_thread_id: str) -> None:
        raise OSError("nope")

    monkeypatch.setattr("invincat_cli.sessions.delete_thread", broken_delete_thread)
    asyncio.run(screen._handle_delete_confirm("c"))
    assert notified[0][1] == "error"

    single = ThreadSelectorScreen(initial_threads=[_thread("only")])
    single._selected_index = 0
    monkeypatch.setattr(single, "_build_list", fake_build_list)
    monkeypatch.setattr(single, "query_one", lambda *_args: focused)

    async def fake_delete_any(_thread_id: str) -> None:
        return None

    monkeypatch.setattr("invincat_cli.sessions.delete_thread", fake_delete_any)
    asyncio.run(single._handle_delete_confirm("only"))
    assert single._selected_index == 0
    assert single._filtered_threads == []


def test_thread_selector_persist_sort_order_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = ThreadSelectorScreen(initial_threads=[_thread("a")])
    workers: list[object] = []
    notified: list[tuple[str, str]] = []
    app = SimpleNamespace(
        notify=lambda message, **kwargs: notified.append((message, kwargs["severity"]))
    )
    monkeypatch.setattr(ThreadSelectorScreen, "app", property(lambda _self: app))
    monkeypatch.setattr(
        screen,
        "run_worker",
        lambda worker, **_kwargs: workers.append(worker),
    )
    monkeypatch.setattr(
        "invincat_cli.model_config.save_thread_sort_order",
        lambda _order: False,
    )

    screen._persist_sort_order("created_at")
    asyncio.run(workers.pop())

    assert notified == [("thread.sort_save_failed", "warning")]


def test_thread_selector_option_clicked_and_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = ThreadSelectorScreen(initial_threads=[_thread("a"), _thread("b")])
    dismissed: list[str | None] = []
    monkeypatch.setattr(screen, "dismiss", dismissed.append)

    screen.on_thread_option_clicked(ThreadOption.Clicked("b", 1))
    screen.action_cancel()

    assert dismissed == ["b", None]

    screen._confirming_delete = True
    screen.on_thread_option_clicked(ThreadOption.Clicked("a", 0))
    assert dismissed == ["b", None]
