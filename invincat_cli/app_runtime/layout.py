"""Textual app layout and theme registration helpers."""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.theme import Theme

from invincat_cli import theme
from invincat_cli.widgets.chat_input import ChatInput
from invincat_cli.widgets.status import StatusBar
from invincat_cli.widgets.welcome import WelcomeBanner

logger = logging.getLogger(__name__)


def get_theme_variable_defaults(app: Any) -> dict[str, str]:  # noqa: ANN401
    """Return custom CSS variable defaults for the current theme."""
    colors = theme.get_theme_colors(app)
    return theme.get_css_variable_defaults(colors=colors)


def compose_layout(app: Any) -> ComposeResult:  # noqa: ANN401
    """Yield UI components for the main chat area and status bar."""
    with VerticalScroll(id="chat"):
        yield WelcomeBanner(
            thread_id=app._lc_thread_id,
            mcp_tool_count=app._mcp_tool_count,
            connecting=app._connecting,
            resuming=app._resume_thread_intent is not None,
            local_server=app._server_kwargs is not None,
            id="welcome-banner",
        )
        yield Container(id="messages")
    with Container(id="bottom-app-container"):
        yield ChatInput(
            cwd=app._cwd,
            image_tracker=app._image_tracker,
            id="input-area",
        )

    yield StatusBar(cwd=app._cwd, id="status-bar")


def register_custom_themes(app: Any) -> None:  # noqa: ANN401
    """Register all custom themes (built-in LC plus user-defined) with Textual."""
    for name, entry in theme.ThemeEntry.REGISTRY.items():
        if entry.custom:
            c = entry.colors
            try:
                app.register_theme(
                    Theme(
                        name=name,
                        primary=c.primary,
                        secondary=c.secondary,
                        accent=c.accent,
                        foreground=c.foreground,
                        background=c.background,
                        surface=c.surface,
                        panel=c.panel,
                        warning=c.warning,
                        error=c.error,
                        success=c.success,
                        dark=entry.dark,
                        variables={
                            "footer-key-foreground": c.primary,
                        },
                    )
                )
            except Exception:
                logger.warning(
                    "Failed to register theme '%s'; skipping",
                    name,
                    exc_info=True,
                )
