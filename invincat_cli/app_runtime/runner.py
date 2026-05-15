"""Runtime entrypoint helpers for the Textual app."""

from __future__ import annotations

from typing import Any


async def run_textual_app(
    *,
    app_cls: type[Any],
    result_cls: type[Any],
    app_kwargs: dict[str, Any],
) -> Any:
    """Create, run, clean up, and summarize a Textual app session."""
    app = app_cls(**app_kwargs)
    try:
        await app.run_async()
    finally:
        if app._server_proc is not None:
            app._server_proc.stop()

    return result_cls(
        return_code=app.return_code or 0,
        thread_id=app._lc_thread_id,
        session_stats=app._session_stats,
        update_available=app._update_available,
    )
