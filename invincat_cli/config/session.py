"""Small shared session state for CLI runtime."""

from __future__ import annotations


class SessionState:
    """Mutable session state shared across app, adapter, and agent."""

    def __init__(self, auto_approve: bool = False, no_splash: bool = False) -> None:
        self.auto_approve = auto_approve
        self.no_splash = no_splash
        self.exit_hint_until: float | None = None
        self.exit_hint_handle = None
        from invincat_cli.sessions import generate_thread_id

        self.thread_id = generate_thread_id()

    def toggle_auto_approve(self) -> bool:
        """Toggle auto-approve and return the new state."""
        self.auto_approve = not self.auto_approve
        return self.auto_approve
