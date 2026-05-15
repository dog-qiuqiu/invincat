"""Tests for Textual app runtime entrypoint helpers."""

from __future__ import annotations

import asyncio

from invincat_cli.app_runtime.runner import run_textual_app


class _Result:
    def __init__(
        self,
        *,
        return_code: int,
        thread_id: str | None,
        session_stats: object,
        update_available: object,
    ) -> None:
        self.return_code = return_code
        self.thread_id = thread_id
        self.session_stats = session_stats
        self.update_available = update_available


class _ServerProc:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def test_run_textual_app_returns_result_and_stops_server() -> None:
    server = _ServerProc()
    stats = object()

    class App:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self._server_proc = server
            self.return_code = None
            self._lc_thread_id = "thread-1"
            self._session_stats = stats
            self._update_available = (False, None)

        async def run_async(self) -> None:
            self.return_code = 3

    result = asyncio.run(
        run_textual_app(
            app_cls=App,
            result_cls=_Result,
            app_kwargs={"agent": "agent"},
        )
    )

    assert result.return_code == 3
    assert result.thread_id == "thread-1"
    assert result.session_stats is stats
    assert result.update_available == (False, None)
    assert server.stopped is True


def test_run_textual_app_stops_server_when_run_fails() -> None:
    server = _ServerProc()

    class App:
        def __init__(self, **_kwargs) -> None:
            self._server_proc = server

        async def run_async(self) -> None:
            raise RuntimeError("boom")

    try:
        asyncio.run(
            run_textual_app(
                app_cls=App,
                result_cls=_Result,
                app_kwargs={},
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected RuntimeError")

    assert server.stopped is True
