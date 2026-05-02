"""WeCom long-connection bridge lifecycle management."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import OrderedDict, deque
from contextlib import suppress
from typing import Any

from collections.abc import Awaitable, Callable

from invincat_cli.wecom.protocol import (
    build_wecom_ping_frame,
    build_wecom_stream_frame,
    build_wecom_subscribe_frame,
    is_supported_wecom_message_frame,
    wecom_frame_req_id,
)

logger = logging.getLogger(__name__)

WECOM_HEARTBEAT_INTERVAL = 30.0
WECOM_STALE_CONNECTION_SECONDS = 90.0
WECOM_MAX_MESSAGE_TASKS = 20


class WeComBridge:
    """Manage the WeCom websocket, outbox, request matching, and reconnects."""

    def __init__(
        self,
        *,
        on_status: Callable[[str], Awaitable[None]],
        on_error: Callable[[str], Awaitable[None]],
        on_message: Callable[[dict[str, Any]], Awaitable[None]],
        should_exit: Callable[[], bool],
    ) -> None:
        self._on_status = on_status
        self._on_error = on_error
        self._on_message = on_message
        self._should_exit = should_exit

        self.active = False
        self._ws: Any = None
        self._send_lock = asyncio.Lock()
        self._outbox: deque[dict[str, Any]] = deque()
        self._seen_req_ids: OrderedDict[str, None] = OrderedDict()
        self._message_tasks: set[asyncio.Task[None]] = set()
        self._pending_requests: dict[str, asyncio.Future[dict[str, Any]]] = {}

    def stop(self) -> None:
        self.active = False

    def enqueue(self, payload: dict[str, Any]) -> None:
        self._outbox.append(payload)

    async def run(self, *, bot_id: str, secret: str, ws_url: str) -> None:
        """Run the WeCom long-connection client until stopped."""
        try:
            import websockets
        except Exception as exc:
            await self._on_error(f"Missing websockets dependency: {exc}")
            self.active = False
            return

        self.active = True
        reconnect_delay = 1
        while self.active and not self._should_exit():
            heartbeat_task: asyncio.Task[None] | None = None
            try:
                async with websockets.connect(
                    ws_url,
                    # WeCom does not respond to RFC-6455 Ping frames reliably;
                    # use the application-level ping command instead.
                    ping_interval=None,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    await ws.send(
                        json.dumps(
                            build_wecom_subscribe_frame(bot_id, secret),
                            ensure_ascii=False,
                        )
                    )
                    await self._on_status("WeCom connected and subscribed.")
                    self._discard_stale_progress_frames()
                    await self.flush_outbox()
                    heartbeat_task = asyncio.create_task(self._heartbeat(ws))
                    saw_subscribe_ack = False
                    while self.active and not self._should_exit():
                        raw = await self._recv_raw(ws)
                        if not self.active or self._should_exit():
                            break
                        try:
                            frame = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        req_id = wecom_frame_req_id(frame)
                        pending = self._pending_requests.pop(req_id, None)
                        if pending is not None and not pending.done():
                            pending.set_result(frame)
                            continue
                        if await self._handle_control_frame(
                            frame,
                            saw_subscribe_ack=saw_subscribe_ack,
                        ):
                            if frame.get("cmd") is None and not saw_subscribe_ack:
                                saw_subscribe_ack = True
                            if not self.active:
                                break
                            continue
                        if frame.get("cmd") is None and not saw_subscribe_ack:
                            saw_subscribe_ack = True
                        await self._handle_callback_frame(frame)

                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await heartbeat_task
                self._ws = None
                reconnect_delay = 1
            except asyncio.CancelledError:
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                self._ws = None
                break
            except Exception as exc:
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                self._ws = None
                logger.warning("wecom bridge disconnected: %s", exc, exc_info=True)
                reason = str(exc).strip() or type(exc).__name__
                code = getattr(exc, "code", None)
                close_reason = getattr(exc, "reason", None)
                if code is not None:
                    reason = f"{reason} (code={code}, reason={close_reason})"
                with suppress(Exception):
                    await self._on_status(
                        "WeCom disconnected: "
                        f"{reason}. Reconnecting in {reconnect_delay}s..."
                    )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)

        self.active = False
        self._cancel_pending_requests()
        self._cancel_message_tasks()

    async def flush_outbox(self) -> bool:
        """Flush pending outbound frames using the current live connection."""
        ws = self._ws
        if ws is None:
            return False
        async with self._send_lock:
            while self._outbox:
                payload = self._outbox[0]
                try:
                    raw = json.dumps(payload, ensure_ascii=False)
                    body = payload.get("body") or {}
                    stream = body.get("stream") or {}
                    logger.debug(
                        "wecom send cmd=%s req_id=%s chatid=%s stream_id=%s finish=%s",
                        payload.get("cmd"),
                        (payload.get("headers") or {}).get("req_id", ""),
                        body.get("chatid", ""),
                        stream.get("id", ""),
                        stream.get("finish", ""),
                    )
                    await ws.send(raw)
                except Exception as send_exc:
                    logger.warning("wecom outbox send failed: %s", send_exc, exc_info=True)
                    if self._ws is ws:
                        self._ws = None
                    await self._close_ws(ws)
                    return False
                self._outbox.popleft()
        return True

    async def send_request(
        self,
        payload: dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Send a WeCom request frame and wait for its matching req_id response."""
        ws = self._ws
        if ws is None:
            raise RuntimeError("WeCom connection is offline")
        req_id = wecom_frame_req_id(payload)
        if not req_id:
            raise RuntimeError("WeCom request payload is missing headers.req_id")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_requests[req_id] = fut
        body = payload.get("body") or {}
        logger.debug(
            "wecom request send cmd=%s req_id=%s body_keys=%s",
            payload.get("cmd"),
            req_id,
            sorted(body.keys()),
        )
        try:
            async with self._send_lock:
                await ws.send(json.dumps(payload, ensure_ascii=False))
            response = await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError as exc:
            if self._ws is ws:
                self._ws = None
            await self._close_ws(ws)
            raise RuntimeError(
                f"WeCom request timed out: cmd={payload.get('cmd')} req_id={req_id}"
            ) from exc
        except Exception:
            if self._ws is ws:
                self._ws = None
            await self._close_ws(ws)
            raise
        finally:
            self._pending_requests.pop(req_id, None)

        errcode = response.get("errcode", 0)
        resp_body = response.get("body") or {}
        logger.debug(
            "wecom request response cmd=%s req_id=%s errcode=%s errmsg=%s body_keys=%s",
            payload.get("cmd"),
            req_id,
            errcode,
            response.get("errmsg", ""),
            sorted(resp_body.keys()) if isinstance(resp_body, dict) else type(resp_body).__name__,
        )
        if errcode not in (0, None):
            errmsg = response.get("errmsg", "")
            raise RuntimeError(f"WeCom request failed: errcode={errcode} errmsg={errmsg}")
        return response

    async def _recv_raw(self, ws: Any) -> str:  # noqa: ANN401
        try:
            return await asyncio.wait_for(
                ws.recv(),
                timeout=WECOM_STALE_CONNECTION_SECONDS,
            )
        except TimeoutError as exc:
            logger.warning(
                "wecom connection received no frames for %.1fs; reconnecting",
                WECOM_STALE_CONNECTION_SECONDS,
            )
            await self._close_ws(ws)
            raise RuntimeError("WeCom connection stale; reconnecting") from exc

    async def _heartbeat(self, ws: Any) -> None:  # noqa: ANN401
        while True:
            await asyncio.sleep(WECOM_HEARTBEAT_INTERVAL)
            try:
                await ws.send(json.dumps(build_wecom_ping_frame(), ensure_ascii=False))
            except Exception as exc:
                logger.warning("wecom heartbeat send failed: %s", exc, exc_info=True)
                await self._close_ws(ws)
                return

    async def _close_ws(self, ws: Any) -> None:  # noqa: ANN401
        with suppress(Exception):
            await asyncio.wait_for(ws.close(), timeout=5)
            return
        transport = getattr(ws, "transport", None)
        if transport is not None:
            with suppress(Exception):
                transport.abort()

    async def _handle_control_frame(
        self,
        frame: dict[str, Any],
        *,
        saw_subscribe_ack: bool,
    ) -> bool:
        if frame.get("cmd") is not None:
            return False
        errcode = frame.get("errcode", 0)
        errmsg = str(frame.get("errmsg", ""))
        if not saw_subscribe_ack:
            if errcode == 0:
                await self._on_status("WeCom subscription acknowledged.")
            else:
                await self._on_error(
                    f"WeCom subscribe failed: errcode={errcode} errmsg={errmsg}"
                )
                self.active = False
        return True

    async def _handle_callback_frame(self, frame: dict[str, Any]) -> None:
        if not is_supported_wecom_message_frame(frame):
            return
        req_id = wecom_frame_req_id(frame)
        body = frame.get("body") or {}
        from_obj = body.get("from") or {}
        from_userid = from_obj.get("userid", "") if isinstance(from_obj, dict) else ""
        logger.info(
            "wecom inbound message req_id=%s chatid=%s chattype=%s from_userid=%s msgtype=%s msgid=%s body_keys=%s",
            req_id,
            body.get("chatid", ""),
            body.get("chattype", ""),
            from_userid,
            body.get("msgtype", ""),
            body.get("msgid", ""),
            sorted(body.keys()),
        )
        if req_id and req_id in self._seen_req_ids:
            logger.debug("Skipping duplicate wecom req_id=%s", req_id)
            return

        if len(self._message_tasks) >= WECOM_MAX_MESSAGE_TASKS:
            logger.warning(
                "wecom inbound message queue full size=%d req_id=%s",
                len(self._message_tasks),
                req_id,
            )
            self.enqueue(
                build_wecom_stream_frame(
                    frame,
                    uuid.uuid4().hex,
                    "当前企业微信消息队列繁忙，请稍后再试。",
                    finish=True,
                )
            )
            if await self.flush_outbox() and req_id:
                self._remember_req_id(req_id)
            return

        if req_id:
            self._remember_req_id(req_id)
        task = asyncio.create_task(self._on_message(frame))
        self._message_tasks.add(task)
        task.add_done_callback(self._on_message_task_done)

    def _remember_req_id(self, req_id: str) -> None:
        self._seen_req_ids[req_id] = None
        if len(self._seen_req_ids) > 500:  # noqa: PLR2004
            while len(self._seen_req_ids) > 300:
                self._seen_req_ids.popitem(last=False)

    def _on_message_task_done(self, task: asyncio.Task[None]) -> None:
        self._message_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(
                "wecom inbound message task failed: %s",
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    def _discard_stale_progress_frames(self) -> None:
        self._outbox = deque(
            f for f in self._outbox
            if not (
                f.get("cmd") == "aibot_respond_msg"
                and not (((f.get("body") or {}).get("stream") or {}).get("finish", True))
            )
        )

    def _cancel_pending_requests(self) -> None:
        for pending in self._pending_requests.values():
            if not pending.done():
                pending.cancel()
        self._pending_requests.clear()

    def _cancel_message_tasks(self) -> None:
        for task in list(self._message_tasks):
            task.cancel()
        self._message_tasks.clear()
