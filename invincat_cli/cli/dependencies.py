"""CLI dependency and optional tool checks."""

from __future__ import annotations

from pathlib import Path

_RIPGREP_URL = "https://github.com/BurntSushi/ripgrep#installation"

_RIPGREP_SUPPRESS_HINT = (
    "To suppress, add to ~/.invincat/config.toml:\n"
    "\\[warnings]\n"
    'suppress = \\["ripgrep"]'
)


def check_cli_dependencies() -> None:
    """Check if CLI optional dependencies are installed."""
    from invincat_cli import main as _main

    missing = []

    if _main.importlib.util.find_spec("requests") is None:
        missing.append("requests")
    if _main.importlib.util.find_spec("dotenv") is None:
        missing.append("python-dotenv")
    if _main.importlib.util.find_spec("tavily") is None:
        missing.append("tavily-python")
    if _main.importlib.util.find_spec("textual") is None:
        missing.append("textual")

    if missing:
        print("\nMissing required CLI dependencies!")  # noqa: T201
        print("\nThe following packages are required to use the invincat CLI:")  # noqa: T201
        for pkg in missing:
            print(f"  - {pkg}")  # noqa: T201
        print("\nPlease install them with:")  # noqa: T201
        print("  pip install invincat-cli")  # noqa: T201
        print("\nOr install all dependencies:")  # noqa: T201
        print("  pip install 'invincat-cli'")  # noqa: T201
        _main.sys.exit(1)


def _ripgrep_install_hint() -> str:
    """Return a platform-specific install command for ripgrep."""
    from invincat_cli import main as _main

    plat = _main.sys.platform
    if plat == "darwin":
        if _main.shutil.which("brew"):
            return "brew install ripgrep"
        if _main.shutil.which("port"):
            return "sudo port install ripgrep"
    elif plat == "linux":
        if _main.shutil.which("apt-get"):
            return "sudo apt-get install ripgrep"
        if _main.shutil.which("dnf"):
            return "sudo dnf install ripgrep"
        if _main.shutil.which("pacman"):
            return "sudo pacman -S ripgrep"
        if _main.shutil.which("zypper"):
            return "sudo zypper install ripgrep"
        if _main.shutil.which("apk"):
            return "sudo apk add ripgrep"
        if _main.shutil.which("nix-env"):
            return "nix-env -iA nixpkgs.ripgrep"
    elif plat == "win32":
        if _main.shutil.which("choco"):
            return "choco install ripgrep"
        if _main.shutil.which("scoop"):
            return "scoop install ripgrep"
        if _main.shutil.which("winget"):
            return "winget install BurntSushi.ripgrep"
    if _main.shutil.which("cargo"):
        return "cargo install ripgrep"
    if _main.shutil.which("conda"):
        return "conda install -c conda-forge ripgrep"
    return _main._RIPGREP_URL  # noqa: SLF001


def check_optional_tools(*, config_path: Path | None = None) -> list[str]:
    """Check for recommended external tools and return missing tool names."""
    from invincat_cli import main as _main
    from invincat_cli.model_config import is_warning_suppressed

    missing: list[str] = []
    if _main.shutil.which("rg") is None and not is_warning_suppressed(
        "ripgrep", config_path
    ):
        missing.append("ripgrep")
    return missing


def format_tool_warning_tui(tool: str) -> str:
    """Format a missing-tool warning for the TUI toast."""
    from invincat_cli import main as _main

    if tool == "ripgrep":
        hint = _main._ripgrep_install_hint()  # noqa: SLF001
        return (
            "ripgrep is not installed; the grep tool will use a slower fallback.\n"
            f"\nInstall: {hint}\n\n"
            f"{_main._RIPGREP_SUPPRESS_HINT}"  # noqa: SLF001
        )
    return f"{tool} is not installed."


def format_tool_warning_cli(tool: str) -> str:
    """Format a missing-tool warning for non-interactive console output."""
    from invincat_cli import main as _main

    if tool == "ripgrep":
        hint = _main._ripgrep_install_hint()  # noqa: SLF001
        if hint.startswith("http"):
            hint = f"[link={hint}]{hint}[/link]"
        return (
            "ripgrep is not installed; the grep tool will use a slower fallback.\n"
            f"Install: {hint}\n\n"
            f"{_main._RIPGREP_SUPPRESS_HINT}\n"  # noqa: SLF001
        )
    return f"{tool} is not installed."
