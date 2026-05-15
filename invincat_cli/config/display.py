"""Display glyph and editable-install helpers for config."""

from __future__ import annotations


def _resolve_editable_info() -> tuple[bool, str | None]:
    """Parse PEP 610 `direct_url.json` once and cache both results."""
    from invincat_cli import config as _config

    if _config._editable_cache is not None:  # noqa: SLF001
        return _config._editable_cache  # noqa: SLF001

    editable = False
    path: str | None = None

    try:
        dist = _config.distribution("deepagents-cli")
        raw = dist.read_text("direct_url.json")
        if raw:
            data = _config.json.loads(raw)
            editable = data.get("dir_info", {}).get("editable", False)
            if editable:
                url = data.get("url", "")
                if url.startswith("file://"):
                    path = _config.unquote(_config.urlparse(url).path)
                    home = str(_config.Path.home())
                    if path.startswith(home):
                        path = "~" + path[len(home) :]
    except (
        _config.PackageNotFoundError,
        FileNotFoundError,
        _config.json.JSONDecodeError,
        TypeError,
    ):
        _config.logger.debug(
            "Failed to read editable install info from PEP 610 metadata",
            exc_info=True,
        )

    _config._editable_cache = (editable, path)  # noqa: SLF001
    return _config._editable_cache  # noqa: SLF001


def _is_editable_install() -> bool:
    """Check if deepagents-cli is installed in editable mode."""
    from invincat_cli import config as _config

    return _config._resolve_editable_info()[0]  # noqa: SLF001


def _get_editable_install_path() -> str | None:
    """Return the `~`-contracted source directory for an editable install."""
    from invincat_cli import config as _config

    return _config._resolve_editable_info()[1]  # noqa: SLF001


def _detect_charset_mode():
    """Auto-detect terminal charset capabilities."""
    from invincat_cli import config as _config

    return _config.detect_charset_mode()


def get_glyphs():
    """Get the glyph set for the current charset mode."""
    from invincat_cli import config as _config

    if _config._glyphs_cache is not None:  # noqa: SLF001
        return _config._glyphs_cache  # noqa: SLF001

    mode = _config._detect_charset_mode()  # noqa: SLF001
    _config._glyphs_cache = (  # noqa: SLF001
        _config.ASCII_GLYPHS
        if mode == _config.CharsetMode.ASCII
        else _config.UNICODE_GLYPHS
    )
    return _config._glyphs_cache  # noqa: SLF001


def reset_glyphs_cache() -> None:
    """Reset the glyph cache."""
    from invincat_cli import config as _config

    _config._glyphs_cache = None  # noqa: SLF001


def is_ascii_mode() -> bool:
    """Check whether the terminal is in ASCII charset mode."""
    from invincat_cli import config as _config

    return _config._detect_charset_mode() == _config.CharsetMode.ASCII  # noqa: SLF001


def get_banner() -> str:
    """Get the appropriate banner for the current charset mode."""
    from invincat_cli import config as _config

    return _config.render_banner(  # noqa: SLF001
        _config.__version__,
        editable=_config._is_editable_install(),  # noqa: SLF001
    )
