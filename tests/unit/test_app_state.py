from __future__ import annotations

import invincat_cli.sessions as sessions
from invincat_cli.app_runtime import state
from invincat_cli.app_runtime.state import AppResult, TextualSessionState
from invincat_cli.core.session_stats import SessionStats


def test_new_thread_id_delegates_to_sessions(monkeypatch) -> None:
    monkeypatch.setattr(sessions, "generate_thread_id", lambda: "thread-generated")

    assert state.new_thread_id() == "thread-generated"


def test_textual_session_state_uses_existing_or_generated_thread(monkeypatch) -> None:
    generated = iter(["thread-1", "thread-2"])
    monkeypatch.setattr(state, "new_thread_id", lambda: next(generated))

    existing = TextualSessionState(auto_approve=True, thread_id="existing")
    fresh = TextualSessionState()

    assert existing.auto_approve is True
    assert existing.thread_id == "existing"
    assert existing.plan_mode is False
    assert existing.goal_mode is False
    assert existing.goal is None
    assert fresh.thread_id == "thread-1"
    assert fresh.reset_thread() == "thread-2"
    assert fresh.thread_id == "thread-2"
    assert fresh.goal_mode is False
    assert fresh.goal is None


def test_app_result_defaults_session_stats_and_update_state() -> None:
    result = AppResult(return_code=0, thread_id="thread-1")

    assert isinstance(result.session_stats, SessionStats)
    assert result.update_available == (False, None)
