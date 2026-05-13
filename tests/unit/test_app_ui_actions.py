from __future__ import annotations

from pathlib import Path

from invincat_cli.app_runtime.ui_actions import (
    ChatScrollSnapshot,
    capture_chat_scroll_state,
    parse_selector_model_spec,
    primary_model_spec,
    resolve_memory_store_paths,
    resolve_model_selector_state,
    restore_chat_scroll_state,
    should_defer_modal_action,
)


class DummyChatScroll:
    def __init__(self, *, y: float, anchored: bool) -> None:
        self.scroll_y = y
        self.is_anchored = anchored
        self.released = False
        self.restored_y: float | None = None
        self.animated: bool | None = None
        self.anchored = False

    def release_anchor(self) -> None:
        self.released = True

    def scroll_to(self, *, y: float, animate: bool = False) -> None:
        self.restored_y = y
        self.animated = animate

    def anchor(self) -> None:
        self.anchored = True


def test_capture_and_restore_chat_scroll_state_when_anchored() -> None:
    chat = DummyChatScroll(y=42.5, anchored=True)

    snapshot = capture_chat_scroll_state(chat)
    restore_chat_scroll_state(chat, snapshot)

    assert snapshot == ChatScrollSnapshot(y=42.5, was_anchored=True)
    assert chat.released is True
    assert chat.restored_y == 42.5
    assert chat.animated is False
    assert chat.anchored is True


def test_restore_chat_scroll_state_leaves_unanchored_chat_unanchored() -> None:
    chat = DummyChatScroll(y=7, anchored=False)

    restore_chat_scroll_state(chat, ChatScrollSnapshot(y=3, was_anchored=False))

    assert chat.restored_y == 3
    assert chat.anchored is False


def test_should_defer_modal_action() -> None:
    assert should_defer_modal_action(
        agent_running=True,
        shell_running=False,
        connecting=False,
    )
    assert should_defer_modal_action(
        agent_running=False,
        shell_running=True,
        connecting=False,
    )
    assert should_defer_modal_action(
        agent_running=False,
        shell_running=False,
        connecting=True,
    )
    assert not should_defer_modal_action(
        agent_running=False,
        shell_running=False,
        connecting=False,
    )


def test_parse_selector_model_spec() -> None:
    assert parse_selector_model_spec(None) == (None, None)
    assert parse_selector_model_spec("") == (None, None)
    assert parse_selector_model_spec("openai:gpt-5") == ("openai", "gpt-5")
    assert parse_selector_model_spec("custom-model") == (None, "custom-model")


def test_resolve_model_selector_state_memory_follows_primary() -> None:
    state = resolve_model_selector_state(
        settings_model_provider="openai",
        settings_model_name="gpt-5",
        memory_model_override=None,
    )

    assert primary_model_spec("openai", "gpt-5") == "openai:gpt-5"
    assert state.current_provider == "openai"
    assert state.current_model == "gpt-5"
    assert state.memory_provider == "openai"
    assert state.memory_model == "gpt-5"


def test_resolve_model_selector_state_uses_memory_override() -> None:
    state = resolve_model_selector_state(
        settings_model_provider="openai",
        settings_model_name="gpt-5",
        memory_model_override="anthropic:claude",
    )

    assert state.current_provider == "openai"
    assert state.current_model == "gpt-5"
    assert state.memory_provider == "anthropic"
    assert state.memory_model == "claude"


def test_resolve_memory_store_paths_uses_project_root(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    project_root = tmp_path / "repo"
    cwd = project_root / "src"
    cwd.mkdir(parents=True)

    paths = resolve_memory_store_paths(
        cwd=cwd,
        assistant_id="assistant",
        get_agent_dir=lambda assistant_id: user_root / assistant_id,
        project_root_finder=lambda _cwd: project_root,
    )

    assert paths == {
        "user": str((user_root / "assistant" / "memory_user.json").resolve()),
        "project": str(
            (project_root / ".invincat" / "memory_project.json").resolve()
        ),
    }


def test_resolve_memory_store_paths_falls_back_to_cwd(tmp_path: Path) -> None:
    paths = resolve_memory_store_paths(
        cwd=tmp_path,
        assistant_id=None,
        get_agent_dir=lambda assistant_id: tmp_path / "agents" / assistant_id,
        project_root_finder=lambda _cwd: None,
    )

    assert paths == {
        "user": str((tmp_path / "agents" / "agent" / "memory_user.json").resolve()),
        "project": str((tmp_path / ".invincat" / "memory_project.json").resolve()),
    }
