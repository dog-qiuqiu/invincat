"""Unit tests for configuration module."""

import os
import sys
import tempfile
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest

import invincat_cli.config as config_mod
from invincat_cli.config import (
    RECOMMENDED_SAFE_SHELL_COMMANDS,
    SHELL_ALLOW_ALL,
    Settings,
    build_stream_config,
    contains_dangerous_patterns,
    get_glyphs,
    is_ascii_mode,
    is_shell_command_allowed,
    parse_shell_allow_list,
    reset_glyphs_cache,
)
from invincat_cli.model_config import ModelConfig, ModelConfigError


class TestSettings:
    """Tests for Settings class."""

    def test_from_environment(self):
        """Test creating settings from environment."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up test environment
            test_env = {
                "OPENAI_API_KEY": "test-openai-key",
                "ANTHROPIC_API_KEY": "test-anthropic-key",
                "TAVILY_API_KEY": "test-tavily-key",
                "DEEPAGENTS_CLI_SHELL_ALLOW_LIST": "ls,cd,pwd",
            }

            with patch.dict(os.environ, test_env, clear=True):
                settings = Settings.from_environment(start_path=Path(tmpdir))

                assert settings.openai_api_key == "test-openai-key"
                assert settings.anthropic_api_key == "test-anthropic-key"
                assert settings.tavily_api_key == "test-tavily-key"
                assert settings.shell_allow_list == ["ls", "cd", "pwd"]
                assert settings.project_root is None  # Not a git repo

    def test_get_agent_dir(self):
        """Test getting agent directory path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("invincat_cli.config.Path.home", return_value=Path(tmpdir)):
                settings = Settings.from_environment()
                agent_dir = settings.get_agent_dir("test-agent")

                expected = Path(tmpdir) / ".invincat" / "test-agent"
                assert agent_dir == expected
                assert "test-agent" in str(agent_dir)

    def test_provider_key_properties(self):
        """Test provider key availability properties."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_environment()

            assert not settings.has_openai
            assert not settings.has_anthropic

            settings.openai_api_key = "test-key"
            settings.anthropic_api_key = "test-key"

            assert settings.has_openai
            assert settings.has_anthropic

    def test_reload_from_environment_reports_masked_changes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Runtime reload refreshes config/env fields without exposing secrets."""
        import invincat_cli.model_config as model_config_mod

        project_root = tmp_path / "repo"
        extra_dir = tmp_path / "skills"
        monkeypatch.setattr(config_mod, "_load_dotenv", lambda *, start_path: False)
        monkeypatch.setattr(
            "invincat_cli.project_utils.find_project_root",
            lambda start_path=None: project_root,
        )
        values = {
            "OPENAI_API_KEY": None,
            "ANTHROPIC_API_KEY": "new-anthropic",
            "GOOGLE_API_KEY": "new-google",
            "NVIDIA_API_KEY": None,
            "TAVILY_API_KEY": "new-tavily",
            "GOOGLE_CLOUD_PROJECT": "new-project",
            "DEEPAGENTS_CLI_LANGSMITH_PROJECT": "trace-project",
        }
        monkeypatch.setattr(
            model_config_mod, "resolve_env_var", lambda name: values.get(name)
        )
        monkeypatch.setenv("DEEPAGENTS_CLI_SHELL_ALLOW_LIST", "recommended")
        monkeypatch.setenv("DEEPAGENTS_CLI_EXTRA_SKILLS_DIRS", str(extra_dir))

        settings = Settings(
            openai_api_key="old-openai",
            anthropic_api_key=None,
            google_api_key=None,
            nvidia_api_key="old-nvidia",
            tavily_api_key=None,
            google_cloud_project="old-project",
            deepagents_langchain_project=None,
            user_langchain_project="user-project",
            project_root=None,
            shell_allow_list=["ls"],
            extra_skills_dirs=None,
            model_name="kept-model",
            model_provider="kept-provider",
            model_context_limit=123,
        )

        changes = settings.reload_from_environment(start_path=tmp_path)

        assert "openai_api_key: set -> unset" in changes
        assert "anthropic_api_key: unset -> set" in changes
        assert settings.project_root == project_root
        assert settings.shell_allow_list == list(
            config_mod.RECOMMENDED_SAFE_SHELL_COMMANDS
        )
        assert settings.extra_skills_dirs == [extra_dir.resolve()]
        assert os.environ["LANGSMITH_PROJECT"] == "trace-project"
        assert settings.model_name == "kept-model"
        assert settings.model_provider == "kept-provider"
        assert settings.model_context_limit == 123

    def test_reload_from_environment_keeps_previous_values_on_errors(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Reload preserves prior shell/project values when refresh helpers fail."""
        import invincat_cli.model_config as model_config_mod

        monkeypatch.setattr(config_mod, "_load_dotenv", lambda *, start_path: False)
        monkeypatch.setattr(
            "invincat_cli.project_utils.find_project_root",
            lambda start_path=None: (_ for _ in ()).throw(OSError("bad cwd")),
        )
        monkeypatch.setattr(
            model_config_mod,
            "resolve_env_var",
            lambda name: {"DEEPAGENTS_CLI_LANGSMITH_PROJECT": None}.get(name),
        )
        monkeypatch.setenv("DEEPAGENTS_CLI_SHELL_ALLOW_LIST", "ls,all")
        monkeypatch.setenv("LANGSMITH_PROJECT", "trace-project")
        monkeypatch.setattr(config_mod, "_original_langsmith_project", "user-project")

        previous_root = tmp_path / "previous"
        settings = Settings(
            openai_api_key=None,
            anthropic_api_key=None,
            google_api_key=None,
            nvidia_api_key=None,
            tavily_api_key=None,
            google_cloud_project=None,
            deepagents_langchain_project="trace-project",
            user_langchain_project="user-project",
            project_root=previous_root,
            shell_allow_list=["ls"],
        )

        changes = settings.reload_from_environment(start_path=tmp_path)

        assert settings.project_root == previous_root
        assert settings.shell_allow_list == ["ls"]
        assert os.environ["LANGSMITH_PROJECT"] == "user-project"
        assert "deepagents_langchain_project: trace-project -> None" in changes

        monkeypatch.setattr(config_mod, "_original_langsmith_project", None)
        monkeypatch.setenv("LANGSMITH_PROJECT", "trace-project")
        settings.deepagents_langchain_project = "trace-project"
        settings.reload_from_environment(start_path=tmp_path)
        assert "LANGSMITH_PROJECT" not in os.environ

    def test_path_helpers_validation_and_session_state(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Settings path helpers validate names and create expected directories."""
        monkeypatch.setattr("invincat_cli.config.Path.home", lambda: tmp_path)
        monkeypatch.setattr(
            "invincat_cli.sessions.generate_thread_id", lambda: "thread-1"
        )
        settings = Settings(
            openai_api_key=None,
            anthropic_api_key=None,
            google_api_key=None,
            nvidia_api_key="nvidia-key",
            tavily_api_key="tavily-key",
            google_cloud_project="project",
            deepagents_langchain_project=None,
            user_langchain_project=None,
            project_root=tmp_path / "repo",
            extra_skills_dirs=[tmp_path / "extra"],
        )

        assert settings.has_nvidia
        assert settings.has_tavily
        assert settings.has_vertex_ai
        assert not settings.has_google
        assert settings.user_deepagents_dir == tmp_path / ".invincat"
        assert (
            settings.get_agent_dir("test agent")
            == tmp_path / ".invincat" / "test agent"
        )
        assert settings.ensure_agent_dir("agent").is_dir()
        assert settings.ensure_user_skills_dir("agent").is_dir()
        assert settings.ensure_user_agent_skills_dir().is_dir()
        assert (
            settings.get_project_skills_dir()
            == tmp_path / "repo" / ".invincat" / "skills"
        )
        project_skills_dir = settings.ensure_project_skills_dir()
        assert project_skills_dir is not None
        assert project_skills_dir.is_dir()
        assert (
            settings.get_project_agent_skills_dir()
            == tmp_path / "repo" / ".agents" / "skills"
        )
        assert settings.get_user_claude_skills_dir() == tmp_path / ".claude" / "skills"
        assert (
            settings.get_project_claude_skills_dir()
            == tmp_path / "repo" / ".claude" / "skills"
        )
        built_in_skills_dir = settings.get_built_in_skills_dir()
        assert built_in_skills_dir.name == "built_in_skills"
        assert built_in_skills_dir.parent.name == "invincat_cli"
        assert (built_in_skills_dir / "skill-creator" / "SKILL.md").is_file()
        assert settings.get_extra_skills_dirs() == [tmp_path / "extra"]

        with pytest.raises(ValueError, match="Invalid agent name"):
            settings.get_agent_dir("../bad")
        with pytest.raises(ValueError, match="Invalid agent name"):
            settings.ensure_agent_dir("")

        no_project = Settings(
            openai_api_key=None,
            anthropic_api_key=None,
            google_api_key="google-key",
            nvidia_api_key=None,
            tavily_api_key=None,
            google_cloud_project="project",
            deepagents_langchain_project=None,
            user_langchain_project=None,
        )
        assert not no_project.has_vertex_ai
        assert no_project.get_project_skills_dir() is None
        assert no_project.ensure_project_skills_dir() is None
        assert no_project.get_project_agent_skills_dir() is None
        assert no_project.get_project_claude_skills_dir() is None

        broken_project = Settings(
            openai_api_key=None,
            anthropic_api_key=None,
            google_api_key=None,
            nvidia_api_key=None,
            tavily_api_key=None,
            google_cloud_project=None,
            deepagents_langchain_project=None,
            user_langchain_project=None,
            project_root=tmp_path / "repo",
        )
        broken_project.get_project_skills_dir = lambda: None  # type: ignore[method-assign]
        assert broken_project.ensure_project_skills_dir() is None

        state = config_mod.SessionState(auto_approve=True, no_splash=True)
        assert state.thread_id == "thread-1"
        assert state.no_splash is True
        assert state.toggle_auto_approve() is False


class TestUtilityFunctions:
    """Tests for utility functions."""

    def test_dotenv_discovery_and_layered_loading(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Project dotenv wins over global dotenv while preserving shell env."""
        project = tmp_path / "repo"
        nested = project / "src" / "pkg"
        nested.mkdir(parents=True)
        (project / ".env").write_text(
            "PROJECT_ONLY=project\nSHARED=project\nSHELL_WINS=project\n",
            encoding="utf-8",
        )
        global_env = tmp_path / "global.env"
        global_env.write_text(
            "GLOBAL_ONLY=global\nSHARED=global\nSHELL_WINS=global\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(config_mod, "_GLOBAL_DOTENV_PATH", global_env)
        monkeypatch.setenv("SHELL_WINS", "shell")
        monkeypatch.delenv("PROJECT_ONLY", raising=False)
        monkeypatch.delenv("GLOBAL_ONLY", raising=False)
        monkeypatch.delenv("SHARED", raising=False)

        assert config_mod._find_dotenv_from_start_path(nested) == project / ".env"
        assert config_mod._load_dotenv(start_path=nested) is True

        assert os.environ["PROJECT_ONLY"] == "project"
        assert os.environ["GLOBAL_ONLY"] == "global"
        assert os.environ["SHARED"] == "project"
        assert os.environ["SHELL_WINS"] == "shell"

    def test_dotenv_error_tolerance_and_default_start_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Dotenv loading tolerates project/global read errors."""
        calls: list[dict[str, object]] = []

        class DotenvStub:
            @staticmethod
            def load_dotenv(**kwargs: object) -> bool:
                calls.append(kwargs)
                if "dotenv_path" in kwargs:
                    raise OSError("bad dotenv")
                return True

        monkeypatch.setitem(sys.modules, "dotenv", DotenvStub)
        monkeypatch.setattr(config_mod, "_GLOBAL_DOTENV_PATH", tmp_path / "missing.env")

        assert config_mod._load_dotenv() is True
        assert calls == [{"override": False}]

        global_env = tmp_path / "global.env"
        global_env.write_text("X=1\n", encoding="utf-8")
        monkeypatch.setattr(config_mod, "_GLOBAL_DOTENV_PATH", global_env)
        assert config_mod._load_dotenv(start_path=tmp_path) is False

        project = tmp_path / "repo"
        project.mkdir()
        (project / ".env").write_text("X=1\n", encoding="utf-8")

        class ProjectDotenvStub:
            @staticmethod
            def load_dotenv(**_kwargs: object) -> bool:
                raise ValueError("bad project env")

        monkeypatch.setitem(sys.modules, "dotenv", ProjectDotenvStub)
        monkeypatch.setattr(config_mod, "_GLOBAL_DOTENV_PATH", tmp_path / "missing.env")
        assert config_mod._load_dotenv(start_path=project) is False

    def test_find_dotenv_tolerates_candidate_stat_errors(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        def broken_is_file(self: Path) -> bool:
            if self.name == ".env":
                raise OSError("cannot stat")
            return False

        monkeypatch.setattr(Path, "is_file", broken_is_file)

        assert config_mod._find_dotenv_from_start_path(tmp_path) is None

    def test_read_config_toml_skills_dirs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Skills config reader accepts only list-shaped extra dirs."""
        import invincat_cli.model_config as model_config_mod

        config_path = tmp_path / "config.toml"
        monkeypatch.setattr(model_config_mod, "DEFAULT_CONFIG_PATH", config_path)

        assert config_mod._read_config_toml_skills_dirs() is None

        config_path.write_text(
            '[skills]\nextra_allowed_dirs = ["one", "two"]\n',
            encoding="utf-8",
        )
        assert config_mod._read_config_toml_skills_dirs() == ["one", "two"]

        config_path.write_text(
            "[skills]\nextra_allowed_dirs = 'bad'\n", encoding="utf-8"
        )
        assert config_mod._read_config_toml_skills_dirs() is None

        config_path.write_text("[bad", encoding="utf-8")
        assert config_mod._read_config_toml_skills_dirs() is None

    def test_bootstrap_langsmith_env_propagation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Bootstrap preserves caller project and propagates prefixed SDK env vars."""
        import invincat_cli.project_utils as project_utils_mod

        ctx = SimpleNamespace(user_cwd=tmp_path)
        monkeypatch.setattr(
            project_utils_mod, "get_server_project_context", lambda: ctx
        )
        monkeypatch.setattr(config_mod, "_load_dotenv", lambda *, start_path: False)
        monkeypatch.setattr(config_mod, "_bootstrap_done", False)
        monkeypatch.setattr(config_mod, "_bootstrap_start_path", None)
        monkeypatch.setattr(config_mod, "_original_langsmith_project", None)
        monkeypatch.setenv("LANGSMITH_PROJECT", "user-project")
        monkeypatch.setenv("DEEPAGENTS_CLI_LANGSMITH_PROJECT", "agent-project")
        monkeypatch.setenv("DEEPAGENTS_CLI_LANGSMITH_API_KEY", "prefixed-key")
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)

        config_mod._ensure_bootstrap()

        assert config_mod._bootstrap_done is True
        assert config_mod._bootstrap_start_path == tmp_path
        assert config_mod._original_langsmith_project == "user-project"
        assert os.environ["LANGSMITH_PROJECT"] == "agent-project"
        assert os.environ["LANGSMITH_API_KEY"] == "prefixed-key"

    def test_bootstrap_idempotent_conflict_and_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Bootstrap handles fast paths, env conflicts, and partial failures."""
        monkeypatch.setattr(config_mod, "_bootstrap_done", True)
        monkeypatch.setattr(
            config_mod,
            "_load_dotenv",
            lambda *, start_path: (_ for _ in ()).throw(AssertionError("called")),
        )
        config_mod._ensure_bootstrap()

        monkeypatch.setattr(config_mod, "_bootstrap_done", False)
        monkeypatch.setattr(
            "invincat_cli.project_utils.get_server_project_context", lambda: None
        )
        monkeypatch.setattr(config_mod, "_load_dotenv", lambda *, start_path: False)
        monkeypatch.setenv("LANGSMITH_API_KEY", "canonical")
        monkeypatch.setenv("DEEPAGENTS_CLI_LANGSMITH_API_KEY", "prefixed")
        config_mod._ensure_bootstrap()
        assert config_mod._bootstrap_done is True

        monkeypatch.setattr(config_mod, "_bootstrap_done", False)

        class MarkDoneLock:
            def __enter__(self):
                monkeypatch.setattr(config_mod, "_bootstrap_done", True)

            def __exit__(self, *_args: object) -> None:
                return None

        monkeypatch.setattr(config_mod, "_bootstrap_lock", MarkDoneLock())
        config_mod._ensure_bootstrap()
        assert os.environ["LANGSMITH_API_KEY"] == "canonical"

        monkeypatch.setattr(config_mod, "_bootstrap_done", False)
        monkeypatch.setattr(config_mod, "_bootstrap_lock", config_mod.threading.Lock())
        monkeypatch.setattr(
            "invincat_cli.project_utils.get_server_project_context",
            lambda: (_ for _ in ()).throw(RuntimeError("no context")),
        )
        config_mod._ensure_bootstrap()
        assert config_mod._bootstrap_done is True

    def test_editable_install_metadata_and_banner(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Editable install helpers cache PEP 610 metadata and banner selection."""
        source = tmp_path / "src"
        source.mkdir()

        class FakeDist:
            def read_text(self, name: str) -> str:
                assert name == "direct_url.json"
                return (
                    '{"url":"file://' + str(source) + '","dir_info":{"editable":true}}'
                )

        monkeypatch.setattr(config_mod, "_editable_cache", None)
        monkeypatch.setattr(config_mod, "distribution", lambda _name: FakeDist())
        monkeypatch.setattr("invincat_cli.config.Path.home", lambda: tmp_path)

        assert config_mod._resolve_editable_info() == (True, "~/src")
        assert config_mod._is_editable_install() is True
        assert config_mod._get_editable_install_path() == "~/src"

        monkeypatch.setattr(
            config_mod,
            "distribution",
            lambda _name: (_ for _ in ()).throw(AssertionError("cached")),
        )
        assert config_mod._resolve_editable_info() == (True, "~/src")

        monkeypatch.setattr(
            config_mod, "_detect_charset_mode", lambda: config_mod.CharsetMode.ASCII
        )
        assert "version:" in config_mod.get_banner()
        monkeypatch.setattr(config_mod, "_editable_cache", None)
        monkeypatch.setattr(
            config_mod,
            "distribution",
            lambda _name: SimpleNamespace(read_text=lambda _file: "{bad"),
        )
        assert config_mod._resolve_editable_info() == (False, None)

    def test_is_ascii_mode(self):
        """Test ASCII mode detection."""
        reset_glyphs_cache()
        with patch.dict(os.environ, {"UI_CHARSET_MODE": "unicode"}):
            assert get_glyphs() is config_mod.UNICODE_GLYPHS
        reset_glyphs_cache()
        with patch.dict(os.environ, {"UI_CHARSET_MODE": "ascii"}):
            assert get_glyphs() is config_mod.ASCII_GLYPHS
        reset_glyphs_cache()

        # Test with ASCII locale when stdout encoding is non-UTF.
        with (
            patch("sys.stdout", new=Mock(encoding="ascii")),
            patch.dict(os.environ, {"LC_ALL": "C", "LANG": "C"}),
        ):
            assert is_ascii_mode() is True

        # UTF-8 stdout encoding should force Unicode mode.
        with (
            patch("sys.stdout", new=Mock(encoding="UTF-8")),
            patch.dict(os.environ, {"LC_ALL": "C", "LANG": "C"}),
        ):
            assert is_ascii_mode() is False

        # UTF-8 locale should force Unicode even if stdout encoding is ASCII.
        with (
            patch("sys.stdout", new=Mock(encoding="ascii")),
            patch.dict(os.environ, {"LC_ALL": "", "LANG": "en_US.UTF-8"}),
        ):
            assert is_ascii_mode() is False

        reset_glyphs_cache()
        with patch.dict(os.environ, {"UI_CHARSET_MODE": "ascii"}):
            cached = get_glyphs()
            assert get_glyphs() is cached
        reset_glyphs_cache()

        assert config_mod.newline_shortcut() == "Ctrl+J"
        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(
                config_mod,
                "_detect_charset_mode",
                lambda: config_mod.CharsetMode.UNICODE,
            )
            assert "version:" in config_mod.get_banner()
        finally:
            monkeypatch.undo()

    def test_parse_shell_allow_list_and_extra_skill_dirs(self, tmp_path: Path):
        """Shell allow-list parsing handles sentinels, merging, and de-duping."""
        assert parse_shell_allow_list(None) is None
        assert parse_shell_allow_list("all") is SHELL_ALLOW_ALL
        assert parse_shell_allow_list("recommended") == list(
            RECOMMENDED_SAFE_SHELL_COMMANDS
        )
        merged = parse_shell_allow_list("ls, recommended, ls, custom")
        assert merged is not None
        assert merged.count("ls") == 1
        assert "cat" in merged
        assert merged[-1] == "custom"

        with pytest.raises(ValueError, match="Cannot combine 'all'"):
            parse_shell_allow_list("ls,all")

        env_dirs = config_mod._parse_extra_skills_dirs(
            f" {tmp_path / 'one'} :{tmp_path / 'two'}: "
        )
        assert env_dirs == [
            (tmp_path / "one").resolve(),
            (tmp_path / "two").resolve(),
        ]
        config_dirs = config_mod._parse_extra_skills_dirs(
            None,
            [str(tmp_path / "three"), "", 123],  # type: ignore[list-item]
        )
        assert config_dirs == [(tmp_path / "three").resolve()]
        assert config_mod._parse_extra_skills_dirs("", []) is None

    def test_is_shell_command_allowed(self):
        """Test shell command allow list checking."""
        # Test with no allow list (all commands require approval)
        assert is_shell_command_allowed("ls", None) is False
        assert is_shell_command_allowed("cd /tmp", None) is False
        assert is_shell_command_allowed("rm -rf /", SHELL_ALLOW_ALL) is True

        # Test with allow list
        allow_list = ["ls", "cd", "pwd", "/bin/ls"]
        assert is_shell_command_allowed("ls", allow_list) is True
        assert is_shell_command_allowed("cd /tmp", allow_list) is True
        assert is_shell_command_allowed("rm -rf /", allow_list) is False
        assert is_shell_command_allowed("ls -la", allow_list) is True  # ls with args
        assert is_shell_command_allowed("/bin/ls", allow_list) is True
        assert is_shell_command_allowed("./script.sh", allow_list) is False
        assert (
            is_shell_command_allowed("cat ./README.md", ["cat"], cwd=Path.cwd()) is True
        )
        assert (
            is_shell_command_allowed("cat ~/.ssh/id_rsa", ["cat"], cwd=Path.cwd())
            is False
        )
        assert (
            is_shell_command_allowed("grep token ../secrets", ["grep"], cwd=Path.cwd())
            is False
        )
        assert is_shell_command_allowed("ls $(pwd)", ["ls"]) is False
        assert is_shell_command_allowed("echo $HOME", ["echo"]) is False
        assert is_shell_command_allowed("ls 'unterminated", ["ls"]) is False
        assert is_shell_command_allowed("  ; ls", ["ls"]) is True
        assert contains_dangerous_patterns("sleep 1 &") is True
        assert config_mod._path_arg_stays_within_cwd("-", Path.cwd()) is True
        assert (
            config_mod._path_arg_stays_within_cwd(
                "missing-file", Path("/definitely/missing")
            )
            is True
        )
        assert config_mod._path_arg_stays_within_cwd("/etc/passwd", Path.cwd()) is False

        with patch.object(Path, "resolve", side_effect=OSError("bad path")):
            assert config_mod._path_arg_stays_within_cwd("./file", Path.cwd()) is False

    def test_build_stream_config(self, monkeypatch: pytest.MonkeyPatch):
        """Test building stream configuration."""
        monkeypatch.setenv("DEEPAGENTS_CLI_USER_ID", "user-1")
        monkeypatch.setattr(config_mod, "_get_git_branch", lambda: "main")
        config = build_stream_config("thread-1", "assistant-1", sandbox_type="modal")

        assert "configurable" in config
        assert "metadata" in config
        assert config["configurable"]["thread_id"] == "thread-1"
        assert config["metadata"]["assistant_id"] == "assistant-1"
        assert config["metadata"]["user_id"] == "user-1"
        assert config["metadata"]["git_branch"] == "main"
        assert config["metadata"]["sandbox_type"] == "modal"

        monkeypatch.setattr(
            "invincat_cli.config.Path.cwd",
            lambda: (_ for _ in ()).throw(OSError("cwd missing")),
        )
        no_cwd = build_stream_config("thread-2", None, sandbox_type="none")
        assert "cwd" not in no_cwd["metadata"]
        assert "assistant_id" not in no_cwd["metadata"]
        assert "sandbox_type" not in no_cwd["metadata"]

    def test_git_branch_cache_and_failures(self, monkeypatch: pytest.MonkeyPatch):
        """Git branch lookup caches success and handles cwd/subprocess failures."""
        config_mod._git_branch_cache.clear()
        monkeypatch.setattr("invincat_cli.config.Path.cwd", lambda: Path("/repo"))
        run_calls = []

        def fake_run(*args, **kwargs):
            run_calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout="main\n")

        monkeypatch.setattr("subprocess.run", fake_run)
        assert config_mod._get_git_branch() == "main"
        assert config_mod._get_git_branch() == "main"
        assert len(run_calls) == 1

        config_mod._git_branch_cache.clear()
        monkeypatch.setattr(
            "subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError())
        )
        assert config_mod._get_git_branch() is None

        monkeypatch.setattr(
            "invincat_cli.config.Path.cwd",
            lambda: (_ for _ in ()).throw(OSError("cwd missing")),
        )
        assert config_mod._get_git_branch() is None

    def test_langsmith_project_and_thread_url(self, monkeypatch: pytest.MonkeyPatch):
        """LangSmith helpers use configured project field and cache fetched URLs."""
        values = {
            "LANGSMITH_API_KEY": "key",
            "LANGSMITH_TRACING": "true",
        }
        monkeypatch.setattr(
            "invincat_cli.model_config.resolve_env_var",
            lambda name: values.get(name),
        )
        monkeypatch.setattr(
            config_mod,
            "_get_settings",
            lambda: SimpleNamespace(deepagents_langchain_project="cli-project"),
        )
        assert config_mod.get_langsmith_project_name() == "cli-project"

        monkeypatch.setattr(
            config_mod,
            "_get_settings",
            lambda: SimpleNamespace(deepagents_langchain_project=None),
        )
        monkeypatch.setenv("LANGSMITH_PROJECT", "env-project")
        assert config_mod.get_langsmith_project_name() == "env-project"
        monkeypatch.delenv("LANGSMITH_PROJECT")
        assert config_mod.get_langsmith_project_name() == "deepagents-cli"

        values.clear()
        assert config_mod.get_langsmith_project_name() is None

        config_mod.reset_langsmith_url_cache()
        values.update({"LANGSMITH_API_KEY": "key", "LANGSMITH_TRACING": "true"})

        class FakeClient:
            calls = 0

            def __init__(self, *, api_key):
                assert api_key == "key"

            def read_project(self, *, project_name):
                FakeClient.calls += 1
                assert project_name == "cli-project"
                return SimpleNamespace(url="https://smith/projects/p")

        monkeypatch.setitem(
            sys.modules, "langsmith", SimpleNamespace(Client=FakeClient)
        )
        assert (
            config_mod.fetch_langsmith_project_url("cli-project")
            == "https://smith/projects/p"
        )
        assert (
            config_mod.fetch_langsmith_project_url("cli-project")
            == "https://smith/projects/p"
        )
        assert FakeClient.calls == 1
        monkeypatch.setattr(
            config_mod,
            "_get_settings",
            lambda: SimpleNamespace(deepagents_langchain_project="cli-project"),
        )
        assert (
            config_mod.build_langsmith_thread_url("thread-1")
            == "https://smith/projects/p/t/thread-1?utm_source=deepagents-cli"
        )

    def test_langsmith_project_url_failure_paths(self, monkeypatch: pytest.MonkeyPatch):
        """LangSmith URL lookup tolerates missing SDK, SDK errors, and no project."""
        config_mod.reset_langsmith_url_cache()
        monkeypatch.delitem(sys.modules, "langsmith", raising=False)
        monkeypatch.setitem(sys.modules, "langsmith", None)
        assert config_mod.fetch_langsmith_project_url("missing-sdk") is None

        class RaisingClient:
            def __init__(self, *, api_key):
                pass

            def read_project(self, *, project_name):
                raise RuntimeError("langsmith down")

        monkeypatch.setitem(
            sys.modules, "langsmith", SimpleNamespace(Client=RaisingClient)
        )
        assert config_mod.fetch_langsmith_project_url("broken") is None

        monkeypatch.setattr(config_mod, "get_langsmith_project_name", lambda: None)
        assert config_mod.build_langsmith_thread_url("thread") is None
        monkeypatch.setattr(config_mod, "get_langsmith_project_name", lambda: "project")
        monkeypatch.setattr(
            config_mod, "fetch_langsmith_project_url", lambda _name: None
        )
        assert config_mod.build_langsmith_thread_url("thread") is None

    def test_langsmith_project_url_timeout_path(self, monkeypatch: pytest.MonkeyPatch):
        config_mod.reset_langsmith_url_cache()

        class FakeEvent:
            def wait(self, _timeout: float) -> bool:
                return False

            def set(self) -> None:
                return None

        class FakeThread:
            def __init__(self, *, target, daemon):  # noqa: ANN001
                self.target = target
                self.daemon = daemon

            def start(self) -> None:
                return None

        monkeypatch.setattr(config_mod.threading, "Event", FakeEvent)
        monkeypatch.setattr(config_mod.threading, "Thread", FakeThread)

        assert config_mod.fetch_langsmith_project_url("slow-project") is None

    def test_detect_provider_variants(self, monkeypatch: pytest.MonkeyPatch):
        """Provider inference handles known model prefixes and VertexAI preference."""
        monkeypatch.setattr(
            config_mod,
            "_get_settings",
            lambda: SimpleNamespace(
                has_anthropic=False,
                has_vertex_ai=True,
                has_google=False,
            ),
        )
        assert config_mod.detect_provider("gpt-4o") == "openai"
        assert config_mod.detect_provider("claude-sonnet") == "google_vertexai"
        assert config_mod.detect_provider("gemini-2.5-pro") == "google_vertexai"
        assert config_mod.detect_provider("nemotron-mini") == "nvidia"
        assert config_mod.detect_provider("nvidia/llama") == "nvidia"
        assert config_mod.detect_provider("unknown-model") is None

        monkeypatch.setattr(
            config_mod,
            "_get_settings",
            lambda: SimpleNamespace(
                has_anthropic=True,
                has_vertex_ai=True,
                has_google=True,
            ),
        )
        assert config_mod.detect_provider("claude-sonnet") == "anthropic"
        assert config_mod.detect_provider("gemini-2.5-pro") == "google_genai"


class TestModelCreation:
    """Tests for model creation functions."""

    @patch("invincat_cli.model_config.ModelConfig.load")
    def test_default_model_spec_requires_registered_model(self, mock_model_config_load):
        """Environment credentials alone should not select a default model."""
        from invincat_cli.config import _get_default_model_spec

        mock_model_config_load.return_value = ModelConfig()

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            with pytest.raises(ModelConfigError, match="No model configured"):
                _get_default_model_spec()

    @patch("invincat_cli.model_config.ModelConfig.load")
    def test_default_model_spec_ignores_unregistered_recent(
        self, mock_model_config_load
    ):
        """Stale recent/default values are ignored unless they are registered."""
        from invincat_cli.config import _get_default_model_spec

        mock_model_config_load.return_value = ModelConfig(
            default_model="openai:gpt-5.2",
            recent_model="anthropic:claude-sonnet-4-6",
            providers={
                "openai": {
                    "models": ["gpt-4o"],
                },
            },
        )

        assert _get_default_model_spec() == "openai:gpt-4o"

    @patch("invincat_cli.model_config.ModelConfig.load")
    def test_default_model_spec_uses_registered_recent(self, mock_model_config_load):
        """Registered recent model takes priority over first registered model."""
        from invincat_cli.config import _get_default_model_spec

        mock_model_config_load.return_value = ModelConfig(
            recent_model="google_genai:gemini-2.5-pro",
            providers={
                "openai": {
                    "models": ["gpt-4o"],
                },
                "google_genai": {
                    "models": ["gemini-2.5-pro"],
                },
            },
        )

        assert _get_default_model_spec() == "google_genai:gemini-2.5-pro"

    @patch("invincat_cli.model_config.ModelConfig.load")
    def test_default_model_spec_uses_registered_default_and_bare_name(
        self, mock_model_config_load
    ):
        """Registered defaults win, including legacy bare-model preferences."""
        from invincat_cli.config import (
            _get_default_memory_model_spec,
            _get_default_model_spec,
        )

        mock_model_config_load.return_value = ModelConfig(
            default_model="openai:gpt-4o",
            memory_default_model="openai:gpt-4o-mini",
            providers={
                "openai": {
                    "models": ["gpt-4o", "gpt-4o-mini"],
                },
            },
        )
        assert _get_default_model_spec() == "openai:gpt-4o"
        assert _get_default_memory_model_spec() == "openai:gpt-4o-mini"

        mock_model_config_load.return_value = ModelConfig(
            providers={
                "openai": {
                    "models": ["gpt-4o"],
                },
            },
        )
        assert _get_default_memory_model_spec() is None

        mock_model_config_load.return_value = ModelConfig(
            default_model="gpt-4o",
            providers={
                "openai": {
                    "models": ["gpt-4o"],
                },
            },
        )
        assert _get_default_model_spec() == "openai:gpt-4o"

    @patch("invincat_cli.config._create_model_via_init")
    @patch("invincat_cli.model_config.ModelConfig.load")
    @patch(
        "invincat_cli.config._get_provider_kwargs", return_value={"api_key": "test-key"}
    )
    def test_create_model_openai(
        self, _mock_kwargs, mock_model_config_load, mock_create_via_init
    ):
        """Test creating OpenAI model returns ModelResult."""
        from invincat_cli.config import create_model

        mock_model = Mock()
        mock_model.profile = {"max_input_tokens": 128000, "image_inputs": False}
        mock_create_via_init.return_value = mock_model

        mock_cfg = Mock()
        mock_cfg.get_class_path.return_value = None
        mock_cfg.get_profile_overrides.return_value = {}
        mock_model_config_load.return_value = mock_cfg

        result = create_model("openai:gpt-4o")

        assert result.model is mock_model
        assert result.model_name == "gpt-4o"
        assert result.provider == "openai"
        assert result.context_limit == 128000
        assert "image" in result.unsupported_modalities

    def test_create_model_invalid_model_spec(self):
        """Test invalid provider:model syntax."""
        from invincat_cli.config import create_model

        with pytest.raises(ModelConfigError, match="model name is required"):
            create_model("openai:")

    @patch("invincat_cli.config._create_model_via_init")
    @patch("invincat_cli.model_config.ModelConfig.load")
    @patch("invincat_cli.config._get_provider_kwargs", return_value={})
    def test_create_model_default_bare_and_profile_overrides(
        self,
        _mock_kwargs,
        mock_model_config_load,
        mock_create_via_init,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Model creation handles default spec, bare names, kwargs, and profiles."""
        from invincat_cli.config import ModelResult, create_model

        mock_model = Mock()
        mock_model.profile = {"audio_inputs": False}
        mock_create_via_init.return_value = mock_model

        mock_cfg = Mock()
        mock_cfg.get_class_path.return_value = None
        mock_cfg.get_profile_overrides.return_value = {"max_input_tokens": 32000}
        mock_model_config_load.return_value = mock_cfg

        monkeypatch.setattr(
            config_mod, "_get_default_model_spec", lambda: "openai:gpt-4o"
        )
        result = create_model(extra_kwargs={"temperature": 0.2})
        assert result.model_name == "gpt-4o"
        assert result.provider == "openai"
        assert result.context_limit == 32000
        assert "audio" in result.unsupported_modalities
        assert mock_create_via_init.call_args.args == (
            "gpt-4o",
            "openai",
            {"temperature": 0.2},
        )

        monkeypatch.setattr(config_mod, "detect_provider", lambda _name: "anthropic")
        create_model(":claude-sonnet", profile_overrides={"video_inputs": False})
        assert mock_create_via_init.call_args.args[0:2] == (
            "claude-sonnet",
            "anthropic",
        )
        assert mock_model.profile["video_inputs"] is False

        create_model("bare-model")
        assert mock_create_via_init.call_args.args[0:2] == (
            "bare-model",
            "anthropic",
        )

        mock_cfg.get_class_path.return_value = (
            "invincat_cli.models.testing:DeterministicIntegrationChatModel"
        )
        class_result = create_model("testing:fake")
        assert class_result.model_name == "fake"
        assert class_result.provider == "testing"

        settings = SimpleNamespace()
        monkeypatch.setattr(config_mod, "_get_settings", lambda: settings)
        ModelResult(
            mock_model, "name", "provider", 1, frozenset({"image"})
        ).apply_to_settings()
        assert settings.model_name == "name"
        assert settings.model_provider == "provider"
        assert settings.model_context_limit == 1
        assert settings.model_unsupported_modalities == frozenset({"image"})

    @patch("invincat_cli.models.deepseek_chat_openai.DeepSeekChatOpenAICompat")
    @patch("invincat_cli.model_config.ModelConfig.load")
    @patch(
        "invincat_cli.config._get_provider_kwargs",
        return_value={
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com/v1",
            "reasoning_effort": "medium",
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    )
    def test_create_model_strips_reasoning_effort_when_deepseek_thinking_disabled(
        self,
        _mock_kwargs,
        mock_model_config_load,
        mock_deepseek_model,
    ):
        """DeepSeek rejects reasoning_effort when thinking is explicitly disabled."""
        from invincat_cli.config import create_model

        mock_model = Mock()
        mock_model.profile = {}
        mock_deepseek_model.return_value = mock_model

        mock_cfg = Mock()
        mock_cfg.get_class_path.return_value = None
        mock_cfg.get_profile_overrides.return_value = {}
        mock_model_config_load.return_value = mock_cfg

        create_model("openai:deepseek-chat")

        _, kwargs = mock_deepseek_model.call_args
        assert kwargs["model"] == "deepseek-chat"
        assert kwargs["extra_body"]["thinking"]["type"] == "disabled"
        assert "reasoning_effort" not in kwargs

    def test_provider_kwargs_resolve_env_base_url_and_openrouter_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Provider kwargs merge config, env credentials, and OpenRouter defaults."""
        from invincat_cli.config import _get_provider_kwargs

        class FakeConfig:
            def get_kwargs(self, provider, *, model_name=None):
                if provider == "direct":
                    return {"api_key": "inline", "base_url": "https://inline"}
                if provider == "openrouter":
                    return {}
                if provider == "config-key":
                    return {}
                return {"api_key_env": "CUSTOM_KEY"}

            def get_base_url(self, provider):
                return "https://base.example" if provider == "custom" else None

            def get_api_key_env(self, provider):
                return "CONFIG_KEY" if provider == "config-key" else None

        monkeypatch.setattr("invincat_cli.model_config.ModelConfig.load", FakeConfig)
        monkeypatch.setattr(
            "invincat_cli.model_config.resolve_env_var",
            lambda name: {"CUSTOM_KEY": "custom", "CONFIG_KEY": "configured"}.get(name),
        )
        custom = _get_provider_kwargs("custom", model_name="model")
        assert custom == {"base_url": "https://base.example", "api_key": "custom"}
        assert _get_provider_kwargs("config-key") == {"api_key": "configured"}
        assert _get_provider_kwargs("direct") == {
            "api_key": "inline",
            "base_url": "https://inline",
        }

        openrouter = _get_provider_kwargs("openrouter")
        assert openrouter["app_url"] == "https://pypi.org/project/deepagents-cli/"
        assert openrouter["app_title"] == "Deep Agents CLI"
        assert openrouter["app_categories"] == ["cli-agent"]

    def test_provider_kwargs_runs_openrouter_version_check(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """OpenRouter provider invokes optional SDK compatibility checks."""
        from invincat_cli.config import _get_provider_kwargs

        calls: list[str] = []

        class FakeConfig:
            def get_kwargs(self, provider, *, model_name=None):
                return {}

            def get_base_url(self, provider):
                return None

            def get_api_key_env(self, provider):
                return None

        monkeypatch.setattr("invincat_cli.model_config.ModelConfig.load", FakeConfig)
        monkeypatch.setattr(
            "invincat_cli.model_config.resolve_env_var", lambda _name: None
        )
        monkeypatch.setitem(
            sys.modules,
            "deepagents._models",
            SimpleNamespace(check_openrouter_version=lambda: calls.append("checked")),
        )

        _get_provider_kwargs("openrouter")

        assert calls == ["checked"]

    def test_custom_model_class_import_and_error_paths(self):
        """Custom model class loading validates import path, type, and constructor."""
        from langchain_core.language_models import BaseChatModel
        from langchain_core.messages import BaseMessage
        from langchain_core.outputs import ChatResult

        from invincat_cli.config import _create_model_from_class

        class RaisingChatModel(BaseChatModel):
            def __init__(self, *args, **kwargs):
                raise RuntimeError("boom")

            def _generate(
                self,
                messages: list[BaseMessage],
                stop: list[str] | None = None,
                **kwargs: Any,
            ) -> ChatResult:
                raise AssertionError("unreachable")

            @property
            def _llm_type(self) -> str:
                return "raising"

        sys.modules["config_test_models"] = SimpleNamespace(
            RaisingChatModel=RaisingChatModel
        )

        model = _create_model_from_class(
            "invincat_cli.models.testing:DeterministicIntegrationChatModel",
            "fake",
            "testing",
            {},
        )
        assert model.model == "fake"

        with pytest.raises(ModelConfigError, match="module.path:ClassName"):
            _create_model_from_class("bad.path", "model", "provider", {})
        with pytest.raises(ModelConfigError, match="Could not import module"):
            _create_model_from_class(
                "missing_config_test_module:Model", "model", "p", {}
            )
        with pytest.raises(ModelConfigError, match="Class 'Missing' not found"):
            _create_model_from_class("json:Missing", "model", "p", {})
        with pytest.raises(ModelConfigError, match="not a BaseChatModel subclass"):
            _create_model_from_class("json:loads", "model", "p", {})
        with pytest.raises(ModelConfigError, match="Failed to instantiate"):
            _create_model_from_class(
                "config_test_models:RaisingChatModel", "model", "p", {}
            )

    def test_create_model_via_init_wraps_langchain_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """LangChain init failures are converted to user-facing config errors."""
        import importlib.util

        import langchain.chat_models as chat_models

        from invincat_cli.config import _create_model_via_init

        monkeypatch.setattr(
            chat_models,
            "init_chat_model",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                ImportError("missing provider")
            ),
        )
        monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
        with pytest.raises(ModelConfigError, match="Missing package"):
            _create_model_via_init("model", "openai", {})

        monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
        with pytest.raises(ModelConfigError, match="installed but failed"):
            _create_model_via_init("model", "openai", {})

        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda _name: (_ for _ in ()).throw(ValueError("bad module")),
        )
        with pytest.raises(ModelConfigError, match="Missing package"):
            _create_model_via_init("model", "openai", {})

        monkeypatch.setattr(
            chat_models,
            "init_chat_model",
            lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad config")),
        )
        with pytest.raises(ModelConfigError, match="Invalid model configuration"):
            _create_model_via_init("model", "", {})

        monkeypatch.setattr(
            chat_models,
            "init_chat_model",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("auth failed")),
        )
        with pytest.raises(ModelConfigError, match="Failed to initialize model"):
            _create_model_via_init("model", "custom", {})

        sentinel = object()
        monkeypatch.setattr(
            chat_models, "init_chat_model", lambda *args, **kwargs: sentinel
        )
        assert _create_model_via_init("model", "", {}) is sentinel

    def test_deepseek_helpers_and_profile_override_failure(self):
        """DeepSeek parameter helpers and profile override errors are deterministic."""
        defaults = config_mod._apply_deepseek_thinking_defaults({"extra_body": {}})
        assert defaults["reasoning_effort"] == "high"
        assert defaults["extra_body"]["thinking"]["type"] == "enabled"
        assert config_mod._sanitize_deepseek_thinking_params(
            {"reasoning_effort": "high", "extra_body": "bad"}
        ) == {"reasoning_effort": "high", "extra_body": "bad"}
        assert config_mod._sanitize_deepseek_thinking_params(
            {"reasoning_effort": "high", "extra_body": {}}
        ) == {"reasoning_effort": "high", "extra_body": {}}

        class RejectingProfile:
            @property
            def profile(self):
                return {}

            @profile.setter
            def profile(self, value):
                raise AttributeError("read-only")

        config_mod._apply_profile_overrides(
            cast(Any, RejectingProfile()),
            {"max_input_tokens": 10},
            "model",
            label="test",
        )
        with pytest.raises(ModelConfigError, match="Could not apply test"):
            config_mod._apply_profile_overrides(
                cast(Any, RejectingProfile()),
                {"max_input_tokens": 10},
                "model",
                label="test",
                raise_on_failure=True,
            )

    def test_validate_model_capabilities_warnings_and_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Capability validation warns on weak profiles and exits on no tools."""
        from invincat_cli.config import validate_model_capabilities

        printed: list[str] = []
        monkeypatch.setattr(
            config_mod,
            "_get_console",
            lambda: SimpleNamespace(print=lambda *args: printed.append(" ".join(args))),
        )

        validate_model_capabilities(
            cast(Any, SimpleNamespace(profile=None)), "no-profile"
        )
        assert any("No capability profile" in line for line in printed)

        validate_model_capabilities(
            cast(Any, SimpleNamespace(profile="unknown")), "non-dict"
        )

        validate_model_capabilities(
            cast(
                Any,
                SimpleNamespace(
                    profile={"tool_calling": True, "max_input_tokens": 4096}
                ),
            ),
            "small",
        )
        assert any("limited context" in line for line in printed)

        with pytest.raises(SystemExit):
            validate_model_capabilities(  # type: ignore[arg-type]
                cast(Any, SimpleNamespace(profile={"tool_calling": False})),
                "no-tools",
            )

    def test_register_model_merges_extra_params(self):
        """Registering a model can persist nested constructor params."""
        from invincat_cli.model_config import register_provider_model

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"

            assert register_provider_model(
                "openai",
                "deepseek-chat",
                api_key_env="DEEPSEEK_API_KEY",
                base_url="https://api.deepseek.com/v1",
                max_input_tokens=64000,
                extra_params={
                    "reasoning_effort": "medium",
                    "extra_body": {
                        "thinking": {
                            "type": "disabled",
                        },
                    },
                },
                config_path=config_path,
            )

            data = tomllib.loads(config_path.read_text())
            model_params = data["models"]["providers"]["openai"]["params"][
                "deepseek-chat"
            ]

            assert model_params["api_key_env"] == "DEEPSEEK_API_KEY"
            assert model_params["base_url"] == "https://api.deepseek.com/v1"
            assert model_params["reasoning_effort"] == "medium"
            assert model_params["extra_body"]["thinking"]["type"] == "disabled"
            assert (
                data["models"]["providers"]["openai"]["profile"]["deepseek-chat"][
                    "max_input_tokens"
                ]
                == 64000
            )

    def test_model_config_returns_isolated_nested_overrides(self):
        """Model params/profile reads should not share nested mutable objects."""
        config = ModelConfig(
            providers={
                "openai": {
                    "models": ["deepseek-chat"],
                    "params": {
                        "deepseek-chat": {
                            "extra_body": {
                                "thinking": {
                                    "type": "enabled",
                                },
                            },
                        },
                    },
                    "profile": {
                        "deepseek-chat": {
                            "metadata": {
                                "owner": "primary",
                            },
                        },
                    },
                },
            },
        )

        first_params = config.get_kwargs("openai", model_name="deepseek-chat")
        second_params = config.get_kwargs("openai", model_name="deepseek-chat")
        first_params["extra_body"]["thinking"]["type"] = "disabled"

        assert second_params["extra_body"]["thinking"]["type"] == "enabled"

        first_profile = config.get_profile_overrides(
            "openai", model_name="deepseek-chat"
        )
        second_profile = config.get_profile_overrides(
            "openai", model_name="deepseek-chat"
        )
        first_profile["metadata"]["owner"] = "memory"

        assert second_profile["metadata"]["owner"] == "primary"

    def test_target_model_params_are_isolated_by_target(self):
        """Primary and memory target params can differ for the same model."""
        from invincat_cli.model_config import (
            get_target_model_params,
            save_target_model_params,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"

            assert save_target_model_params(
                "primary",
                "openai:deepseek-chat",
                {
                    "reasoning_effort": "high",
                    "extra_body": {"thinking": {"type": "enabled"}},
                },
                config_path=config_path,
            )
            assert save_target_model_params(
                "memory",
                "openai:deepseek-chat",
                {
                    "reasoning_effort": "low",
                    "extra_body": {"thinking": {"type": "disabled"}},
                },
                config_path=config_path,
            )

            primary = get_target_model_params(
                "primary", "openai:deepseek-chat", config_path=config_path
            )
            memory = get_target_model_params(
                "memory", "openai:deepseek-chat", config_path=config_path
            )

            assert primary["reasoning_effort"] == "high"
            assert primary["extra_body"]["thinking"]["type"] == "enabled"
            assert memory["reasoning_effort"] == "low"
            assert memory["extra_body"]["thinking"]["type"] == "disabled"

            primary["extra_body"]["thinking"]["type"] = "disabled"

            assert (
                get_target_model_params(
                    "primary", "openai:deepseek-chat", config_path=config_path
                )["extra_body"]["thinking"]["type"]
                == "enabled"
            )

    def test_lazy_singletons_and_module_getattr(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Lazy module globals are cached and reset without eager initialization."""
        monkeypatch.delitem(config_mod.__dict__, "settings", raising=False)
        monkeypatch.delitem(config_mod.__dict__, "console", raising=False)
        monkeypatch.setattr(config_mod, "_ensure_bootstrap", lambda: None)
        monkeypatch.setattr(config_mod, "_bootstrap_start_path", tmp_path)

        sentinel_settings = Settings(
            openai_api_key=None,
            anthropic_api_key=None,
            google_api_key=None,
            nvidia_api_key=None,
            tavily_api_key=None,
            google_cloud_project=None,
            deepagents_langchain_project=None,
            user_langchain_project=None,
        )
        monkeypatch.setattr(
            config_mod.Settings,
            "from_environment",
            staticmethod(lambda *, start_path=None: sentinel_settings),
        )

        assert config_mod.__getattr__("settings") is sentinel_settings
        assert config_mod._get_settings() is sentinel_settings
        console = config_mod.__getattr__("console")
        assert config_mod._get_console() is console

        config_mod.reset_settings_cache()
        assert "settings" not in config_mod.__dict__
        with pytest.raises(AttributeError, match="has no attribute"):
            config_mod.__getattr__("missing")

    def test_lazy_singletons_lock_recheck_and_settings_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        settings_sentinel = object()
        console_sentinel = object()
        original_settings = config_mod.__dict__.get("settings", settings_sentinel)
        original_console = config_mod.__dict__.get("console", console_sentinel)
        config_mod.__dict__.pop("settings", None)
        config_mod.__dict__.pop("console", None)
        monkeypatch.setattr(config_mod, "_ensure_bootstrap", lambda: None)
        monkeypatch.setattr(config_mod, "_bootstrap_start_path", tmp_path)

        try:

            class PopulateConsoleLock:
                def __enter__(self):
                    config_mod.__dict__["console"] = "console-in-lock"

                def __exit__(self, *_args: object) -> None:
                    return None

            monkeypatch.setattr(config_mod, "_singleton_lock", PopulateConsoleLock())
            assert config_mod._get_console() == "console-in-lock"

            config_mod.__dict__.pop("console", None)
            config_mod.__dict__.pop("settings", None)

            class PopulateSettingsLock:
                def __enter__(self):
                    config_mod.__dict__["settings"] = "settings-in-lock"

                def __exit__(self, *_args: object) -> None:
                    return None

            monkeypatch.setattr(config_mod, "_singleton_lock", PopulateSettingsLock())
            assert config_mod._get_settings() == "settings-in-lock"

            config_mod.__dict__.pop("settings", None)
            monkeypatch.setattr(
                config_mod, "_singleton_lock", config_mod.threading.Lock()
            )
            monkeypatch.setattr(
                config_mod.Settings,
                "from_environment",
                staticmethod(
                    lambda *, start_path=None: (_ for _ in ()).throw(
                        RuntimeError("bad env")
                    )
                ),
            )
            with pytest.raises(RuntimeError, match="bad env"):
                config_mod._get_settings()
        finally:
            config_mod.__dict__.pop("settings", None)
            config_mod.__dict__.pop("console", None)
            if original_settings is not settings_sentinel:
                config_mod.__dict__["settings"] = original_settings
            if original_console is not console_sentinel:
                config_mod.__dict__["console"] = original_console


if __name__ == "__main__":
    pytest.main([__file__])
