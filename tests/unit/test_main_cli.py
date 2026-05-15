from __future__ import annotations

import asyncio
import io
import locale
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

import invincat_cli.main as main


class _FakeConsole:
    def __init__(self) -> None:
        self.messages: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def print(self, *args: Any, **kwargs: Any) -> None:
        self.messages.append((args, kwargs))


class _FakeStdin(io.StringIO):
    def __init__(self, text: str, *, is_tty: bool) -> None:
        super().__init__(text)
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def test_check_cli_dependencies_exits_when_required_packages_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main.importlib.util, "find_spec", lambda _name: None)

    with pytest.raises(SystemExit) as exc_info:
        main.check_cli_dependencies()

    assert exc_info.value.code == 1


def test_check_cli_dependencies_accepts_present_packages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main.importlib.util, "find_spec", lambda _name: object())

    main.check_cli_dependencies()


def test_ripgrep_install_hint_prefers_platform_package_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main.sys, "platform", "darwin")
    monkeypatch.setattr(
        main.shutil,
        "which",
        lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None,
    )

    assert main._ripgrep_install_hint() == "brew install ripgrep"


def test_ripgrep_install_hint_falls_back_to_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main.sys, "platform", "plan9")
    monkeypatch.setattr(main.shutil, "which", lambda _name: None)

    assert main._ripgrep_install_hint() == main._RIPGREP_URL


@pytest.mark.parametrize(
    ("platform", "available", "expected"),
    [
        ("darwin", {"port"}, "sudo port install ripgrep"),
        ("linux", {"apt-get"}, "sudo apt-get install ripgrep"),
        ("linux", {"dnf"}, "sudo dnf install ripgrep"),
        ("linux", {"pacman"}, "sudo pacman -S ripgrep"),
        ("linux", {"zypper"}, "sudo zypper install ripgrep"),
        ("linux", {"apk"}, "sudo apk add ripgrep"),
        ("linux", {"nix-env"}, "nix-env -iA nixpkgs.ripgrep"),
        ("win32", {"choco"}, "choco install ripgrep"),
        ("win32", {"scoop"}, "scoop install ripgrep"),
        ("win32", {"winget"}, "winget install BurntSushi.ripgrep"),
        ("freebsd", {"cargo"}, "cargo install ripgrep"),
        ("freebsd", {"conda"}, "conda install -c conda-forge ripgrep"),
    ],
)
def test_ripgrep_install_hint_covers_platform_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    available: set[str],
    expected: str,
) -> None:
    monkeypatch.setattr(main.sys, "platform", platform)
    monkeypatch.setattr(
        main.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in available else None,
    )

    assert main._ripgrep_install_hint() == expected


def test_check_optional_tools_respects_warning_suppression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import invincat_cli.model_config as model_config

    monkeypatch.setattr(main.shutil, "which", lambda _name: None)
    monkeypatch.setattr(model_config, "is_warning_suppressed", lambda *_args: False)
    assert main.check_optional_tools(config_path=tmp_path / "config.toml") == [
        "ripgrep"
    ]

    monkeypatch.setattr(model_config, "is_warning_suppressed", lambda *_args: True)
    assert main.check_optional_tools(config_path=tmp_path / "config.toml") == []


def test_format_tool_warning_cli_links_url_hints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main, "_ripgrep_install_hint", lambda: "https://example.test/rg"
    )

    warning = main.format_tool_warning_cli("ripgrep")

    assert "[link=https://example.test/rg]" in warning
    assert main.format_tool_warning_cli("git") == "git is not installed."
    assert main.format_tool_warning_tui("git") == "git is not installed."


def test_format_tool_warning_tui_includes_ripgrep_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "_ripgrep_install_hint", lambda: "brew install ripgrep")

    warning = main.format_tool_warning_tui("ripgrep")

    assert "ripgrep is not installed" in warning
    assert "Install: brew install ripgrep" in warning
    assert main._RIPGREP_SUPPRESS_HINT in warning


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["deepagents"], {"command": None, "agent": "agent"}),
        (
            ["deepagents", "threads", "list", "--agent", "coder", "-n", "5"],
            {
                "command": "threads",
                "threads_command": "list",
                "agent": "coder",
                "limit": 5,
            },
        ),
        (
            ["deepagents", "skills", "delete", "demo", "--force", "--json"],
            {
                "command": "skills",
                "skills_command": "delete",
                "name": "demo",
                "force": True,
                "output_format": "json",
            },
        ),
        (["deepagents", "wecombot"], {"command": "wecombot"}),
    ],
)
def test_parse_args_builds_cli_subcommands(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    expected: dict[str, Any],
) -> None:
    monkeypatch.setattr(main.sys, "argv", argv)

    args = main.parse_args()

    for key, value in expected.items():
        assert getattr(args, key) == value


def test_parse_args_lazy_help_action_invokes_ui_help(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.ui as ui_module

    events: list[str] = []
    monkeypatch.setattr(ui_module, "show_help", lambda: events.append("help"))
    monkeypatch.setattr(main.sys, "argv", ["deepagents", "-h"])

    with pytest.raises(SystemExit) as exc_info:
        main.parse_args()

    assert exc_info.value.code == 0
    assert events == ["help"]


@pytest.mark.parametrize("error_name", ["package-not-found", "runtime-error"])
def test_parse_args_uses_unknown_sdk_version_when_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
    error_name: str,
) -> None:
    import importlib.metadata as metadata

    error: Exception
    if error_name == "package-not-found":
        error = metadata.PackageNotFoundError("deepagents")
    else:
        error = RuntimeError("metadata unavailable")

    monkeypatch.setattr(main.sys, "argv", ["deepagents"])
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda _name: (_ for _ in ()).throw(error),
    )

    args = main.parse_args()

    assert args.agent == "agent"


def _args_for_stdin(**overrides: Any) -> SimpleNamespace:
    args = {
        "stdin": False,
        "non_interactive_message": None,
        "initial_prompt": None,
    }
    args.update(overrides)
    return SimpleNamespace(**args)


def test_apply_stdin_pipe_sets_non_interactive_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args_for_stdin()
    monkeypatch.setattr(main.sys, "stdin", _FakeStdin("  piped text  ", is_tty=False))
    monkeypatch.setattr(
        main.os,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no tty")),
    )

    main.apply_stdin_pipe(args)

    assert args.non_interactive_message == "piped text"


def test_apply_stdin_pipe_prepends_existing_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args_for_stdin(non_interactive_message="task")
    monkeypatch.setattr(main.sys, "stdin", _FakeStdin("context", is_tty=False))
    monkeypatch.setattr(
        main.os,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no tty")),
    )

    main.apply_stdin_pipe(args)

    assert args.non_interactive_message == "context\n\ntask"

    args = _args_for_stdin(initial_prompt="explain")
    monkeypatch.setattr(main.sys, "stdin", _FakeStdin("log", is_tty=False))

    main.apply_stdin_pipe(args)

    assert args.initial_prompt == "log\n\nexplain"


def test_apply_stdin_pipe_errors_when_explicit_stdin_is_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args_for_stdin(stdin=True)
    monkeypatch.setattr(main.sys, "stdin", _FakeStdin("", is_tty=True))

    with pytest.raises(SystemExit) as exc_info:
        main.apply_stdin_pipe(args)

    assert exc_info.value.code == 1


def test_apply_stdin_pipe_ignores_implicit_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args_for_stdin(stdin=False)
    monkeypatch.setattr(main.sys, "stdin", _FakeStdin("", is_tty=True))

    main.apply_stdin_pipe(args)

    assert args.non_interactive_message is None


def test_apply_stdin_pipe_errors_when_explicit_stdin_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args_for_stdin(stdin=True)
    monkeypatch.setattr(main.sys, "stdin", None)

    with pytest.raises(SystemExit) as exc_info:
        main.apply_stdin_pipe(args)

    assert exc_info.value.code == 1


def test_apply_stdin_pipe_ignores_missing_or_unknown_implicit_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args_for_stdin(stdin=False)
    monkeypatch.setattr(main.sys, "stdin", None)

    main.apply_stdin_pipe(args)
    assert args.non_interactive_message is None

    class BadStdin:
        def isatty(self) -> bool:
            raise OSError("bad fd")

    monkeypatch.setattr(main.sys, "stdin", BadStdin())
    main.apply_stdin_pipe(args)
    assert args.non_interactive_message is None


def test_apply_stdin_pipe_errors_when_explicit_tty_state_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BadStdin:
        def isatty(self) -> bool:
            raise OSError("bad fd")

    args = _args_for_stdin(stdin=True)
    monkeypatch.setattr(main.sys, "stdin", BadStdin())

    with pytest.raises(SystemExit) as exc_info:
        main.apply_stdin_pipe(args)

    assert exc_info.value.code == 1


def test_apply_stdin_pipe_ignores_unreadable_implicit_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BadStdin:
        def isatty(self) -> bool:
            return False

        def read(self, _size: int) -> str:
            raise OSError("broken")

    args = _args_for_stdin(stdin=False)
    monkeypatch.setattr(main.sys, "stdin", BadStdin())

    main.apply_stdin_pipe(args)

    assert args.non_interactive_message is None


def test_apply_stdin_pipe_errors_on_explicit_unreadable_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BadStdin:
        def isatty(self) -> bool:
            return False

        def read(self, _size: int) -> str:
            raise OSError("broken")

    args = _args_for_stdin(stdin=True)
    monkeypatch.setattr(main.sys, "stdin", BadStdin())

    with pytest.raises(SystemExit) as exc_info:
        main.apply_stdin_pipe(args)

    assert exc_info.value.code == 1


def test_apply_stdin_pipe_errors_on_unicode_decode_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BadStdin:
        def isatty(self) -> bool:
            return False

        def read(self, _size: int) -> str:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")

    args = _args_for_stdin(stdin=True)
    monkeypatch.setattr(main.sys, "stdin", BadStdin())

    with pytest.raises(SystemExit) as exc_info:
        main.apply_stdin_pipe(args)

    assert exc_info.value.code == 1


def test_apply_stdin_pipe_errors_on_oversized_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args_for_stdin(stdin=True)
    monkeypatch.setattr(
        main.sys, "stdin", _FakeStdin("x" * (10 * 1024 * 1024 + 1), is_tty=False)
    )

    with pytest.raises(SystemExit) as exc_info:
        main.apply_stdin_pipe(args)

    assert exc_info.value.code == 1


def test_apply_stdin_pipe_ignores_empty_piped_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args_for_stdin()
    monkeypatch.setattr(main.sys, "stdin", _FakeStdin("   \n\t", is_tty=False))
    monkeypatch.setattr(
        main.os,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no restore")),
    )

    main.apply_stdin_pipe(args)

    assert args.non_interactive_message is None


def test_apply_stdin_pipe_restores_tty_after_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args_for_stdin()
    opened_fds: list[int] = []
    dup_calls: list[tuple[int, int]] = []
    closed_fds: list[int] = []
    monkeypatch.setattr(main.sys, "stdin", _FakeStdin("context", is_tty=False))
    monkeypatch.setattr(
        main.os,
        "open",
        lambda *_args, **_kwargs: opened_fds.append(99) or 99,
    )
    monkeypatch.setattr(
        main.os,
        "dup2",
        lambda source, target: dup_calls.append((source, target)),
    )
    monkeypatch.setattr(main.os, "close", lambda fd: closed_fds.append(fd))
    monkeypatch.setattr(
        "builtins.open",
        lambda *_args, **_kwargs: _FakeStdin("", is_tty=True),
    )

    main.apply_stdin_pipe(args)

    assert args.non_interactive_message == "context"
    assert opened_fds == [99]
    assert dup_calls == [(99, 0)]
    assert closed_fds == [99]
    assert main.sys.stdin.isatty()


def test_apply_stdin_pipe_warns_when_tty_restore_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args_for_stdin(initial_prompt="task")
    closed_fds: list[int] = []
    monkeypatch.setattr(main.sys, "stdin", _FakeStdin("context", is_tty=False))
    monkeypatch.setattr(main.os, "open", lambda *_args, **_kwargs: 77)
    monkeypatch.setattr(main.os, "close", lambda fd: closed_fds.append(fd))
    monkeypatch.setattr(
        "builtins.open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("open failed")),
    )

    main.apply_stdin_pipe(args)

    assert args.initial_prompt == "context\n\ntask"
    assert closed_fds == [77]


def test_apply_stdin_pipe_warns_when_failed_restore_fd_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args_for_stdin(initial_prompt="task")
    monkeypatch.setattr(main.sys, "stdin", _FakeStdin("context", is_tty=False))
    monkeypatch.setattr(main.os, "open", lambda *_args, **_kwargs: 77)
    monkeypatch.setattr(
        main.os,
        "close",
        lambda _fd: (_ for _ in ()).throw(OSError("close failed")),
    )
    monkeypatch.setattr(
        "builtins.open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("open failed")),
    )

    main.apply_stdin_pipe(args)

    assert args.initial_prompt == "context\n\ntask"


def test_preload_session_mcp_server_info_cleans_up_session_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleaned = False

    class FakeSessionManager:
        async def cleanup(self) -> None:
            nonlocal cleaned
            cleaned = True

    async def fake_resolve_and_load_mcp_tools(
        **_kwargs: Any,
    ) -> tuple[list[Any], FakeSessionManager, list[str]]:
        return [], FakeSessionManager(), ["server"]

    mcp_tools = ModuleType("invincat_cli.mcp.tools")
    mcp_tools.resolve_and_load_mcp_tools = fake_resolve_and_load_mcp_tools
    project_utils = ModuleType("invincat_cli.project_utils")
    project_utils.ProjectContext = SimpleNamespace(
        from_user_cwd=staticmethod(lambda _cwd: "project-context")
    )
    monkeypatch.setitem(sys.modules, "invincat_cli.mcp.tools", mcp_tools)
    monkeypatch.setitem(sys.modules, "invincat_cli.project_utils", project_utils)

    result = asyncio.run(
        main._preload_session_mcp_server_info(
            mcp_config_path=None,
            no_mcp=False,
            trust_project_mcp=None,
        )
    )

    assert result == ["server"]
    assert cleaned is True


def test_preload_session_mcp_server_info_tolerates_context_and_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_contexts: list[Any] = []

    class BadSessionManager:
        async def cleanup(self) -> None:
            raise RuntimeError("cleanup failed")

    async def fake_resolve_and_load_mcp_tools(
        **kwargs: Any,
    ) -> tuple[list[Any], BadSessionManager, list[str]]:
        seen_contexts.append(kwargs["project_context"])
        return [], BadSessionManager(), ["server"]

    mcp_tools = ModuleType("invincat_cli.mcp.tools")
    mcp_tools.resolve_and_load_mcp_tools = fake_resolve_and_load_mcp_tools
    project_utils = ModuleType("invincat_cli.project_utils")
    project_utils.ProjectContext = SimpleNamespace(
        from_user_cwd=staticmethod(
            lambda _cwd: (_ for _ in ()).throw(OSError("cwd missing"))
        )
    )
    monkeypatch.setitem(sys.modules, "invincat_cli.mcp.tools", mcp_tools)
    monkeypatch.setitem(sys.modules, "invincat_cli.project_utils", project_utils)

    result = asyncio.run(
        main._preload_session_mcp_server_info(
            mcp_config_path=None,
            no_mcp=False,
            trust_project_mcp=None,
        )
    )

    assert result == ["server"]
    assert seen_contexts == [None]


def test_preload_session_mcp_server_info_returns_none_when_disabled() -> None:
    assert (
        asyncio.run(
            main._preload_session_mcp_server_info(
                mcp_config_path=None,
                no_mcp=True,
                trust_project_mcp=None,
            )
        )
        is None
    )


def _install_project_mcp_trust_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    project_configs: list[Path],
    servers: list[tuple[str, str, str]],
    trusted: bool = False,
) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []

    mcp_tools = ModuleType("invincat_cli.mcp.tools")
    mcp_tools.discover_mcp_configs = lambda project_context: project_configs
    mcp_tools.classify_discovered_configs = lambda configs: ([], configs)
    mcp_tools.load_mcp_config_lenient = lambda _path: {"mcpServers": {}}
    mcp_tools.extract_server_summaries = lambda _cfg: servers
    monkeypatch.setitem(sys.modules, "invincat_cli.mcp.tools", mcp_tools)

    project_utils = ModuleType("invincat_cli.project_utils")
    project_utils.ProjectContext = SimpleNamespace(
        from_user_cwd=staticmethod(
            lambda _cwd: SimpleNamespace(project_root=tmp_path, user_cwd=tmp_path)
        )
    )
    monkeypatch.setitem(sys.modules, "invincat_cli.project_utils", project_utils)

    mcp_trust = ModuleType("invincat_cli.mcp.trust")
    mcp_trust.compute_config_fingerprint = lambda _configs: "fingerprint"
    mcp_trust.is_project_mcp_trusted = lambda _root, _fingerprint: trusted

    def fake_trust_project_mcp(root: str, fingerprint: str) -> None:
        events.append((root, fingerprint))

    mcp_trust.trust_project_mcp = fake_trust_project_mcp
    monkeypatch.setitem(sys.modules, "invincat_cli.mcp.trust", mcp_trust)
    return events


def test_check_mcp_project_trust_returns_none_without_project_configs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_project_mcp_trust_fakes(
        monkeypatch,
        tmp_path,
        project_configs=[],
        servers=[],
    )

    assert main._check_mcp_project_trust() is None


def test_check_mcp_project_trust_allows_trust_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_project_mcp_trust_fakes(
        monkeypatch,
        tmp_path,
        project_configs=[tmp_path / ".mcp.json"],
        servers=[("srv", "stdio", "cmd")],
    )

    assert main._check_mcp_project_trust(trust_flag=True) is True


def test_check_mcp_project_trust_uses_persisted_trust(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_project_mcp_trust_fakes(
        monkeypatch,
        tmp_path,
        project_configs=[tmp_path / ".mcp.json"],
        servers=[("srv", "stdio", "cmd")],
        trusted=True,
    )

    assert main._check_mcp_project_trust() is True


def test_check_mcp_project_trust_interactive_yes_persists_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events = _install_project_mcp_trust_fakes(
        monkeypatch,
        tmp_path,
        project_configs=[tmp_path / ".mcp.json"],
        servers=[("srv", "stdio", "cmd")],
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    assert main._check_mcp_project_trust() is True
    assert events == [(str(tmp_path.resolve()), "fingerprint")]


def test_check_mcp_project_trust_interactive_no_denies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_project_mcp_trust_fakes(
        monkeypatch,
        tmp_path,
        project_configs=[tmp_path / ".mcp.json"],
        servers=[("srv", "stdio", "cmd")],
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    assert main._check_mcp_project_trust() is False


def test_check_mcp_project_trust_returns_none_on_context_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp_tools = ModuleType("invincat_cli.mcp.tools")
    mcp_tools.discover_mcp_configs = lambda project_context: []
    mcp_tools.classify_discovered_configs = lambda configs: ([], configs)
    mcp_tools.load_mcp_config_lenient = lambda _path: None
    mcp_tools.extract_server_summaries = lambda _cfg: []
    monkeypatch.setitem(sys.modules, "invincat_cli.mcp.tools", mcp_tools)

    project_utils = ModuleType("invincat_cli.project_utils")
    project_utils.ProjectContext = SimpleNamespace(
        from_user_cwd=staticmethod(
            lambda _cwd: (_ for _ in ()).throw(RuntimeError("no context"))
        )
    )
    monkeypatch.setitem(sys.modules, "invincat_cli.project_utils", project_utils)

    assert main._check_mcp_project_trust() is None


def test_check_mcp_project_trust_returns_none_without_servers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_project_mcp_trust_fakes(
        monkeypatch,
        tmp_path,
        project_configs=[tmp_path / ".mcp.json"],
        servers=[],
    )

    assert main._check_mcp_project_trust() is None


def test_check_mcp_project_trust_eof_denies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_project_mcp_trust_fakes(
        monkeypatch,
        tmp_path,
        project_configs=[tmp_path / ".mcp.json"],
        servers=[("srv", "stdio", "cmd")],
    )
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError)
    )

    assert main._check_mcp_project_trust() is False


def test_print_session_stats_ignores_non_session_stats() -> None:
    console = _FakeConsole()

    main._print_session_stats(object(), console)

    assert console.messages == []


def test_print_session_stats_prints_valid_session_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.textual_adapter as adapter_module
    from invincat_cli.core.session_stats import SessionStats

    calls: list[tuple[SessionStats, float, _FakeConsole]] = []
    monkeypatch.setattr(
        adapter_module,
        "print_usage_table",
        lambda stats, wall_time, console: calls.append((stats, wall_time, console)),
    )
    console = _FakeConsole()
    stats = SessionStats(wall_time_seconds=1.5)

    main._print_session_stats(stats, console)

    assert calls == [(stats, 1.5, console)]


def test_run_textual_cli_async_passes_deferred_server_kwargs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import invincat_cli.config as config_module

    class FakeAppResult:
        def __init__(self, *, return_code: int, thread_id: str | None) -> None:
            self.return_code = return_code
            self.thread_id = thread_id

    calls: list[dict[str, Any]] = []
    app_module = ModuleType("invincat_cli.app")
    app_module.AppResult = FakeAppResult
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        config_module, "_get_default_model_spec", lambda: "openai:gpt-test"
    )
    monkeypatch.setattr(config_module, "detect_provider", lambda _model: "openai")

    async def fake_run_textual_app(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return app_module.AppResult(return_code=0, thread_id="thread-1")

    app_module.run_textual_app = fake_run_textual_app
    monkeypatch.setitem(sys.modules, "invincat_cli.app", app_module)

    result = asyncio.run(
        main.run_textual_cli_async(
            "agent",
            auto_approve=True,
            sandbox_type="none",
            model_params={"temperature": 0},
            no_mcp=True,
        )
    )

    assert result.return_code == 0
    assert calls[0]["auto_approve"] is True
    assert calls[0]["cwd"] == tmp_path
    assert "auto_approve" not in calls[0]["server_kwargs"]
    assert calls[0]["server_kwargs"]["enable_ask_user"] is True
    assert calls[0]["mcp_preload_kwargs"] is None
    assert calls[0]["model_kwargs"]["model_spec"] == "openai:gpt-test"


def test_run_textual_cli_async_detects_provider_for_bare_model_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.config as config_module

    class FakeAppResult:
        def __init__(self, *, return_code: int, thread_id: str | None) -> None:
            self.return_code = return_code
            self.thread_id = thread_id

    calls: list[dict[str, Any]] = []
    app_module = ModuleType("invincat_cli.app")
    app_module.AppResult = FakeAppResult

    async def fake_run_textual_app(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return app_module.AppResult(return_code=0, thread_id=None)

    app_module.run_textual_app = fake_run_textual_app
    monkeypatch.setitem(sys.modules, "invincat_cli.app", app_module)
    monkeypatch.setattr(config_module, "_get_default_model_spec", lambda: "gpt-test")
    monkeypatch.setattr(config_module, "detect_provider", lambda _model: "openai")

    result = asyncio.run(main.run_textual_cli_async("agent"))

    assert result.return_code == 0
    assert calls[0]["model_kwargs"]["model_spec"] == "gpt-test"
    assert config_module.settings.model_name == "gpt-test"
    assert config_module.settings.model_provider == "openai"


def test_run_textual_cli_async_returns_error_on_model_config_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.config as config_module
    from invincat_cli.model_config import ModelConfigError

    class FakeAppResult:
        def __init__(self, *, return_code: int, thread_id: str | None) -> None:
            self.return_code = return_code
            self.thread_id = thread_id

    app_module = ModuleType("invincat_cli.app")
    app_module.AppResult = FakeAppResult
    app_module.run_textual_app = lambda **_kwargs: None
    monkeypatch.setitem(sys.modules, "invincat_cli.app", app_module)
    monkeypatch.setattr(config_module, "console", _FakeConsole())

    class BadModelName:
        calls = 0

        def __bool__(self) -> bool:
            self.calls += 1
            if self.calls == 1:
                raise ModelConfigError("missing key")
            return True

        def __str__(self) -> str:
            return "bad-model"

    result = asyncio.run(
        main.run_textual_cli_async("agent", model_name=BadModelName())  # type: ignore[arg-type]
    )

    assert result.return_code == 1
    assert result.thread_id is None


def test_run_textual_cli_async_prints_debug_traceback_on_app_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.config as config_module

    class FakeAppResult:
        def __init__(self, *, return_code: int, thread_id: str | None) -> None:
            self.return_code = return_code
            self.thread_id = thread_id

    async def broken_run_textual_app(**_kwargs: Any) -> Any:
        raise RuntimeError("ui boom")

    app_module = ModuleType("invincat_cli.app")
    app_module.AppResult = FakeAppResult
    app_module.run_textual_app = broken_run_textual_app
    console = _FakeConsole()
    monkeypatch.setitem(sys.modules, "invincat_cli.app", app_module)
    monkeypatch.setattr(
        config_module, "_get_default_model_spec", lambda: "openai:gpt-test"
    )
    monkeypatch.setattr(config_module, "console", console)
    monkeypatch.setattr(
        main.logger,
        "isEnabledFor",
        lambda level: level == main.logging.DEBUG,
    )

    result = asyncio.run(main.run_textual_cli_async("agent"))

    assert result.return_code == 1
    assert any("Traceback" in str(args[0]) for args, _kwargs in console.messages)


def test_run_textual_cli_async_defers_server_when_default_model_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.config as config_module
    from invincat_cli.model_config import ModelConfigError

    class FakeAppResult:
        def __init__(self, *, return_code: int, thread_id: str | None) -> None:
            self.return_code = return_code
            self.thread_id = thread_id

    calls: list[dict[str, Any]] = []
    app_module = ModuleType("invincat_cli.app")
    app_module.AppResult = FakeAppResult

    async def fake_run_textual_app(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return app_module.AppResult(return_code=0, thread_id=None)

    app_module.run_textual_app = fake_run_textual_app
    monkeypatch.setitem(sys.modules, "invincat_cli.app", app_module)
    monkeypatch.setattr(
        config_module,
        "_get_default_model_spec",
        lambda: (_ for _ in ()).throw(ModelConfigError("missing default")),
    )

    result = asyncio.run(main.run_textual_cli_async("agent"))

    assert result.return_code == 0
    assert calls[0]["defer_server_start"] is True
    assert calls[0]["model_kwargs"] is None
    assert config_module.settings.model_name == ""


def _install_acp_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    create_agent_error: Exception | None = None,
    mcp_error: Exception | None = None,
    cleanup_error: Exception | None = None,
    has_tavily: bool = False,
) -> list[str]:
    import invincat_cli.config as config_module
    import invincat_cli.model_config as model_config_module

    events: list[str] = []

    class FakeModelResult:
        provider = "openai"
        model_name = "gpt-test"
        model = object()

        def apply_to_settings(self) -> None:
            events.append("apply-settings")

    def fake_create_model(*_args: Any, **_kwargs: Any) -> FakeModelResult:
        return FakeModelResult()

    def fake_load_async_subagents() -> list[Any]:
        events.append("load-subagents")
        return []

    def fake_create_cli_agent(**_kwargs: Any) -> tuple[object, object]:
        if create_agent_error is not None:
            raise create_agent_error
        events.append("create-agent")
        return object(), object()

    agent_module = ModuleType("invincat_cli.agent")
    agent_module.create_cli_agent = fake_create_cli_agent
    agent_module.load_async_subagents = fake_load_async_subagents
    monkeypatch.setitem(sys.modules, "invincat_cli.agent", agent_module)
    monkeypatch.setattr(config_module, "create_model", fake_create_model)
    monkeypatch.setattr(
        config_module, "settings", SimpleNamespace(has_tavily=has_tavily)
    )
    monkeypatch.setattr(
        model_config_module,
        "save_recent_model",
        lambda value: events.append(f"save:{value}"),
    )

    class FakeSessionManager:
        async def cleanup(self) -> None:
            if cleanup_error is not None:
                raise cleanup_error
            events.append("cleanup")

    async def fake_resolve_and_load_mcp_tools(
        **_kwargs: Any,
    ) -> tuple[list[Any], Any, list[str]]:
        if mcp_error is not None:
            raise mcp_error
        events.append("mcp")
        return [], FakeSessionManager(), ["mcp-server"]

    mcp_tools = ModuleType("invincat_cli.mcp.tools")
    mcp_tools.resolve_and_load_mcp_tools = fake_resolve_and_load_mcp_tools
    monkeypatch.setitem(sys.modules, "invincat_cli.mcp.tools", mcp_tools)

    memory_module = ModuleType("langgraph.checkpoint.memory")
    memory_module.InMemorySaver = lambda: object()
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.memory", memory_module)

    return events


def test_run_acp_cli_async_success_cleans_up_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _install_acp_dependencies(monkeypatch)

    class FakeServer:
        def __init__(self, _graph: object) -> None:
            events.append("server")

    async def fake_run_acp_agent(_server: FakeServer) -> None:
        events.append("run")

    exit_code = asyncio.run(
        main._run_acp_cli_async(
            "agent",
            run_acp_agent=fake_run_acp_agent,
            agent_server_cls=FakeServer,
            no_mcp=True,
        )
    )

    assert exit_code == 0
    assert events == [
        "apply-settings",
        "save:openai:gpt-test",
        "mcp",
        "load-subagents",
        "create-agent",
        "server",
        "run",
        "cleanup",
    ]


def test_run_acp_cli_async_returns_error_on_model_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.config as config_module
    from invincat_cli.model_config import ModelConfigError

    monkeypatch.setattr(
        config_module,
        "create_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ModelConfigError("bad")),
    )

    exit_code = asyncio.run(
        main._run_acp_cli_async(
            "agent",
            run_acp_agent=lambda _server: None,
            agent_server_cls=object,
        )
    )

    assert exit_code == 1


def test_run_acp_cli_async_returns_error_on_mcp_load_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _install_acp_dependencies(
        monkeypatch,
        mcp_error=RuntimeError("mcp boom"),
    )

    exit_code = asyncio.run(
        main._run_acp_cli_async(
            "agent",
            run_acp_agent=lambda _server: None,
            agent_server_cls=object,
        )
    )

    assert exit_code == 1
    assert "create-agent" not in events


def test_run_acp_cli_async_returns_error_on_missing_mcp_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _install_acp_dependencies(
        monkeypatch,
        mcp_error=FileNotFoundError("missing-mcp.json"),
    )

    exit_code = asyncio.run(
        main._run_acp_cli_async(
            "agent",
            run_acp_agent=lambda _server: None,
            agent_server_cls=object,
        )
    )

    assert exit_code == 1
    assert "create-agent" not in events


def test_run_acp_cli_async_returns_error_on_agent_creation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _install_acp_dependencies(
        monkeypatch,
        create_agent_error=RuntimeError("agent boom"),
    )

    exit_code = asyncio.run(
        main._run_acp_cli_async(
            "agent",
            run_acp_agent=lambda _server: None,
            agent_server_cls=object,
        )
    )

    assert exit_code == 1
    assert "mcp" in events


def test_run_acp_cli_async_reports_server_failure_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _install_acp_dependencies(monkeypatch)

    class FakeServer:
        def __init__(self, _graph: object) -> None:
            events.append("server")

    async def fake_run_acp_agent(_server: FakeServer) -> None:
        events.append("run")
        raise RuntimeError("server boom")

    exit_code = asyncio.run(
        main._run_acp_cli_async(
            "agent",
            run_acp_agent=fake_run_acp_agent,
            agent_server_cls=FakeServer,
        )
    )

    assert exit_code == 1
    assert events[-2:] == ["run", "cleanup"]


def test_run_acp_cli_async_keyboard_interrupt_and_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _install_acp_dependencies(
        monkeypatch,
        cleanup_error=RuntimeError("cleanup boom"),
        has_tavily=True,
    )

    class FakeServer:
        def __init__(self, _graph: object) -> None:
            events.append("server")

    async def fake_run_acp_agent(_server: FakeServer) -> None:
        events.append("run")
        raise KeyboardInterrupt

    exit_code = asyncio.run(
        main._run_acp_cli_async(
            "agent",
            run_acp_agent=fake_run_acp_agent,
            agent_server_cls=FakeServer,
        )
    )

    assert exit_code == 0
    assert events[-2:] == ["server", "run"]


def _cli_args(**overrides: Any) -> SimpleNamespace:
    args: dict[str, Any] = {
        "model_params": None,
        "profile_override": None,
        "acp": False,
        "no_mcp": False,
        "mcp_config": None,
        "shell_allow_list": None,
        "command": None,
        "quiet": False,
        "no_stream": False,
        "non_interactive_message": None,
        "update": False,
        "clear_default_model": False,
        "default_model": None,
        "output_format": "text",
        "agent": "agent",
        "model": None,
        "trust_project_mcp": False,
        "sandbox": "none",
        "sandbox_id": None,
        "sandbox_setup": None,
        "resume_thread": None,
        "auto_approve": False,
        "initial_prompt": None,
        "thread_id": None,
        "agents_command": None,
        "threads_command": None,
        "limit": None,
        "sort": None,
        "branch": None,
        "verbose": False,
        "relative": None,
        "dry_run": False,
    }
    args.update(overrides)
    return SimpleNamespace(**args)


def _prepare_cli_main(
    monkeypatch: pytest.MonkeyPatch,
    args: SimpleNamespace,
    *,
    argv: list[str] | None = None,
) -> None:
    monkeypatch.setattr(main.sys, "argv", argv or ["deepagents"])
    monkeypatch.setattr(main.sys, "platform", "darwin")
    monkeypatch.setattr(main, "check_cli_dependencies", lambda: None)
    monkeypatch.setattr(main, "parse_args", lambda: args)
    monkeypatch.setattr(main, "apply_stdin_pipe", lambda _args: None)


def test_cli_main_version_fast_path_exits_without_dependency_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checked = False
    monkeypatch.setattr(main.sys, "argv", ["deepagents", "--version"])
    monkeypatch.setattr(main.sys, "platform", "darwin")
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda _name: "1.2.3",
    )
    monkeypatch.setattr(
        main,
        "check_cli_dependencies",
        lambda: (_ for _ in ()).throw(AssertionError("should not run")),
    )

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert checked is False
    assert exc_info.value.code == 0
    assert "deepagents (SDK) 1.2.3" in capsys.readouterr().out


@pytest.mark.parametrize("error_name", ["package-not-found", "runtime-error"])
def test_cli_main_version_fast_path_uses_unknown_sdk_version_when_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error_name: str,
) -> None:
    import importlib.metadata as metadata

    error: Exception
    if error_name == "package-not-found":
        error = metadata.PackageNotFoundError("deepagents")
    else:
        error = RuntimeError("metadata unavailable")

    monkeypatch.setattr(main.sys, "argv", ["deepagents", "--version"])
    monkeypatch.setattr(main.sys, "platform", "darwin")
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda _name: (_ for _ in ()).throw(error),
    )

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 0
    assert "deepagents (SDK) unknown" in capsys.readouterr().out


def test_cli_main_non_darwin_ensures_utf8_locale_before_version_fast_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(main.sys, "argv", ["deepagents", "--version"])
    monkeypatch.setattr(main.sys, "platform", "linux")
    monkeypatch.setattr(main, "_ensure_utf8_locale", lambda: calls.append("utf8"))
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "1.2.3")

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 0
    assert calls == ["utf8"]


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"model_params": "{"}, 1),
        ({"model_params": "[]"}, 1),
        ({"profile_override": "{"}, 1),
        ({"profile_override": "[]"}, 1),
        ({"no_mcp": True, "mcp_config": "mcp.json"}, 2),
        ({"quiet": True}, 2),
        ({"quiet": True, "no_stream": True}, 2),
    ],
)
def test_cli_main_rejects_invalid_headless_arguments(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    expected_code: int,
) -> None:
    _prepare_cli_main(monkeypatch, _cli_args(**overrides))

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == expected_code


def test_cli_main_acp_exits_when_dependencies_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "acp", None)
    _prepare_cli_main(monkeypatch, _cli_args(acp=True))

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 1


def test_cli_main_acp_rejects_conflicting_mcp_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acp_module = ModuleType("acp")
    acp_module.run_agent = lambda _server: None
    server_module = ModuleType("deepagents_acp.server")
    server_module.AgentServerACP = object
    monkeypatch.setitem(sys.modules, "acp", acp_module)
    monkeypatch.setitem(sys.modules, "deepagents_acp.server", server_module)
    _prepare_cli_main(
        monkeypatch,
        _cli_args(acp=True, no_mcp=True, mcp_config="mcp.json"),
    )

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 2


def test_cli_main_acp_delegates_to_async_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acp_module = ModuleType("acp")
    run_agent = object()
    acp_module.run_agent = run_agent
    server_module = ModuleType("deepagents_acp.server")
    server_cls = object()
    server_module.AgentServerACP = server_cls
    monkeypatch.setitem(sys.modules, "acp", acp_module)
    monkeypatch.setitem(sys.modules, "deepagents_acp.server", server_module)
    calls: list[dict[str, Any]] = []

    async def fake_run_acp_cli_async(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 6

    monkeypatch.setattr(main, "_run_acp_cli_async", fake_run_acp_cli_async)
    _prepare_cli_main(
        monkeypatch,
        _cli_args(
            acp=True,
            agent="coder",
            model="openai:gpt-test",
            model_params='{"temperature": 0}',
            profile_override='{"max_input_tokens": 1000}',
            mcp_config="mcp.json",
            trust_project_mcp=True,
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 6
    assert calls == [
        {
            "assistant_id": "coder",
            "run_acp_agent": run_agent,
            "agent_server_cls": server_cls,
            "model_name": "openai:gpt-test",
            "model_params": {"temperature": 0},
            "profile_override": {"max_input_tokens": 1000},
            "mcp_config_path": "mcp.json",
            "no_mcp": False,
            "trust_project_mcp": True,
        }
    ]


def test_cli_main_applies_shell_allow_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.config as config_module

    events: list[str] = []
    _prepare_cli_main(
        monkeypatch,
        _cli_args(command="help", shell_allow_list="recommended"),
    )
    monkeypatch.setattr(
        config_module,
        "parse_shell_allow_list",
        lambda value: events.append(value) or frozenset({"git"}),
    )
    ui_module = ModuleType("invincat_cli.ui")
    ui_module.show_help = lambda: events.append("help")
    monkeypatch.setitem(sys.modules, "invincat_cli.ui", ui_module)

    main.cli_main()

    assert events == ["recommended", "help"]
    assert config_module.settings.shell_allow_list == frozenset({"git"})


@pytest.mark.parametrize(
    ("available", "latest", "upgrade_success", "expected_code"),
    [
        (False, None, True, 1),
        (False, "1.0.0", True, 0),
        (True, "9.9.9", True, 0),
        (True, "9.9.9", False, 1),
    ],
)
def test_cli_main_update_paths_exit_with_expected_status(
    monkeypatch: pytest.MonkeyPatch,
    available: bool,
    latest: str | None,
    upgrade_success: bool,
    expected_code: int,
) -> None:
    update_module = ModuleType("invincat_cli.update_check")
    update_module.is_update_available = lambda *, bypass_cache: (available, latest)

    async def fake_perform_upgrade() -> tuple[bool, str]:
        return upgrade_success, "upgrade output"

    update_module.perform_upgrade = fake_perform_upgrade
    update_module.upgrade_command = lambda: "uv tool upgrade deepagents-cli"
    monkeypatch.setitem(sys.modules, "invincat_cli.update_check", update_module)

    _prepare_cli_main(monkeypatch, _cli_args(update=True))

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == expected_code


def test_cli_main_update_reports_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update_module = ModuleType("invincat_cli.update_check")
    update_module.is_update_available = lambda *, bypass_cache: (_ for _ in ()).throw(
        RuntimeError("network boom")
    )
    monkeypatch.setitem(sys.modules, "invincat_cli.update_check", update_module)

    _prepare_cli_main(monkeypatch, _cli_args(update=True))

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 1


@pytest.mark.parametrize(("succeeds", "expected_code"), [(True, 0), (False, 1)])
def test_cli_main_clear_default_model_exits(
    monkeypatch: pytest.MonkeyPatch,
    succeeds: bool,
    expected_code: int,
) -> None:
    import invincat_cli.model_config as model_config

    _prepare_cli_main(monkeypatch, _cli_args(clear_default_model=True))
    monkeypatch.setattr(model_config, "clear_default_model", lambda: succeeds)

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == expected_code


def test_cli_main_shows_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.model_config as model_config

    _prepare_cli_main(monkeypatch, _cli_args(default_model="__SHOW__"))
    monkeypatch.setattr(
        model_config.ModelConfig,
        "load",
        classmethod(lambda cls: SimpleNamespace(default_model="openai:gpt-test")),
    )

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 0


def test_cli_main_shows_empty_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.model_config as model_config

    _prepare_cli_main(monkeypatch, _cli_args(default_model="__SHOW__"))
    monkeypatch.setattr(
        model_config.ModelConfig,
        "load",
        classmethod(lambda cls: SimpleNamespace(default_model=None)),
    )

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 0


@pytest.mark.parametrize(("save_result", "expected_code"), [(True, 0), (False, 1)])
def test_cli_main_saves_default_model_with_provider_detection(
    monkeypatch: pytest.MonkeyPatch,
    save_result: bool,
    expected_code: int,
) -> None:
    import invincat_cli.config as config_module
    import invincat_cli.model_config as model_config

    saved: list[str] = []
    _prepare_cli_main(monkeypatch, _cli_args(default_model="gpt-test"))
    monkeypatch.setattr(config_module, "detect_provider", lambda _model: "openai")
    monkeypatch.setattr(
        model_config,
        "save_default_model",
        lambda value: saved.append(value) or save_result,
    )

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == expected_code
    assert saved == ["openai:gpt-test"]


def test_cli_main_dispatches_agents_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []
    _prepare_cli_main(
        monkeypatch,
        _cli_args(command="agents", agents_command="ls", output_format="json"),
    )
    agent_module = ModuleType("invincat_cli.agent")
    agent_module.list_agents = lambda **kwargs: events.append(("agents", kwargs))
    ui_module = ModuleType("invincat_cli.ui")
    ui_module.show_agents_help = lambda: events.append(("agents-help", None))
    monkeypatch.setitem(sys.modules, "invincat_cli.agent", agent_module)
    monkeypatch.setitem(sys.modules, "invincat_cli.ui", ui_module)

    main.cli_main()

    assert events == [("agents", {"output_format": "json"})]


def test_cli_main_dispatches_help_agents_help_skills_wecombot_and_threads_help(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    ui_module = ModuleType("invincat_cli.ui")
    ui_module.show_help = lambda: events.append("help")
    ui_module.show_agents_help = lambda: events.append("agents-help")
    ui_module.show_threads_help = lambda: events.append("threads-help")
    monkeypatch.setitem(sys.modules, "invincat_cli.ui", ui_module)

    skills_module = ModuleType("invincat_cli.skills")
    skills_module.execute_skills_command = lambda _args: events.append("skills")
    monkeypatch.setitem(sys.modules, "invincat_cli.skills", skills_module)

    _prepare_cli_main(monkeypatch, _cli_args(command="help"))
    main.cli_main()

    _prepare_cli_main(monkeypatch, _cli_args(command="agents", agents_command=None))
    agent_module = ModuleType("invincat_cli.agent")
    agent_module.list_agents = lambda **_kwargs: events.append("agents-list")
    monkeypatch.setitem(sys.modules, "invincat_cli.agent", agent_module)
    main.cli_main()

    _prepare_cli_main(monkeypatch, _cli_args(command="skills"))
    main.cli_main()

    monkeypatch.setattr(
        main, "_run_wecombot_foreground", lambda _console: events.append("wecombot")
    )
    _prepare_cli_main(monkeypatch, _cli_args(command="wecombot"))
    main.cli_main()

    sessions_module = ModuleType("invincat_cli.sessions")
    sessions_module.list_threads_command = lambda **_kwargs: None
    sessions_module.delete_thread_command = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "invincat_cli.sessions", sessions_module)
    _prepare_cli_main(monkeypatch, _cli_args(command="threads", threads_command=None))
    main.cli_main()

    assert events == ["help", "agents-help", "skills", "wecombot", "threads-help"]


def test_cli_main_dispatches_threads_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []
    sessions_module = ModuleType("invincat_cli.sessions")

    async def fake_list_threads_command(**kwargs: Any) -> None:
        events.append(("list", kwargs))

    async def fake_delete_thread_command(
        thread_id: str,
        *,
        dry_run: bool,
        output_format: str,
    ) -> None:
        events.append(
            (
                "delete",
                {
                    "thread_id": thread_id,
                    "dry_run": dry_run,
                    "output_format": output_format,
                },
            )
        )

    sessions_module.list_threads_command = fake_list_threads_command
    sessions_module.delete_thread_command = fake_delete_thread_command
    monkeypatch.setitem(sys.modules, "invincat_cli.sessions", sessions_module)

    _prepare_cli_main(
        monkeypatch,
        _cli_args(
            command="threads",
            threads_command="list",
            agent="coder",
            limit=3,
            sort="created",
            branch="main",
            verbose=True,
            relative=True,
            output_format="json",
        ),
    )
    main.cli_main()

    _prepare_cli_main(
        monkeypatch,
        _cli_args(
            command="threads",
            threads_command="delete",
            thread_id="thread-1",
            dry_run=True,
            output_format="json",
        ),
    )
    main.cli_main()

    assert events == [
        (
            "list",
            {
                "agent_name": "coder",
                "limit": 3,
                "sort_by": "created",
                "branch": "main",
                "verbose": True,
                "relative": True,
                "output_format": "json",
            },
        ),
        (
            "delete",
            {
                "thread_id": "thread-1",
                "dry_run": True,
                "output_format": "json",
            },
        ),
    ]


def test_cli_main_non_interactive_runs_agent_and_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    non_interactive_module = ModuleType("invincat_cli.non_interactive")

    async def fake_run_non_interactive(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 7

    non_interactive_module.run_non_interactive = fake_run_non_interactive
    monkeypatch.setitem(
        sys.modules, "invincat_cli.non_interactive", non_interactive_module
    )
    monkeypatch.setattr(main, "check_optional_tools", lambda: [])
    _prepare_cli_main(
        monkeypatch,
        _cli_args(
            non_interactive_message="do work",
            quiet=True,
            no_stream=True,
            model_params='{"temperature": 0}',
            profile_override='{"max_input_tokens": 1000}',
            thread_id="thread-1",
            sandbox="none",
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 7
    assert calls[0]["message"] == "do work"
    assert calls[0]["model_params"] == {"temperature": 0}
    assert calls[0]["profile_override"] == {"max_input_tokens": 1000}
    assert calls[0]["stream"] is False


def test_cli_main_non_interactive_warns_for_optional_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    non_interactive_module = ModuleType("invincat_cli.non_interactive")

    async def fake_run_non_interactive(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    non_interactive_module.run_non_interactive = fake_run_non_interactive
    monkeypatch.setitem(
        sys.modules, "invincat_cli.non_interactive", non_interactive_module
    )
    monkeypatch.setattr(main, "check_optional_tools", lambda: ["git"])
    _prepare_cli_main(monkeypatch, _cli_args(non_interactive_message="do work"))

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 0
    assert calls[0]["message"] == "do work"


def test_cli_main_non_interactive_skips_optional_tools_when_rich_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    non_interactive_module = ModuleType("invincat_cli.non_interactive")

    async def fake_run_non_interactive(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    non_interactive_module.run_non_interactive = fake_run_non_interactive
    monkeypatch.setitem(
        sys.modules, "invincat_cli.non_interactive", non_interactive_module
    )
    monkeypatch.setitem(sys.modules, "rich.console", None)
    monkeypatch.setattr(
        main,
        "check_optional_tools",
        lambda: (_ for _ in ()).throw(AssertionError("should not check tools")),
    )
    _prepare_cli_main(monkeypatch, _cli_args(non_interactive_message="do work"))

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 0
    assert calls[0]["message"] == "do work"


def test_cli_main_non_interactive_exits_on_sandbox_dependency_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox_module = ModuleType("invincat_cli.integrations.sandbox_factory")
    sandbox_module.verify_sandbox_deps = lambda _name: (_ for _ in ()).throw(
        ImportError("missing sdk")
    )
    monkeypatch.setitem(
        sys.modules, "invincat_cli.integrations.sandbox_factory", sandbox_module
    )
    _prepare_cli_main(
        monkeypatch,
        _cli_args(non_interactive_message="do work", sandbox="modal"),
    )
    monkeypatch.setattr(main, "check_optional_tools", lambda: [])

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 1


def test_cli_main_non_interactive_ignores_optional_tool_warning_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    non_interactive_module = ModuleType("invincat_cli.non_interactive")

    async def fake_run_non_interactive(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    non_interactive_module.run_non_interactive = fake_run_non_interactive
    monkeypatch.setitem(
        sys.modules, "invincat_cli.non_interactive", non_interactive_module
    )
    monkeypatch.setattr(
        main,
        "check_optional_tools",
        lambda: (_ for _ in ()).throw(RuntimeError("tool check failed")),
    )
    _prepare_cli_main(monkeypatch, _cli_args(non_interactive_message="do work"))

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 0
    assert calls[0]["message"] == "do work"


def test_cli_main_interactive_runs_textual_and_prints_exit_hints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.config as config_module
    import invincat_cli.sessions as sessions_module

    calls: list[dict[str, Any]] = []
    _prepare_cli_main(
        monkeypatch,
        _cli_args(
            auto_approve=True,
            initial_prompt="hello",
            sandbox="none",
            trust_project_mcp=True,
        ),
    )
    monkeypatch.setattr(sessions_module, "generate_thread_id", lambda: "thread-new")

    async def fake_thread_exists(_thread_id: str) -> bool:
        return True

    monkeypatch.setattr(sessions_module, "thread_exists", fake_thread_exists)
    monkeypatch.setattr(
        config_module,
        "build_langsmith_thread_url",
        lambda thread_id: f"https://langsmith.test/{thread_id}",
    )
    monkeypatch.setattr(main, "_check_mcp_project_trust", lambda **_kwargs: True)
    monkeypatch.setattr(main, "_print_session_stats", lambda *_args: None)

    async def fake_run_textual_cli_async(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(
            return_code=0,
            thread_id="thread-final",
            session_stats=None,
            update_available=(False, None),
        )

    monkeypatch.setattr(main, "run_textual_cli_async", fake_run_textual_cli_async)

    main.cli_main()

    assert calls[0]["assistant_id"] == "agent"
    assert calls[0]["auto_approve"] is True
    assert calls[0]["thread_id"] == "thread-new"
    assert calls[0]["initial_prompt"] == "hello"
    assert calls[0]["trust_project_mcp"] is True


def test_cli_main_interactive_exits_on_sandbox_dependency_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.sessions as sessions_module

    sandbox_module = ModuleType("invincat_cli.integrations.sandbox_factory")
    sandbox_module.verify_sandbox_deps = lambda _name: (_ for _ in ()).throw(
        ImportError("missing sdk")
    )
    monkeypatch.setitem(
        sys.modules, "invincat_cli.integrations.sandbox_factory", sandbox_module
    )
    monkeypatch.setattr(sessions_module, "generate_thread_id", lambda: "thread-new")
    _prepare_cli_main(monkeypatch, _cli_args(sandbox="modal"))

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 1


def test_cli_main_interactive_reports_textual_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.sessions as sessions_module

    monkeypatch.setattr(sessions_module, "generate_thread_id", lambda: "thread-new")
    monkeypatch.setattr(main, "_check_mcp_project_trust", lambda **_kwargs: None)

    async def broken_run_textual_cli_async(**_kwargs: Any) -> Any:
        raise RuntimeError("ui boom")

    monkeypatch.setattr(main, "run_textual_cli_async", broken_run_textual_cli_async)
    _prepare_cli_main(monkeypatch, _cli_args())

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 1


def test_cli_main_interactive_prints_update_banner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.sessions as sessions_module

    calls: list[dict[str, Any]] = []
    update_module = ModuleType("invincat_cli.update_check")
    update_module.upgrade_command = lambda: "uv tool upgrade deepagents-cli"
    update_module.is_auto_update_enabled = lambda: False
    monkeypatch.setitem(sys.modules, "invincat_cli.update_check", update_module)
    monkeypatch.setattr(sessions_module, "generate_thread_id", lambda: "thread-new")

    async def fake_thread_exists(_thread_id: str) -> bool:
        return False

    monkeypatch.setattr(sessions_module, "thread_exists", fake_thread_exists)
    monkeypatch.setattr(main, "_check_mcp_project_trust", lambda **_kwargs: None)
    monkeypatch.setattr(main, "_print_session_stats", lambda *_args: None)

    async def fake_run_textual_cli_async(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(
            return_code=0,
            thread_id=None,
            session_stats=None,
            update_available=(True, "9.9.9"),
        )

    monkeypatch.setattr(main, "run_textual_cli_async", fake_run_textual_cli_async)
    _prepare_cli_main(monkeypatch, _cli_args())

    main.cli_main()

    assert calls[0]["thread_id"] == "thread-new"


def test_cli_main_interactive_ignores_exit_banner_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.sessions as sessions_module

    update_module = ModuleType("invincat_cli.update_check")
    update_module.upgrade_command = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    update_module.is_auto_update_enabled = lambda: True
    monkeypatch.setitem(sys.modules, "invincat_cli.update_check", update_module)
    monkeypatch.setattr(sessions_module, "generate_thread_id", lambda: "thread-new")

    async def broken_thread_exists(_thread_id: str) -> bool:
        raise RuntimeError("db down")

    monkeypatch.setattr(sessions_module, "thread_exists", broken_thread_exists)
    monkeypatch.setattr(main, "_check_mcp_project_trust", lambda **_kwargs: None)
    monkeypatch.setattr(main, "_print_session_stats", lambda *_args: None)

    async def fake_run_textual_cli_async(**_kwargs: Any) -> Any:
        return SimpleNamespace(
            return_code=0,
            thread_id="thread-final",
            session_stats=None,
            update_available=(True, "9.9.9"),
        )

    monkeypatch.setattr(main, "run_textual_cli_async", fake_run_textual_cli_async)
    _prepare_cli_main(monkeypatch, _cli_args())

    main.cli_main()


def test_cli_main_interactive_ignores_langsmith_link_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.config as config_module
    import invincat_cli.sessions as sessions_module

    monkeypatch.setattr(sessions_module, "generate_thread_id", lambda: "thread-new")
    monkeypatch.setattr(
        config_module,
        "build_langsmith_thread_url",
        lambda _thread_id: (_ for _ in ()).throw(RuntimeError("url failed")),
    )
    monkeypatch.setattr(main, "_check_mcp_project_trust", lambda **_kwargs: None)
    monkeypatch.setattr(main, "_print_session_stats", lambda *_args: None)

    async def fake_run_textual_cli_async(**_kwargs: Any) -> Any:
        return SimpleNamespace(
            return_code=0,
            thread_id="thread-final",
            session_stats=None,
            update_available=(False, None),
        )

    monkeypatch.setattr(main, "run_textual_cli_async", fake_run_textual_cli_async)
    _prepare_cli_main(monkeypatch, _cli_args())

    main.cli_main()


def test_cli_main_keyboard_interrupt_handles_missing_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[str] = []
    monkeypatch.setattr(main.sys, "argv", ["deepagents"])
    monkeypatch.setattr(main.sys, "platform", "darwin")
    monkeypatch.setattr(main, "check_cli_dependencies", lambda: None)
    monkeypatch.setattr(
        main,
        "parse_args",
        lambda: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    monkeypatch.setattr(main.sys.stderr, "write", lambda text: writes.append(text))

    with pytest.raises(SystemExit) as exc_info:
        main.cli_main()

    assert exc_info.value.code == 0
    assert "Interrupted" in "".join(writes)


def test_run_wecombot_foreground_exits_on_invalid_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.wecom import daemon as daemon_module

    console = _FakeConsole()
    monkeypatch.setattr(
        daemon_module.WeComDaemonConfig,
        "from_env",
        classmethod(lambda cls, cwd: (_ for _ in ()).throw(ValueError("missing"))),
    )

    with pytest.raises(SystemExit) as exc_info:
        main._run_wecombot_foreground(console)

    assert exc_info.value.code == 1


def test_run_wecombot_foreground_runs_daemon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from invincat_cli.wecom import daemon as daemon_module

    console = _FakeConsole()
    config = object()
    seen: list[Any] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        daemon_module.WeComDaemonConfig,
        "from_env",
        classmethod(lambda cls, cwd: config),
    )
    monkeypatch.setattr(
        daemon_module, "run_daemon_foreground", lambda value: seen.append(value)
    )

    with pytest.raises(SystemExit) as exc_info:
        main._run_wecombot_foreground(console)

    assert exc_info.value.code == 0
    assert seen == [config]


def test_ensure_utf8_locale_sets_environment_when_fallback_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_setlocale(_category: int, value: str) -> str:
        calls.append(value)
        if value == "":
            return "C"
        if value == "C.UTF-8":
            return value
        raise locale.Error

    monkeypatch.setattr(locale, "setlocale", fake_setlocale)
    monkeypatch.setattr(locale, "getpreferredencoding", lambda _do_setlocale: "GBK")
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)

    main._ensure_utf8_locale()

    assert calls[:2] == ["", "C.UTF-8"]
    assert main.os.environ["LANG"] == "C.UTF-8"
    assert main.os.environ["LC_ALL"] == "C.UTF-8"


def test_ensure_utf8_locale_noops_when_encoding_is_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        locale,
        "setlocale",
        lambda _category, value: calls.append(value) or "en_US.UTF-8",
    )
    monkeypatch.setattr(locale, "getpreferredencoding", lambda _do_setlocale: "UTF-8")

    main._ensure_utf8_locale()

    assert calls == [""]


def test_ensure_utf8_locale_sets_env_and_warns_when_locale_switches_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[str] = []

    def fake_setlocale(_category: int, _value: str) -> str:
        raise locale.Error

    monkeypatch.setattr(locale, "setlocale", fake_setlocale)
    monkeypatch.setattr(locale, "getpreferredencoding", lambda _do_setlocale: "GBK")
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.setattr(main.sys.stderr, "write", lambda text: writes.append(text))

    main._ensure_utf8_locale()

    assert main.os.environ["LANG"] == "en_US.UTF-8"
    assert main.os.environ["LC_ALL"] == "en_US.UTF-8"
    assert "terminal locale is not UTF-8" in "".join(writes)
