"""Unit tests for configuration module."""

import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from invincat_cli.config import (
    Settings,
    build_stream_config,
    is_ascii_mode,
    is_shell_command_allowed,
)
from invincat_cli.model_config import ModelConfigError


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

    def test_get_user_agent_md_path(self):
        """Test getting user-level AGENTS.md path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("invincat_cli.config.Path.home", return_value=Path(tmpdir)):
                md_path = Settings.get_user_agent_md_path("test-agent")

                expected = Path(tmpdir) / ".invincat" / "test-agent" / "AGENTS.md"
                assert md_path == expected

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


class TestUtilityFunctions:
    """Tests for utility functions."""

    def test_is_ascii_mode(self):
        """Test ASCII mode detection."""
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

    def test_is_shell_command_allowed(self):
        """Test shell command allow list checking."""
        # Test with no allow list (all commands require approval)
        assert is_shell_command_allowed("ls", None) is False
        assert is_shell_command_allowed("cd /tmp", None) is False

        # Test with allow list
        allow_list = ["ls", "cd", "pwd", "/bin/ls"]
        assert is_shell_command_allowed("ls", allow_list) is True
        assert is_shell_command_allowed("cd /tmp", allow_list) is True
        assert is_shell_command_allowed("rm -rf /", allow_list) is False
        assert is_shell_command_allowed("ls -la", allow_list) is True  # ls with args
        assert is_shell_command_allowed("/bin/ls", allow_list) is True
        assert is_shell_command_allowed("./script.sh", allow_list) is False

    def test_build_stream_config(self):
        """Test building stream configuration."""
        config = build_stream_config("thread-1", "assistant-1")

        assert "configurable" in config
        assert "metadata" in config
        assert config["configurable"]["thread_id"] == "thread-1"
        assert config["metadata"]["assistant_id"] == "assistant-1"


class TestModelCreation:
    """Tests for model creation functions."""

    @patch("invincat_cli.config._create_model_via_init")
    @patch("invincat_cli.model_config.ModelConfig.load")
    @patch("invincat_cli.config._get_provider_kwargs", return_value={"api_key": "test-key"})
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


if __name__ == "__main__":
    pytest.main([__file__])
