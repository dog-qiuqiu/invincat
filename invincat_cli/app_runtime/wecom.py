"""WeCom runtime helpers for the Textual app."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Mapping


DEFAULT_WECOM_WS_URL = "wss://openws.work.weixin.qq.com"


@dataclass(frozen=True, slots=True)
class WeComBotConfig:
    """Environment-backed WeCom bot bridge config."""

    bot_id: str
    secret: str
    ws_url: str = DEFAULT_WECOM_WS_URL

    @property
    def is_complete(self) -> bool:
        return bool(self.bot_id and self.secret)


def load_wecom_bot_config(environ: Mapping[str, str]) -> WeComBotConfig:
    """Load WeCom bot config from environment variables."""
    return WeComBotConfig(
        bot_id=environ.get("WECOM_BOT_ID", "").strip(),
        secret=environ.get("WECOM_BOT_SECRET", "").strip(),
        ws_url=environ.get("WECOM_WS_URL", DEFAULT_WECOM_WS_URL).strip()
        or DEFAULT_WECOM_WS_URL,
    )


def wecom_bot_is_running(task: object | None) -> bool:
    """Return whether the bridge task is alive."""
    return bool(task is not None and not task.done())  # type: ignore[attr-defined]


def wecom_bot_started_message(*, auto_approve_was_enabled: bool) -> str:
    """Build the `/wecombot-start` success message."""
    message = "WeCom bot bridge started. Use /wecombot-stop to stop."
    if not auto_approve_was_enabled:
        message += (
            "\nAuto-approve mode enabled to prevent remote WeCom turns from "
            "blocking on local approvals."
        )
    return message


def wecom_bot_status_message(*, running: bool) -> str:
    """Build the `/wecombot-status` response."""
    return f"WeCom bot bridge status: {'running' if running else 'stopped'}"


def wecom_bot_usage_message() -> str:
    """Build the invalid `/wecombot-*` usage message."""
    return "Usage: /wecombot-start | /wecombot-status | /wecombot-stop"


def wecom_bot_missing_config_message() -> str:
    """Build the missing environment variables message."""
    return "WECOM_BOT_ID / WECOM_BOT_SECRET not set; cannot start /wecombot-start."


def wecom_turn_is_busy(
    *,
    connecting: bool,
    thread_switching: bool,
    model_switching: bool,
    agent_running: bool,
    shell_running: bool,
) -> bool:
    """Return whether a WeCom turn must wait for the local session."""
    return (
        connecting
        or thread_switching
        or model_switching
        or agent_running
        or shell_running
    )


class WeComTurnContext:
    """Manage temporary active inbound frame state for one WeCom turn."""

    def __init__(
        self,
        *,
        get_current_frame: Callable[[], dict[str, Any] | None],
        set_current_frame: Callable[[dict[str, Any] | None], None],
        inbound_frame: dict[str, Any],
    ) -> None:
        self._get_current_frame = get_current_frame
        self._set_current_frame = set_current_frame
        self._inbound_frame = inbound_frame
        self._previous_frame: dict[str, Any] | None = None

    def enter(self) -> None:
        self._previous_frame = self._get_current_frame()
        self._set_current_frame(self._inbound_frame)

    def exit(self) -> None:
        self._set_current_frame(self._previous_frame)
