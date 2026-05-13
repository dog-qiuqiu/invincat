"""Tests for WeCom runtime helpers used by the Textual app."""

from __future__ import annotations

from invincat_cli.app_runtime.wecom import (
    DEFAULT_WECOM_WS_URL,
    WeComTurnContext,
    create_wecom_message_responder,
    load_wecom_bot_config,
    should_clear_wecom_bridge,
    wecom_bot_already_running_message,
    wecom_bot_is_running,
    wecom_bot_missing_config_message,
    wecom_bot_started_message,
    wecom_bot_status_message,
    wecom_bot_stopped_message,
    wecom_bot_usage_message,
    wecom_bridge_is_online,
    wecom_bridge_offline_error,
    wecom_bridge_offline_message,
    wecom_turn_is_busy,
)


class _Task:
    def __init__(self, done: bool) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


def test_load_wecom_bot_config() -> None:
    config = load_wecom_bot_config(
        {
            "WECOM_BOT_ID": " bot ",
            "WECOM_BOT_SECRET": " secret ",
            "WECOM_WS_URL": " wss://example.test ",
        }
    )

    assert config.bot_id == "bot"
    assert config.secret == "secret"
    assert config.ws_url == "wss://example.test"
    assert config.is_complete is True


def test_load_wecom_bot_config_defaults_ws_url() -> None:
    config = load_wecom_bot_config(
        {
            "WECOM_BOT_ID": "bot",
            "WECOM_BOT_SECRET": "secret",
            "WECOM_WS_URL": "",
        }
    )

    assert config.ws_url == DEFAULT_WECOM_WS_URL
    assert config.is_complete is True
    assert load_wecom_bot_config({}).is_complete is False


def test_wecom_bot_is_running() -> None:
    assert wecom_bot_is_running(_Task(done=False)) is True
    assert wecom_bot_is_running(_Task(done=True)) is False
    assert wecom_bot_is_running(None) is False


def test_wecom_bot_messages() -> None:
    assert wecom_bot_already_running_message() == "WeCom bot is already running."
    assert wecom_bot_started_message(auto_approve_was_enabled=True) == (
        "WeCom bot bridge started. Use /wecombot-stop to stop."
    )
    assert "Auto-approve mode enabled" in wecom_bot_started_message(
        auto_approve_was_enabled=False
    )
    assert wecom_bot_status_message(running=True).endswith("running")
    assert wecom_bot_status_message(running=False).endswith("stopped")
    assert wecom_bot_stopped_message() == "WeCom bot bridge stopped."
    assert "/wecombot-start" in wecom_bot_usage_message()
    assert "WECOM_BOT_ID" in wecom_bot_missing_config_message()


def test_wecom_bridge_state_helpers() -> None:
    bridge = object()

    assert wecom_bridge_is_online(bridge) is True
    assert wecom_bridge_is_online(None) is False
    assert should_clear_wecom_bridge(current_bridge=bridge, bridge=bridge) is True
    assert should_clear_wecom_bridge(current_bridge=object(), bridge=bridge) is False
    assert str(wecom_bridge_offline_error()) == "WeCom connection is offline"
    assert wecom_bridge_offline_message() == "WeCom bridge is offline"


def test_wecom_turn_is_busy() -> None:
    assert wecom_turn_is_busy(
        connecting=False,
        thread_switching=False,
        model_switching=False,
        agent_running=False,
        shell_running=False,
    ) is False
    assert wecom_turn_is_busy(
        connecting=False,
        thread_switching=True,
        model_switching=False,
        agent_running=False,
        shell_running=False,
    ) is True


def test_wecom_turn_context_restores_previous_frame() -> None:
    current = {"frame": {"id": "previous"}}
    inbound = {"id": "inbound"}

    context = WeComTurnContext(
        get_current_frame=lambda: current["frame"],
        set_current_frame=lambda frame: current.__setitem__("frame", frame),
        inbound_frame=inbound,
    )

    context.enter()
    assert current["frame"] == inbound
    context.exit()
    assert current["frame"] == {"id": "previous"}


def test_create_wecom_message_responder() -> None:
    async def flush() -> bool:
        return True

    async def build_agent_input(_frame: dict) -> str:
        return "hello"

    async def run_turn(
        _text: str,
        _frame: dict,
        _on_content,  # noqa: ANN001
    ) -> str:
        return "answer"

    async def report_error(_message: str) -> None:
        return None

    responder = create_wecom_message_responder(
        enqueue=lambda _payload: None,
        flush=flush,
        build_agent_input=build_agent_input,
        run_turn=run_turn,
        report_error=report_error,
    )

    assert responder.__class__.__name__ == "WeComMessageResponder"
