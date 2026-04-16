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
            # Mock config directory
            with patch("invincat_cli.config.settings.config_dir", Path(tmpdir)):
                settings = Settings.from_environment()
                agent_dir = settings.get_agent_dir("test-agent")
                
                expected = Path(tmpdir) / "agents" / "test-agent"
                assert agent_dir == expected
                assert "test-agent" in str(agent_dir)

    def test_get_model_config_path(self):
        """Test getting model configuration path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("invincat_cli.config.settings.config_dir", Path(tmpdir)):
                settings = Settings.from_environment()
                config_path = settings.get_model_config_path()
                
                expected = Path(tmpdir) / "models.json"
                assert config_path == expected

    def test_has_api_key(self):
        """Test checking for API keys."""
        settings = Settings.from_environment()
        
        # Initially no keys set
        assert not settings.has_api_key("openai")
        assert not settings.has_api_key("anthropic")
        
        # Set keys and test
        settings.openai_api_key = "test-key"
        settings.anthropic_api_key = "test-key"
        
        assert settings.has_api_key("openai")
        assert settings.has_api_key("anthropic")
        assert not settings.has_api_key("unknown")

    def test_get_api_key(self):
        """Test getting API keys."""
        settings = Settings.from_environment()
        settings.openai_api_key = "openai-test"
        settings.anthropic_api_key = "anthropic-test"
        
        assert settings.get_api_key("openai") == "openai-test"
        assert settings.get_api_key("anthropic") == "anthropic-test"
        assert settings.get_api_key("unknown") is None


class TestUtilityFunctions:
    """Tests for utility functions."""

    def test_is_ascii_mode(self):
        """Test ASCII mode detection."""
        # Test with ASCII locale
        with patch.dict(os.environ, {"LC_ALL": "C", "LANG": "C"}):
            assert is_ascii_mode() is True
        
        # Test with UTF-8 locale
        with patch.dict(os.environ, {"LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"}):
            assert is_ascii_mode() is False
        
        # Test with no locale (defaults to False)
        with patch.dict(os.environ, {}, clear=True):
            assert is_ascii_mode() is False

    def test_is_shell_command_allowed(self):
        """Test shell command allow list checking."""
        # Test with no allow list (all commands require approval)
        assert is_shell_command_allowed("ls") is False
        assert is_shell_command_allowed("cd /tmp") is False
        
        # Test with allow list
        with patch("invincat_cli.config.settings.shell_allow_list", ["ls", "cd", "pwd"]):
            assert is_shell_command_allowed("ls") is True
            assert is_shell_command_allowed("cd /tmp") is True
            assert is_shell_command_allowed("rm -rf /") is False
            assert is_shell_command_allowed("ls -la") is True  # ls with args
            
            # Test command with path
            assert is_shell_command_allowed("/bin/ls") is True
            assert is_shell_command_allowed("./script.sh") is False

    def test_build_stream_config(self):
        """Test building stream configuration."""
        config = build_stream_config()
        
        assert "callbacks" in config
        assert "configurable" in config
        assert "model_name" in config["configurable"]
        assert "model_provider" in config["configurable"]
        
        # Test with custom model
        custom_config = build_stream_config(model_name="gpt-4", model_provider="openai")
        assert custom_config["configurable"]["model_name"] == "gpt-4"
        assert custom_config["configurable"]["model_provider"] == "openai"


class TestModelCreation:
    """Tests for model creation functions."""

    @patch("invincat_cli.config.ChatOpenAI")
    def test_create_model_openai(self, mock_chat_openai):
        """Test creating OpenAI model."""
        from invincat_cli.config import create_model
        
        mock_instance = Mock()
        mock_chat_openai.return_value = mock_instance
        
        # Mock settings
        with patch("invincat_cli.config.settings.openai_api_key", "test-key"):
            model = create_model("gpt-4", "openai")
            
            assert model == mock_instance
            mock_chat_openai.assert_called_once()
            call_kwargs = mock_chat_openai.call_args[1]
            assert call_kwargs["model"] == "gpt-4"
            assert call_kwargs["api_key"] == "test-key"

    @patch("invincat_cli.config.ChatAnthropic")
    def test_create_model_anthropic(self, mock_chat_anthropic):
        """Test creating Anthropic model."""
        from invincat_cli.config import create_model
        
        mock_instance = Mock()
        mock_chat_anthropic.return_value = mock_instance
        
        # Mock settings
        with patch("invincat_cli.config.settings.anthropic_api_key", "test-key"):
            model = create_model("claude-3-opus", "anthropic")
            
            assert model == mock_instance
            mock_chat_anthropic.assert_called_once()
            call_kwargs = mock_chat_anthropic.call_args[1]
            assert "claude-3-opus" in call_kwargs["model"]
            assert call_kwargs["api_key"] == "test-key"

    def test_create_model_unknown_provider(self):
        """Test creating model with unknown provider."""
        from invincat_cli.config import create_model
        
        with pytest.raises(ValueError, match="Unsupported provider"):
            create_model("test-model", "unknown-provider")


if __name__ == "__main__":
    pytest.main([__file__])