"""WeCom runtime helpers for the Textual app."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from invincat_cli.wecom.session import WeComMessageResponder

DEFAULT_WECOM_WS_URL = "wss://openws.work.weixin.qq.com"
WeComBotCommandKind = Literal["start", "stop", "status", "usage"]


@dataclass(frozen=True, slots=True)
class WeComBotConfig:
    """Environment-backed WeCom bot bridge config."""

    bot_id: str
    secret: str
    ws_url: str = DEFAULT_WECOM_WS_URL

    @property
    def is_complete(self) -> bool:
        return bool(self.bot_id and self.secret)


@dataclass(frozen=True, slots=True)
class WeComBotCommandDecision:
    """Decision for handling a `/wecombot-*` command."""

    kind: WeComBotCommandKind
    message: str
    should_enable_auto_approve: bool = False
    should_start_bridge: bool = False
    should_stop_bridge: bool = False


@dataclass(frozen=True, slots=True)
class WeComBridgeAvailability:
    """Bridge availability decision for outbound operations."""

    online: bool
    error_message: str | None = None


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


def wecom_bot_already_running_message() -> str:
    """Build the `/wecombot-start` already-running message."""
    return "WeCom bot is already running."


def wecom_bot_stopped_message() -> str:
    """Build the `/wecombot-stop` success message."""
    return "WeCom bot bridge stopped."


def resolve_wecom_bot_command_decision(
    *,
    action: str,
    running: bool,
    auto_approve_enabled: bool,
) -> WeComBotCommandDecision:
    """Resolve side effects and message for a `/wecombot-*` command."""
    if action == "start":
        if running:
            return WeComBotCommandDecision(
                kind="status",
                message=wecom_bot_already_running_message(),
            )
        return WeComBotCommandDecision(
            kind="start",
            message=wecom_bot_started_message(
                auto_approve_was_enabled=auto_approve_enabled,
            ),
            should_enable_auto_approve=True,
            should_start_bridge=True,
        )
    if action == "stop":
        return WeComBotCommandDecision(
            kind="stop",
            message=wecom_bot_stopped_message(),
            should_stop_bridge=True,
        )
    if action == "status":
        return WeComBotCommandDecision(
            kind="status",
            message=wecom_bot_status_message(running=running),
        )
    return WeComBotCommandDecision(kind="usage", message=wecom_bot_usage_message())


def wecom_bridge_is_online(bridge: object | None) -> bool:
    """Return whether a bridge object is currently available."""
    return bridge is not None


def should_clear_wecom_bridge(*, current_bridge: object | None, bridge: object) -> bool:
    """Return whether bridge shutdown should clear the active bridge pointer."""
    return current_bridge is bridge


def wecom_bridge_offline_error() -> RuntimeError:
    """Build the standard WeCom bridge offline exception."""
    return RuntimeError("WeCom connection is offline")


def wecom_bridge_offline_message() -> str:
    """Build the standard WeCom bridge offline text."""
    return "WeCom bridge is offline"


def resolve_wecom_bridge_availability(bridge: object | None) -> WeComBridgeAvailability:
    """Resolve whether outbound WeCom operations can use the bridge."""
    if wecom_bridge_is_online(bridge):
        return WeComBridgeAvailability(online=True)
    return WeComBridgeAvailability(
        online=False,
        error_message=wecom_bridge_offline_message(),
    )


def create_wecom_message_responder(
    *,
    enqueue: Callable[[dict[str, Any]], None],
    flush: Callable[[], Awaitable[bool]],
    build_agent_input: Callable[[dict[str, Any]], Awaitable[str]],
    run_turn: Callable[
        [str, dict[str, Any], Callable[[str], Awaitable[None]]],
        Awaitable[str],
    ],
    report_error: Callable[[str], Awaitable[None]],
) -> WeComMessageResponder:
    """Create the responder used for one inbound WeCom message."""
    return WeComMessageResponder(
        enqueue=enqueue,
        flush=flush,
        build_agent_input=build_agent_input,
        run_turn=run_turn,
        report_error=report_error,
    )


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
