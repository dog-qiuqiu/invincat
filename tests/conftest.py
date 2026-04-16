"""Pytest configuration and fixtures."""

import os
import tempfile
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_env() -> Generator[dict, None, None]:
    """Provide a clean test environment."""
    original_env = os.environ.copy()
    
    # Set up test environment
    test_env_vars = {
        "OPENAI_API_KEY": "test-openai-key",
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        "GOOGLE_API_KEY": "test-google-key",
        "TAVILY_API_KEY": "test-tavily-key",
        "DEEPAGENTS_CLI_SHELL_ALLOW_LIST": "ls,cd,pwd,echo",
        "LC_ALL": "en_US.UTF-8",  # Ensure UTF-8 for tests
    }
    
    os.environ.clear()
    os.environ.update(test_env_vars)
    
    yield test_env_vars
    
    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def mock_settings():
    """Mock settings for tests."""
    from unittest.mock import Mock
    from invincat_cli.config import settings as actual_settings
    
    # Create mock settings
    mock_settings = Mock()
    mock_settings.openai_api_key = "test-openai-key"
    mock_settings.anthropic_api_key = "test-anthropic-key"
    mock_settings.google_api_key = "test-google-key"
    mock_settings.tavily_api_key = "test-tavily-key"
    mock_settings.shell_allow_list = ["ls", "cd", "pwd", "echo"]
    mock_settings.project_root = None
    
    # Mock methods
    mock_settings.has_api_key.return_value = True
    mock_settings.get_api_key.side_effect = lambda provider: {
        "openai": "test-openai-key",
        "anthropic": "test-anthropic-key",
        "google": "test-google-key",
        "tavily": "test-tavily-key",
    }.get(provider)
    
    return mock_settings


@pytest.fixture
def sample_file_content() -> str:
    """Sample file content for tests."""
    return """# Sample Python file
def hello(name: str) -> str:
    \"\"\"Return a greeting.\"\"\"
    return f"Hello, {name}!"

def add(a: int, b: int) -> int:
    \"\"\"Add two numbers.\"\"\"
    return a + b

if __name__ == "__main__":
    print(hello("World"))
    print(add(1, 2))
"""