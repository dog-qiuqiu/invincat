from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest
from textual.content import Content

from invincat_cli.widgets import messages
from invincat_cli.widgets.messages import (
    AppMessage,
    AssistantMessage,
    DiffMessage,
    ErrorMessage,
    QueuedUserMessage,
    SkillMessage,
    SummarizationMessage,
    ToolCallMessage,
    UserMessage,
)


class _FakeStatic:
    def __init__(self) -> None:
        self.value: object | None = None
        self.display = True
        self.classes: list[str] = []

    def update(self, value: object) -> None:
        self.value = value

    def add_class(self, *classes: str) -> None:
        self.classes.extend(classes)

    def remove_class(self, *classes: str) -> None:
        self.classes = [cls for cls in self.classes if cls not in classes]


class _FakeTimer:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakeEvent:
    def __init__(self, *, key: str = "ctrl+o") -> None:
        self.key = key
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakeStream:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.stopped = False

    async def write(self, text: str) -> None:
        self.writes.append(text)

    async def stop(self) -> None:
        self.stopped = True


class _FakeMarkdown:
    def __init__(self) -> None:
        self.updates: list[str] = []

    async def update(self, content: str) -> None:
        self.updates.append(content)


def _patch_theme_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    colors = SimpleNamespace(
        primary="#ffffff",
        muted="#888888",
        warning="#ffff00",
        error="#ff0000",
        skill="#00ff00",
        mode_bash="#00ffff",
        mode_command="#ff00ff",
    )
    monkeypatch.setattr(messages.theme, "get_theme_colors", lambda *_args: colors)


def test_strip_success_exit_line_only_removes_success_trailer() -> None:
    assert (
        messages._strip_success_exit_line("done\n[Command succeeded with exit code 0]")
        == "done"
    )
    assert (
        messages._strip_success_exit_line("failed\n[Command failed with exit code 1]")
        == "failed\n[Command failed with exit code 1]"
    )


def test_mode_color_falls_back_for_unknown_mode(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _patch_theme_colors(monkeypatch)
    with caplog.at_level("WARNING"):
        color = messages._mode_color("missing")

    assert color == messages.theme.get_theme_colors().primary
    assert "Missing color" in caplog.text


def test_mode_color_known_and_default_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_theme_colors(monkeypatch)

    assert messages._mode_color(None) == "#ffffff"
    assert messages._mode_color("shell") == "#00ffff"
    assert messages._mode_color("command") == "#ff00ff"


def test_show_timestamp_toast_handles_missing_data_and_click_mixin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages._show_timestamp_toast(UserMessage("hello", id="msg-1"))

    missing_id = SimpleNamespace(id="", app=SimpleNamespace())
    messages._show_timestamp_toast(missing_id)  # type: ignore[arg-type]

    missing_data_app = SimpleNamespace(
        _message_store=SimpleNamespace(get_message=lambda _message_id: None),
        notify=lambda *_args, **_kwargs: pytest.fail("unexpected notify"),
    )
    messages._show_timestamp_toast(
        SimpleNamespace(id="missing", app=missing_data_app)  # type: ignore[arg-type]
    )

    notified: list[tuple[str, int]] = []
    data = SimpleNamespace(timestamp=0)
    store = SimpleNamespace(get_message=lambda _message_id: data)
    app = SimpleNamespace(
        _message_store=store,
        notify=lambda label, timeout: notified.append((label, timeout)),
    )
    widget = SimpleNamespace(id="msg-1", app=app)

    messages._show_timestamp_toast(widget)  # type: ignore[arg-type]

    assert notified
    assert notified[0][1] == 3

    clicks: list[object] = []
    monkeypatch.setattr(
        messages, "_show_timestamp_toast", lambda value: clicks.append(value)
    )
    user = UserMessage("hello")
    user.on_click(_FakeEvent())  # type: ignore[arg-type]
    assert clicks == [user]


def test_user_message_render_highlights_modes_mentions_and_skips_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme_colors(monkeypatch)
    rendered = UserMessage("/help @README.md user@example.com").render()

    plain = rendered.plain
    assert plain.startswith("/ ")
    assert "help" in plain
    assert "@README.md" in plain
    assert "user@example.com" in plain


def test_user_message_highlights_slash_command_without_mode_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme_colors(monkeypatch)
    monkeypatch.setattr(messages, "PREFIX_TO_MODE", {})

    rendered = UserMessage("/help").render()

    assert rendered.plain == "> /help"


def test_user_and_queued_message_ascii_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_theme_colors(monkeypatch)
    monkeypatch.setattr(messages, "is_ascii_mode", lambda: True)
    user = UserMessage("!pwd")
    queued = QueuedUserMessage("pending")

    user.on_mount()
    queued.on_mount()

    assert "-mode-shell" in user.classes
    assert "-ascii" in user.classes
    assert "-ascii" in queued.classes
    assert UserMessage("plain").render().plain.startswith("> ")


def test_queued_user_message_render_uses_mode_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme_colors(monkeypatch)
    assert QueuedUserMessage("!pwd").render().plain.startswith("$ ")
    assert QueuedUserMessage("plain").render().plain.startswith("> ")


def test_strip_frontmatter_handles_missing_and_closed_blocks() -> None:
    assert messages._strip_frontmatter("body") == "body"
    assert messages._strip_frontmatter("---\nname: demo\n---\n# Body") == "# Body"
    assert messages._strip_frontmatter("---\nunterminated") == "---\nunterminated"


def test_skill_message_prepare_and_toggle_body() -> None:
    long_body = "\n".join(f"line {idx}" for idx in range(8))
    widget = SkillMessage("demo", body=long_body)
    hint = _FakeStatic()
    md = _FakeStatic()
    widget._hint_widget = hint  # type: ignore[assignment]
    widget._md_widget = md  # type: ignore[assignment]

    widget._prepare_body(long_body)
    assert "more lines" in str(hint.value)
    assert widget._expanded is False

    widget.toggle_body()
    assert widget._expanded is True
    assert widget._md_rendered is True
    assert md.value is not None
    widget.watch__expanded(False)
    assert "expand" in str(hint.value)


def test_skill_message_short_body_renders_and_hides_hint() -> None:
    widget = SkillMessage("demo", body="short")
    hint = _FakeStatic()
    md = _FakeStatic()
    widget._hint_widget = hint  # type: ignore[assignment]
    widget._md_widget = md  # type: ignore[assignment]

    widget._prepare_body("short")
    widget.watch__expanded(True)

    assert widget._expanded is True
    assert hint.display is False


def test_skill_message_markdown_render_falls_back_to_plain_text(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    widget = SkillMessage("demo", body="body")
    md = _FakeStatic()
    widget._md_widget = md  # type: ignore[assignment]
    real_module = sys.modules.get("rich.markdown")

    class BadMarkdown:
        def __init__(self, _body: str) -> None:
            raise RuntimeError("bad markdown")

    monkeypatch.setitem(
        sys.modules,
        "rich.markdown",
        SimpleNamespace(Markdown=BadMarkdown),
    )

    with caplog.at_level("WARNING"):
        widget._ensure_md_rendered("body")

    assert md.value == "body"
    assert widget._md_rendered is True
    assert "falling back to plain text" in caplog.text
    if real_module is not None:
        monkeypatch.setitem(sys.modules, "rich.markdown", real_module)


def test_skill_message_mount_caches_widgets_and_applies_deferred_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme_colors(monkeypatch)
    monkeypatch.setattr(messages, "is_ascii_mode", lambda: True)
    widget = SkillMessage("demo", body="\n".join(f"line {idx}" for idx in range(6)))
    md = _FakeStatic()
    hint = _FakeStatic()
    widget._deferred_expanded = True

    def fake_query(selector: str, *_args: object) -> object:
        return md if selector == "#skill-md" else hint

    widget.query_one = fake_query  # type: ignore[method-assign]

    widget.on_mount()

    assert widget._md_widget is md
    assert widget._hint_widget is hint
    assert widget._expanded is True
    assert widget._deferred_expanded is False
    assert widget.styles.border_left[0] == "ascii"


def test_skill_message_compose_includes_optional_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme_colors(monkeypatch)
    widget = SkillMessage(
        "demo",
        description="does things",
        source="user",
        body="body",
        args="please run",
    )

    children = list(widget.compose())

    assert len(children) == 5
    assert "skill-header" in children[0].classes
    assert "skill-description" in children[1].classes
    assert "skill-args" in children[2].classes


def test_skill_message_toggle_without_body_and_click_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget = SkillMessage("demo", body="")
    widget.toggle_body()
    assert widget._expanded is False

    calls: list[object] = []
    monkeypatch.setattr(
        messages, "_show_timestamp_toast", lambda value: calls.append(value)
    )
    event = _FakeEvent()
    widget._on_toggle_click(event)  # type: ignore[arg-type]

    assert event.stopped is True
    assert calls == [widget]


def test_skill_message_toggle_click_expands_body() -> None:
    widget = SkillMessage("demo", body="body")
    event = _FakeEvent()

    widget._on_toggle_click(event)  # type: ignore[arg-type]

    assert event.stopped is True
    assert widget._expanded is True


async def _exercise_assistant_message(
    widget: AssistantMessage,
) -> tuple[_FakeStream, str]:
    stream = _FakeStream()
    widget._stream = stream  # type: ignore[assignment]
    await widget.append_content("")
    await widget.append_content(" chunk")
    await widget.write_initial_content()
    content_after_append = widget._content

    reasoning = _FakeStatic()
    widget._reasoning_widget = reasoning  # type: ignore[assignment]
    await widget.append_reasoning("")
    await widget.append_reasoning("why")
    assert reasoning.display is True
    assert reasoning.value == "why"

    markdown = _FakeMarkdown()
    widget._markdown = markdown  # type: ignore[assignment]
    await widget.set_content("final")
    assert markdown.updates == ["final"]
    return stream, content_after_append


def test_assistant_message_streaming_and_reasoning_helpers() -> None:
    widget = AssistantMessage("initial")
    assert len(list(widget.compose())) == 2

    stream, content_after_append = asyncio.run(_exercise_assistant_message(widget))

    assert content_after_append == "initial chunk"
    assert widget._content == "final"
    assert stream.writes == [" chunk", "initial chunk"]
    assert stream.stopped is True
    assert widget._stream is None


def test_assistant_message_mount_and_lazy_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from textual.widgets import Markdown

    widget = AssistantMessage()
    markdown = _FakeMarkdown()
    reasoning = _FakeStatic()
    stream = _FakeStream()

    def fake_query(selector: str, *_args: object) -> object:
        return markdown if selector == "#assistant-content" else reasoning

    widget.query_one = fake_query  # type: ignore[method-assign]
    monkeypatch.setattr(Markdown, "get_stream", lambda _markdown: stream)

    widget.on_mount()

    assert widget._get_markdown() is markdown
    assert widget._ensure_stream() is stream


def test_assistant_message_lazy_markdown_and_reasoning_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from textual.widgets import Markdown

    widget = AssistantMessage()
    markdown = _FakeMarkdown()
    reasoning = _FakeStatic()
    stream = _FakeStream()

    def fake_query(selector: str, *_args: object) -> object:
        return markdown if selector == "#assistant-content" else reasoning

    widget.query_one = fake_query  # type: ignore[method-assign]
    monkeypatch.setattr(Markdown, "get_stream", lambda _markdown: stream)

    assert widget._get_markdown() is markdown
    assert widget._ensure_stream() is stream
    asyncio.run(widget.append_reasoning("because"))
    assert reasoning.value == "because"


def test_diff_message_counts_lines_and_toggles() -> None:
    diff = "--- a/file\n+++ b/file\n@@\n old\n-new\n+new\n..."
    widget = DiffMessage(diff, file_path="file.py")

    assert widget._total_lines == 4
    assert widget._expanded is False
    widget.toggle_expand()
    assert widget._expanded is True


def test_diff_message_key_handler_stops_ctrl_o_for_long_diff() -> None:
    diff = "\n".join(["@@"] + [f"+line {idx}" for idx in range(8)])
    widget = DiffMessage(diff)
    event = _FakeEvent(key="ctrl+o")

    widget.on_key(event)

    assert widget._expanded is True
    assert event.stopped is True


def test_diff_message_compose_click_and_non_matching_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(DiffMessage, "refresh", lambda *args, **kwargs: None)
    diff = "\n".join(
        ["--- a", "+++ b", "@@", "+1", "-2", " ctx", "+3", "-4", "+5", "+6"]
    )
    widget = DiffMessage(diff, file_path="file.py")

    collapsed = list(widget.compose())
    assert any("diff-header" in child.classes for child in collapsed)
    assert any("diff-hint" in child.classes for child in collapsed)

    widget.on_key(_FakeEvent(key="x"))
    assert widget._expanded is False
    widget.on_click()
    assert widget._expanded is True
    expanded = list(widget.compose())
    assert any("diff-hint" in child.classes for child in expanded)


def test_tool_call_message_filters_args_and_state_transitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme_colors(monkeypatch)
    widget = ToolCallMessage("shell", {"command": "pwd"}, args_finalized=True)
    status = _FakeStatic()
    preview = _FakeStatic()
    full = _FakeStatic()
    hint = _FakeStatic()
    widget._status_widget = status  # type: ignore[assignment]
    widget._preview_widget = preview  # type: ignore[assignment]
    widget._full_widget = full  # type: ignore[assignment]
    widget._hint_widget = hint  # type: ignore[assignment]
    widget._animation_timer = _FakeTimer()  # type: ignore[assignment]
    monkeypatch.setattr(widget, "refresh", lambda *args, **kwargs: None)

    widget.set_running()
    assert widget._status == "running"
    assert status.display is True

    widget.set_success("done\n[Command succeeded with exit code 0]")
    assert widget._status == "success"
    assert widget._output == "done"
    assert preview.display is True
    assert full.display is False

    widget.set_error("boom")
    assert widget._status == "error"
    assert "$ pwd" in widget._output
    assert widget._expanded is True
    assert "error" in status.classes

    widget.set_rejected()
    assert widget._status == "rejected"
    assert "rejected" in status.classes

    widget.set_skipped()
    assert widget._status == "skipped"
    assert "Skipped" in status.value.plain

    widget.toggle_output()
    assert widget._expanded is False
    widget.on_click(_FakeEvent())  # type: ignore[arg-type]
    assert widget._expanded is True

    write_widget = ToolCallMessage(
        "write_file",
        {"file_path": "a.txt", "content": "hidden", "replace_all": True},
    )
    assert write_widget._filtered_args() == {
        "file_path": "a.txt",
        "replace_all": True,
    }
    custom = ToolCallMessage("custom", {})
    custom.update_args({"alpha": 1})
    assert custom._args == {"alpha": 1}
    assert custom._args_finalized is True


def test_tool_call_message_compose_variants_and_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme_colors(monkeypatch)
    long_desc = "x" * 140
    task = ToolCallMessage("task", {"description": long_desc})
    task_children = list(task.compose())
    assert any("tool-task-desc" in child.classes for child in task_children)

    custom = ToolCallMessage("custom", {"a": 1, "b": 2, "c": 3, "d": 4})
    custom_children = list(custom.compose())
    assert any("tool-args" in child.classes for child in custom_children)
    assert custom.has_output is False
    assert custom._filtered_args() == {"a": 1, "b": 2, "c": 3, "d": 4}
    assert custom._prefix_output(Content("value")).plain.startswith("⎿")

    calls: list[object] = []
    monkeypatch.setattr(
        messages, "_show_timestamp_toast", lambda value: calls.append(value)
    )
    event = _FakeEvent()
    custom.on_click(event)  # type: ignore[arg-type]
    assert event.stopped is True
    assert calls == [custom]


def test_tool_call_message_deferred_restore_and_animation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme_colors(monkeypatch)

    for status in ("success", "error", "rejected", "skipped", "running", "pending"):
        widget = ToolCallMessage("custom", {"alpha": 1})
        status_widget = _FakeStatic()
        widget._status_widget = status_widget  # type: ignore[assignment]
        widget._preview_widget = _FakeStatic()  # type: ignore[assignment]
        widget._full_widget = _FakeStatic()  # type: ignore[assignment]
        widget._hint_widget = _FakeStatic()  # type: ignore[assignment]
        widget._animation_timer = _FakeTimer()  # type: ignore[assignment]
        widget._deferred_status = status
        widget._deferred_output = "result"
        widget._deferred_expanded = status == "success"
        widget._format_output = lambda output, *, is_preview=False: (  # type: ignore[method-assign]
            messages.FormattedOutput(Content(f"formatted:{output}"), None)
        )

        widget._restore_deferred_state()

        assert widget._deferred_status is None
        assert widget._animation_timer is None
        if status != "pending":
            assert widget._status == status

    widget = ToolCallMessage("custom", {}, args_finalized=False)
    widget._status_widget = _FakeStatic()  # type: ignore[assignment]
    widget._spinner_frames = ["-"]
    widget._update_animation()
    assert "Generating" in widget._status_widget.value.plain
    widget._args_finalized = True
    widget._update_animation()
    assert "Pending" in widget._status_widget.value.plain
    widget._status = "running"
    widget._update_animation()
    assert "Running" in widget._status_widget.value.plain


def test_tool_call_message_mount_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_theme_colors(monkeypatch)
    monkeypatch.setattr(messages, "is_ascii_mode", lambda: True)

    for status, args_finalized in (
        ("pending", False),
        ("pending", True),
        ("running", True),
        ("success", True),
        ("error", True),
    ):
        widget = ToolCallMessage("custom", {"alpha": 1}, args_finalized=args_finalized)
        widget._status = status
        widget._output = "done" if status in {"success", "error"} else ""
        status_widget = _FakeStatic()
        preview = _FakeStatic()
        hint = _FakeStatic()
        full = _FakeStatic()
        timer = _FakeTimer()
        by_id = {
            "#status": status_widget,
            "#output-preview": preview,
            "#output-hint": hint,
            "#output-full": full,
        }
        widget.query_one = lambda selector, *_args: by_id[selector]  # type: ignore[method-assign]
        widget.set_interval = lambda *_args, **_kwargs: timer  # type: ignore[method-assign]
        monkeypatch.setattr(widget, "refresh", lambda *args, **kwargs: None)

        widget.on_mount()

        assert "-ascii" in widget.classes
        if status in {"pending", "running"}:
            assert widget._animation_timer is timer
            assert status_widget.display is True
        elif status == "success":
            assert preview.display is True
        else:
            assert "error" in status_widget.classes


def test_tool_call_message_output_display_preview_and_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme_colors(monkeypatch)
    widget = ToolCallMessage("custom", {"alpha": 1})
    preview = _FakeStatic()
    full = _FakeStatic()
    hint = _FakeStatic()
    widget._preview_widget = preview  # type: ignore[assignment]
    widget._full_widget = full  # type: ignore[assignment]
    widget._hint_widget = hint  # type: ignore[assignment]

    def fake_format(
        output: str, *, is_preview: bool = False
    ) -> messages.FormattedOutput:
        truncation = "truncated output" if is_preview else None
        prefix = "preview" if is_preview else "full"
        return messages.FormattedOutput(Content(f"{prefix}:{output}"), truncation)

    widget._format_output = fake_format  # type: ignore[method-assign]
    widget._prefix_output = lambda content: Content.assemble("out:", content)  # type: ignore[method-assign]
    widget._output = "\n".join(f"line {idx}" for idx in range(8))

    widget._update_output_display()
    assert preview.display is True
    assert full.display is False
    assert "preview:" in preview.value.plain
    assert "truncated output" in hint.value.plain

    widget._expanded = True
    widget._update_output_display()
    assert preview.display is False
    assert full.display is True
    assert "full:" in full.value.plain
    assert "collapse" in hint.value.plain

    widget._expanded = False
    widget._output = "short"
    widget._update_output_display()
    assert preview.display is True
    assert hint.display is False

    widget._output = "   "
    widget._update_output_display()
    assert preview.display is False


def test_tool_call_message_update_args_after_mount(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _patch_theme_colors(monkeypatch)
    monkeypatch.setattr(ToolCallMessage, "is_mounted", property(lambda _self: True))

    header = _FakeStatic()
    args_widget = _FakeStatic()
    widget = ToolCallMessage("custom", {}, args_finalized=False)
    widget._status_widget = _FakeStatic()  # type: ignore[assignment]
    widget._spinner_frames = ["-"]

    def fake_query(selector: str, *_args: object) -> object:
        if selector == ".tool-header":
            return header
        if selector == ".tool-args":
            return args_widget
        raise LookupError(selector)

    widget.query_one = fake_query  # type: ignore[method-assign]

    widget.update_args({"a": 1, "b": 2, "c": 3, "d": 4})

    assert widget._args_finalized is True
    assert "custom" in str(header.value)
    assert "..." in args_widget.value.plain
    assert args_widget.display is True

    empty_args = ToolCallMessage("custom", {}, args_finalized=False)
    empty_args.query_one = fake_query  # type: ignore[method-assign]
    empty_args.update_args({})
    assert args_widget.display is False

    task_desc = _FakeStatic()
    task = ToolCallMessage("task", {}, args_finalized=False)
    task.query_one = lambda selector, *_args: task_desc  # type: ignore[method-assign]
    task.update_args({"description": "x" * 140})
    assert task_desc.display is True
    assert str(task_desc.value.plain).endswith("...")

    task.update_args({"description": ""})
    assert task_desc.display is False

    failing = ToolCallMessage("task", {}, args_finalized=False)
    failing.query_one = lambda *_args, **_kwargs: (_ for _ in ()).throw(LookupError)  # type: ignore[method-assign]
    with caplog.at_level("DEBUG"):
        failing.update_args({"description": "still ok"})
    assert "could not update" in caplog.text


def test_tool_call_message_update_args_logs_tool_args_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _patch_theme_colors(monkeypatch)
    monkeypatch.setattr(ToolCallMessage, "is_mounted", property(lambda _self: True))
    widget = ToolCallMessage("custom", {}, args_finalized=False)
    widget._status_widget = _FakeStatic()  # type: ignore[assignment]

    def fake_query(selector: str, *_args: object) -> object:
        if selector == ".tool-header":
            return _FakeStatic()
        if selector == ".tool-args":
            raise LookupError("missing args")
        raise LookupError(selector)

    widget.query_one = fake_query  # type: ignore[method-assign]

    with caplog.at_level("DEBUG"):
        widget.update_args({"alpha": 1})

    assert "could not update .tool-args" in caplog.text


def test_tool_call_message_small_guard_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme_colors(monkeypatch)
    widget = ToolCallMessage("custom", {})
    monkeypatch.setattr(widget, "refresh", lambda *args, **kwargs: None)
    widget.set_interval = lambda *_args, **_kwargs: _FakeTimer()  # type: ignore[method-assign]
    widget._update_animation()
    widget.set_running()
    widget.set_running()
    widget.toggle_output()
    widget.set_error("plain error")

    assert widget._status == "error"
    assert widget._output == "plain error"

    preview = _FakeStatic()
    full = _FakeStatic()
    hint = _FakeStatic()
    widget._preview_widget = preview  # type: ignore[assignment]
    widget._full_widget = full  # type: ignore[assignment]
    widget._hint_widget = hint  # type: ignore[assignment]
    widget._output = "long output"
    widget._format_output = lambda _output, *, is_preview=False: (  # type: ignore[method-assign]
        messages.FormattedOutput(Content("preview"), None)
    )
    widget._prefix_output = lambda content: content  # type: ignore[method-assign]
    monkeypatch.setattr(widget, "_PREVIEW_CHARS", 3)
    widget._expanded = False
    widget._update_output_display()

    assert "expand" in hint.value.plain


def test_tool_call_message_restore_noop_and_deferred_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme_colors(monkeypatch)
    widget = ToolCallMessage("custom", {"alpha": 1})
    widget._restore_deferred_state()
    assert widget._status == "pending"

    widget._deferred_status = "success"
    widget._deferred_output = "done"
    widget._deferred_expanded = True
    status = _FakeStatic()
    preview = _FakeStatic()
    hint = _FakeStatic()
    full = _FakeStatic()
    widget.query_one = lambda selector, *_args: {  # type: ignore[method-assign]
        "#status": status,
        "#output-preview": preview,
        "#output-hint": hint,
        "#output-full": full,
    }[selector]

    widget.on_mount()

    assert widget._status == "success"
    assert full.display is True
    assert widget._deferred_status is None


def test_diff_and_error_ascii_mount_and_app_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_theme_colors(monkeypatch)
    monkeypatch.setattr(messages, "is_ascii_mode", lambda: True)
    diff = DiffMessage("+one")
    error = ErrorMessage("boom")

    diff.on_mount()
    error.on_mount()

    assert diff.styles.border[0][0] == "ascii"
    assert error.styles.border_left[0] == "ascii"

    calls: list[object] = []
    monkeypatch.setattr(messages, "open_style_link", lambda event: calls.append(event))
    monkeypatch.setattr(
        messages, "_show_timestamp_toast", lambda value: calls.append(value)
    )
    app_message = AppMessage("link")
    event = _FakeEvent()
    app_message.on_click(event)  # type: ignore[arg-type]

    assert calls == [event, app_message]


def test_error_app_and_summarization_render(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_theme_colors(monkeypatch)
    assert "boom" in ErrorMessage("boom").render().plain

    content = Content("linked")
    app = AppMessage(content)
    assert app._content is content

    default_summary = SummarizationMessage()
    assert "Conversation offloaded" in default_summary.render().plain

    custom_content = Content("custom")
    assert SummarizationMessage(custom_content).render() is custom_content
    assert SummarizationMessage("done").render().plain == "done"
