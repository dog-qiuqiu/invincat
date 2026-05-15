from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from invincat_cli.widgets import autocomplete as autocomplete_mod
from invincat_cli.widgets.autocomplete import (
    CompletionResult,
    FuzzyFileController,
    MultiCompletionManager,
    ShellCompletionController,
    SlashCommandController,
    _escape_path,
    _fuzzy_score,
    _fuzzy_search,
    _get_longest_common_prefix,
    _get_project_files,
    _get_system_commands,
    _is_dotpath,
    _parse_shell_tokens,
    _path_depth,
    _unescape_token,
)


class _FakeView:
    def __init__(self) -> None:
        self.rendered: list[tuple[list[tuple[str, str]], int]] = []
        self.cleared = 0
        self.replacements: list[tuple[int, int, str]] = []

    def render_completion_suggestions(
        self, suggestions: list[tuple[str, str]], selected_index: int
    ) -> None:
        self.rendered.append((list(suggestions), selected_index))

    def clear_completion_suggestions(self) -> None:
        self.cleared += 1

    def replace_completion_range(self, start: int, end: int, replacement: str) -> None:
        self.replacements.append((start, end, replacement))


class _FakeController:
    def __init__(self, *, handles: bool) -> None:
        self.handles = handles
        self.changed: list[tuple[str, int]] = []
        self.keys: list[str] = []
        self.resets = 0

    def can_handle(self, _text: str, _cursor_index: int) -> bool:
        return self.handles

    def on_text_changed(self, text: str, cursor_index: int) -> None:
        self.changed.append((text, cursor_index))

    def on_key(
        self, event: SimpleNamespace, _text: str, _cursor_index: int
    ) -> CompletionResult:
        self.keys.append(event.key)
        return CompletionResult.HANDLED

    def reset(self) -> None:
        self.resets += 1


def _event(key: str) -> SimpleNamespace:
    return SimpleNamespace(key=key)


def test_slash_command_scoring_text_change_and_key_selection() -> None:
    view = _FakeView()
    controller = SlashCommandController(
        [
            ("/help", "Show help", "docs"),
            ("/history", "Search previous sessions", "threads"),
            ("/model", "Switch model", "provider"),
        ],
        view,
    )

    assert SlashCommandController.can_handle("/he", 3)
    assert not SlashCommandController.can_handle(" /he", 4)
    assert controller._score_command("he", "/help", "Show help") == 200.0
    assert controller._score_command("isto", "/history", "Search previous sessions") > 0
    assert (
        controller._score_command("prov", "/model", "Switch model", "provider") == 120.0
    )
    assert controller._score_command("z", "/help", "Show help") == 0.0

    controller.on_text_changed("/", 1)
    assert [item[0] for item in view.rendered[-1][0]] == ["/help", "/history", "/model"]

    controller.on_text_changed("/mod", 4)
    assert view.rendered[-1][0][0] == ("/model", "Switch model")

    assert controller.on_key(_event("down"), "/mod", 4) == CompletionResult.HANDLED
    assert view.rendered[-1][1] == 0
    assert controller.on_key(_event("tab"), "/mod", 4) == CompletionResult.HANDLED
    assert view.replacements[-1] == (0, 4, "/model")
    assert view.cleared == 1

    controller.update_commands([("/clear", "Clear chat", "")])
    assert view.cleared == 1
    controller.on_text_changed("/missing", 8)
    assert controller._suggestions == []

    assert controller.on_key(_event("tab"), "/missing", 8) == CompletionResult.IGNORED
    assert controller._apply_selected_completion(8) is False


def test_slash_command_invalid_cursor_reset_and_key_edges() -> None:
    view = _FakeView()
    controller = SlashCommandController([("/help", "Show help", "")], view)

    assert controller._score_command("", "/help", "Show help") == 0.0
    assert controller._score_command("show", "/help", "Show help") == 110.0
    assert controller._score_command("previous", "/history", "Searchprevious") == 90.0
    assert controller._score_command("elp", "/help", "Show help") == 150.0

    controller.on_text_changed("/help", -1)
    controller.on_text_changed("plain", 5)
    assert controller._suggestions == []
    controller._suggestions = [("/help", "Show help")]
    controller._apply_selected_completion = lambda _cursor: False  # type: ignore[method-assign]
    assert controller.on_key(_event("tab"), "/help", 5) == CompletionResult.IGNORED
    assert controller.on_key(_event("enter"), "/help", 5) == CompletionResult.HANDLED
    assert controller.on_key(_event("unknown"), "/help", 5) == CompletionResult.IGNORED
    controller._suggestions = []
    assert controller._apply_selected_completion(5) is False

    controller = SlashCommandController([("/help", "Show help", "")], view)
    controller.on_text_changed("/", 1)
    assert controller.on_key(_event("up"), "/", 1) == CompletionResult.HANDLED
    assert controller.on_key(_event("enter"), "/", 1) == CompletionResult.SUBMIT
    assert view.replacements[-1] == (0, 1, "/help")

    controller.on_text_changed("/", 1)
    assert controller.on_key(_event("escape"), "/", 1) == CompletionResult.HANDLED
    controller._move_selection(1)


def test_fuzzy_file_helpers_and_project_file_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert _is_dotpath(".github/workflows/ci.yml")
    assert not _is_dotpath("src/app.py")
    assert _path_depth("src/app.py") == 1
    assert _fuzzy_score("app", "src/app.py") > _fuzzy_score("app", "src/snippet.py")
    assert _fuzzy_search(
        "",
        ["src/app.py", "README.md", ".env", "docs/guide.md"],
        include_dotfiles=False,
    ) == ["README.md", "docs/guide.md", "src/app.py"]
    assert _fuzzy_search("read", ["README.md", "docs/readme_notes.md"]) == [
        "README.md",
        "docs/readme_notes.md",
    ]

    (tmp_path / "README.md").write_text("readme", encoding="utf-8")
    (tmp_path / ".env").write_text("secret", encoding="utf-8")
    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "app.py").write_text("print('ok')", encoding="utf-8")
    monkeypatch.setattr(autocomplete_mod, "_get_git_executable", lambda: None)

    assert sorted(_get_project_files(tmp_path)) == ["README.md", "src/app.py"]


def test_project_files_git_and_fuzzy_score_edges(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(autocomplete_mod.shutil, "which", lambda name: f"/bin/{name}")
    assert autocomplete_mod._get_git_executable() == "/bin/git"

    monkeypatch.setattr(autocomplete_mod, "_get_git_executable", lambda: "/bin/git")

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="a.py\nsrc/b.py\n\n")

    monkeypatch.setattr(autocomplete_mod.subprocess, "run", fake_run)
    assert _get_project_files(tmp_path) == ["a.py", "src/b.py"]

    def timeout_run(*_args, **_kwargs):
        raise autocomplete_mod.subprocess.TimeoutExpired("git", 5)

    monkeypatch.setattr(autocomplete_mod.subprocess, "run", timeout_run)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "name_match.py").write_text("x", encoding="utf-8")
    assert "src/name_match.py" in _get_project_files(tmp_path)

    assert _fuzzy_score("match", "src/name_match.py") > _fuzzy_score(
        "match", "src/domain.py"
    )
    assert _fuzzy_score("src", "src/name.py") > 0
    assert _fuzzy_score("nom", "very/nome.py") >= 15
    assert _fuzzy_score("py", "src/app.py") >= 100
    assert _fuzzy_score("rc", "src/app.py") >= 40
    assert _fuzzy_score("apx", "src/app.py") > 0


def test_project_files_fallback_caps_and_ignores_glob_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(autocomplete_mod, "_get_git_executable", lambda: None)
    monkeypatch.setattr(autocomplete_mod, "_MAX_FALLBACK_FILES", 1)
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    (tmp_path / "b.py").write_text("b", encoding="utf-8")

    assert len(_get_project_files(tmp_path)) == 1

    class BrokenRoot:
        def glob(self, _pattern: str):
            raise OSError("glob failed")

    assert _get_project_files(BrokenRoot()) == []  # type: ignore[arg-type]


def test_fuzzy_file_controller_applies_mentions_and_warms_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    view = _FakeView()
    controller = FuzzyFileController(view, cwd=tmp_path)
    controller._file_cache = ["README.md", "src/app.py", ".env"]

    assert FuzzyFileController.can_handle("@", 1)
    assert not FuzzyFileController.can_handle("@", 2)
    assert not FuzzyFileController.can_handle("see @foo bar", 9)
    assert FuzzyFileController.can_handle("see @REA", 8)

    controller.on_text_changed("see @REA", 8)
    assert view.rendered[-1][0][0] == ("@README.md", "md")
    assert controller.on_key(_event("enter"), "see @REA", 8) == CompletionResult.HANDLED
    assert view.replacements[-1] == (4, 8, "@README.md")
    assert view.cleared == 1

    controller.refresh_cache()
    assert controller._file_cache is None
    monkeypatch.setattr(
        autocomplete_mod, "_get_project_files", lambda _root: ["fresh.py"]
    )
    asyncio.run(controller.warm_cache())
    assert controller._file_cache == ["fresh.py"]


def test_fuzzy_file_controller_edges(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    view = _FakeView()
    controller = FuzzyFileController(view, cwd=tmp_path)
    controller._file_cache = ["README.md"]

    assert not FuzzyFileController.can_handle("plain", 5)
    assert not FuzzyFileController.can_handle("@", 0)
    assert not FuzzyFileController.can_handle("@", 2)

    controller.on_text_changed("plain", 5)
    assert controller._suggestions == []
    controller.on_text_changed("@missing", 8)
    assert controller._suggestions == []
    assert controller._get_files() == ["README.md"]

    controller._file_cache = ["plainfile", ".env", "src/app.py"]
    assert controller._get_fuzzy_suggestions("") == [
        ("@plainfile", "file"),
        ("@src/app.py", "py"),
    ]
    dot_suggestions = controller._get_fuzzy_suggestions(".")
    assert dot_suggestions[0] == ("@.env", "file")
    assert ("@src/app.py", "py") in dot_suggestions

    assert controller.on_key(_event("tab"), "@README", 7) == CompletionResult.IGNORED
    assert (
        controller.on_key(_event("unknown"), "@README", 7) == CompletionResult.IGNORED
    )
    controller._suggestions = [("@README.md", "md")]
    assert controller._apply_selected_completion("plain", 5) is False
    controller._suggestions = []
    assert controller._apply_selected_completion("@README", 7) is False
    controller._move_selection(1)
    controller._suggestions = [("@README.md", "md")]
    assert controller.on_key(_event("tab"), "README", 6) == CompletionResult.IGNORED
    assert (
        controller.on_key(_event("unknown"), "@README", 7) == CompletionResult.IGNORED
    )
    assert controller.on_key(_event("down"), "@README", 7) == CompletionResult.HANDLED
    assert controller.on_key(_event("up"), "@README", 7) == CompletionResult.HANDLED
    assert controller.on_key(_event("escape"), "@README", 7) == CompletionResult.HANDLED
    assert view.cleared == 1

    controller._suggestions = [("@README.md", "md")]
    assert controller._apply_selected_completion("README", 6) is False

    controller._file_cache = None
    monkeypatch.setattr(
        autocomplete_mod, "_get_project_files", lambda _root: ["lazy.py"]
    )
    assert controller._get_files() == ["lazy.py"]
    controller._file_cache = None

    async def broken_to_thread(*_args, **_kwargs):
        raise RuntimeError("ignored")

    monkeypatch.setattr(autocomplete_mod.asyncio, "to_thread", broken_to_thread)
    asyncio.run(controller.warm_cache())
    assert controller._file_cache is None

    async def fresh_to_thread(*_args, **_kwargs):
        return ["fresh.py"]

    monkeypatch.setattr(autocomplete_mod.asyncio, "to_thread", fresh_to_thread)
    asyncio.run(controller.warm_cache())
    assert controller._file_cache == ["fresh.py"]

    asyncio.run(controller.warm_cache())
    assert controller._file_cache == ["fresh.py"]


def test_shell_helpers_system_commands_and_path_suggestions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cmd = bin_dir / "mycmd"
    cmd.write_text("#!/bin/sh\n", encoding="utf-8")
    cmd.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    assert _get_system_commands() == ["mycmd"]
    assert _escape_path("two words.txt") == "'two words.txt'"
    assert _escape_path("it's.txt") == "'it'\"'\"'s.txt'"
    assert _unescape_token("'it'\"'\"'s.txt'") == "it's.txt"
    assert _unescape_token('"a\\$b"') == "a$b"
    assert _parse_shell_tokens("git commit -m 'hello world'") == [
        "git",
        "commit",
        "-m",
        "'hello world'",
    ]
    assert _parse_shell_tokens('echo "hello world"') == ["echo", '"hello world"']
    assert _parse_shell_tokens("'literal\\path'") == [r"'literal\path'"]
    assert _get_longest_common_prefix(["alpha", "alpine", "also"]) == "al"
    assert _get_longest_common_prefix(["same", "same"]) == "same"
    assert _get_longest_common_prefix([]) == ""
    assert _get_longest_common_prefix(["single"]) == "single"
    assert _escape_path("") == ""
    assert _unescape_token("") == ""
    assert _unescape_token(r"a\ b\$c") == "a b$c"
    assert _parse_shell_tokens(r"echo a\ b \"quoted\" 'single\raw'") == [
        "echo",
        r"a\ b",
        r"\"quoted\"",
        r"'single\raw'",
    ]

    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "file one.txt").write_text("x", encoding="utf-8")
    (cwd / "folder").mkdir()
    (cwd / ".hidden").write_text("x", encoding="utf-8")
    controller = ShellCompletionController(_FakeView(), cwd=cwd)

    assert controller._get_command_suggestions("my") == [("mycmd", "command")]
    assert controller._get_path_suggestions("f") == [
        ("file one.txt", "file"),
        ("folder/", "dir"),
    ]
    assert controller._get_path_suggestions(".h") == [(".hidden", "file")]
    assert controller._get_path_suggestions("missing/") == []

    home_file = tmp_path / "home-file.txt"
    home_file.write_text("x", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert ("home-file.txt", "file") in controller._get_path_suggestions("~/home-")
    assert controller._get_path_suggestions("~")

    monkeypatch.setattr(Path, "is_dir", lambda _self: True)
    monkeypatch.setattr(
        Path,
        "iterdir",
        lambda _self: (_ for _ in ()).throw(OSError("nope")),
    )
    assert controller._get_path_suggestions("anything") == []


def test_system_commands_skip_empty_missing_and_unreadable_path_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()
    monkeypatch.setenv(
        "PATH",
        f"{autocomplete_mod.os.pathsep}{missing}{autocomplete_mod.os.pathsep}{unreadable}",
    )
    monkeypatch.setattr(
        Path,
        "iterdir",
        lambda self: (
            (_ for _ in ()).throw(OSError("unreadable"))
            if self == unreadable
            else iter(())
        ),
    )

    assert _get_system_commands() == []


def test_shell_controller_tab_cycle_enter_and_reset(tmp_path: Path) -> None:
    view = _FakeView()
    controller = ShellCompletionController(view, cwd=tmp_path)
    controller._command_cache = ["git", "grep"]

    controller.on_text_changed("!g", 2)
    assert controller._suggestions == [("git", "command"), ("grep", "command")]
    assert controller._completion_start == 1

    assert controller.on_key(_event("tab"), "!g", 2) == CompletionResult.HANDLED
    assert view.replacements[-1] == (1, 2, "git ")
    assert controller.on_key(_event("tab"), "!g", 2) == CompletionResult.HANDLED
    assert view.replacements[-1] == (1, 5, "grep ")

    controller.on_text_changed("!g", 2)
    assert controller.on_key(_event("down"), "!g", 2) == CompletionResult.HANDLED
    assert controller._selected_index == 1
    assert controller.on_key(_event("up"), "!g", 2) == CompletionResult.HANDLED
    assert controller._selected_index == 0
    assert controller.on_key(_event("enter"), "!g", 2) == CompletionResult.SUBMIT
    assert controller._suggestions == []
    assert controller.on_key(_event("unknown"), "!g", 2) == CompletionResult.IGNORED
    assert controller.on_key(_event("escape"), "!g", 2) == CompletionResult.HANDLED


def test_shell_controller_space_path_one_suggestion_and_edge_keys(
    tmp_path: Path,
) -> None:
    view = _FakeView()
    (tmp_path / "dir").mkdir()
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    controller = ShellCompletionController(view, cwd=tmp_path)

    assert ShellCompletionController.can_handle("", 0) is True
    assert controller._strip_prefix("!ls") == ("ls", 1)
    assert controller._strip_prefix("ls") == ("ls", 0)
    assert ShellCompletionController.can_handle("anything", 999) is True
    assert controller._get_command_suggestions("")[0] == ("ls", "command")

    controller.on_text_changed("!ls ", 4)
    assert controller._completion_start == 4
    assert controller._suggestions

    controller._suggestions = [("dir/", "dir")]
    controller._completion_start = 4
    assert controller.on_key(_event("tab"), "!ls ", 4) == CompletionResult.HANDLED
    assert view.replacements[-1] == (4, 4, "dir/")
    assert controller._suggestions == []

    controller._suggestions = [("file.txt", "file")]
    controller._completion_start = 4
    assert controller.on_key(_event("enter"), "!ls ", 4) == CompletionResult.SUBMIT
    assert view.replacements[-1] == (4, 4, "file.txt ")

    assert controller.on_key(_event("enter"), "!ls", 3) == CompletionResult.IGNORED
    assert controller.on_key(_event("down"), "!ls", 3) == CompletionResult.IGNORED
    assert controller.on_key(_event("up"), "!ls", 3) == CompletionResult.IGNORED
    assert controller._apply_completion_for_token() is False

    controller.on_text_changed("!   ", 4)
    assert controller._suggestions == []
    controller._suggestions = [("git", "command")]
    assert controller.on_key(_event("enter"), "!", 1) == CompletionResult.SUBMIT
    controller._suggestions = [("git", "command")]
    assert controller._handle_tab("!", 1) == CompletionResult.HANDLED

    controller._get_path_suggestions = lambda _prefix: []  # type: ignore[method-assign]
    controller.on_text_changed("!ls ", 4)
    assert controller._suggestions == []
    controller.on_text_changed("!ls 'unterminated", len("!ls 'unterminated"))
    assert controller._suggestions == []

    controller._is_cycling = True
    controller._original_token = "old"
    controller._current_completion_end = 99
    controller._command_cache = ["git"]
    controller.on_text_changed("!g", 2)
    assert controller._original_token == ""
    assert controller._current_completion_end == 0


def test_shell_controller_cache_warm_and_completion_edges(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    view = _FakeView()
    controller = ShellCompletionController(view, cwd=tmp_path)
    monkeypatch.setattr(autocomplete_mod, "_get_system_commands", lambda: ["cmd"])
    assert controller._get_commands() == ["cmd"]
    controller.refresh_cache()
    assert controller._command_cache is None

    async def broken_to_thread(*_args, **_kwargs):
        raise RuntimeError("ignored")

    monkeypatch.setattr(autocomplete_mod.asyncio, "to_thread", broken_to_thread)
    asyncio.run(controller.warm_cache())
    assert controller._command_cache is None

    async def fresh_to_thread(*_args, **_kwargs):
        return ["fresh"]

    monkeypatch.setattr(autocomplete_mod.asyncio, "to_thread", fresh_to_thread)
    asyncio.run(controller.warm_cache())
    assert controller._command_cache == ["fresh"]
    asyncio.run(controller.warm_cache())
    assert controller._command_cache == ["fresh"]

    controller._suggestions = [("one", "file"), ("two", "file")]
    controller._completion_start = 0
    assert controller._handle_tab("   ", 3) == CompletionResult.HANDLED
    assert view.replacements[-1] == (0, 3, "one ")

    controller._suggestions = [("dir/", "dir")]
    controller._completion_start = 4
    assert controller._handle_tab("!ls ", 4) == CompletionResult.HANDLED
    assert view.replacements[-1] == (4, 4, "dir/")

    controller._suggestions = []
    controller._get_command_suggestions = lambda _prefix: []  # type: ignore[method-assign]
    assert controller._handle_tab("!missing", 8) == CompletionResult.IGNORED
    assert controller._apply_completion_for_token() is False
    controller._move_selection(1)


def test_shell_controller_path_suggestion_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "dir").mkdir()
    (cwd / "a.txt").write_text("x", encoding="utf-8")
    (cwd / ".env").write_text("x", encoding="utf-8")
    (cwd / "b.txt").write_text("x", encoding="utf-8")
    controller = ShellCompletionController(_FakeView(), cwd=cwd)

    assert controller._get_path_suggestions(str(cwd / "dir") + "/") == []
    assert controller._get_path_suggestions(str(cwd / "missing") + "/") == []
    assert (".env", "file") not in controller._get_path_suggestions(str(cwd) + "/")

    monkeypatch.setattr(autocomplete_mod, "MAX_SUGGESTIONS", 1)
    assert len(controller._get_path_suggestions(str(cwd) + "/")) == 1

    controller.on_text_changed("!ls d", 5)
    assert controller._suggestions == [("dir/", "dir")]
    assert controller._completion_start == 4


def test_multi_completion_manager_switches_active_controller() -> None:
    first = _FakeController(handles=True)
    second = _FakeController(handles=True)
    manager = MultiCompletionManager([first, second])

    manager.on_text_changed("/help", 5)
    assert first.changed == [("/help", 5)]
    assert manager.on_key(_event("tab"), "/help", 5) == CompletionResult.HANDLED
    assert first.keys == ["tab"]

    first.handles = False
    manager.on_text_changed("@README", 7)
    assert first.resets == 1
    assert second.changed == [("@README", 7)]

    second.handles = False
    manager.on_text_changed("plain", 5)
    assert second.resets == 1
    assert manager.on_key(_event("tab"), "plain", 5) == CompletionResult.IGNORED

    manager.on_text_changed("@again", 6)
    second.handles = True
    manager.on_text_changed("@again", 6)
    manager.reset()
    assert manager._active is None
