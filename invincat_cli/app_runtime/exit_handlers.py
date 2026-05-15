"""App-bound exit cleanup handlers."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any


def prepare_exit(app: Any, *, restore_cursor_guide: Callable[[], None]) -> None:  # noqa: ANN401
    """Run synchronous cleanup before Textual tears down the app."""
    inflight = app._inflight_turn_stats
    if inflight is not None:
        app._inflight_turn_stats = None
        if not inflight.wall_time_seconds:
            inflight.wall_time_seconds = time.monotonic() - app._inflight_turn_start
        app._session_stats.merge(inflight)

    app._discard_queue()

    if app._shell_running and app._shell_worker:
        app._shell_worker.cancel()
    if app._agent_running and app._agent_worker:
        app._agent_worker.cancel()
    if app._wecom_task and not app._wecom_task.done():
        if app._wecom_bridge is not None:
            app._wecom_bridge.stop()
        app._wecom_task.cancel()

    from invincat_cli.hooks import _dispatch_hook_sync, _load_hooks

    hooks = _load_hooks()
    if hooks:
        payload = json.dumps(
            {
                "event": "session.end",
                "thread_id": getattr(app, "_lc_thread_id", ""),
            }
        ).encode()
        _dispatch_hook_sync("session.end", payload, hooks)

    restore_cursor_guide()
