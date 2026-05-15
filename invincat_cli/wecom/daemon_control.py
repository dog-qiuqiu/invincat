"""Unix-socket control RPC for the WeCom daemon."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

async def _socket_rpc(socket_path: Path, request: dict[str, Any]) -> dict[str, Any]:
    from invincat_cli.wecom import daemon as daemon_mod

    """Send one JSON request to the daemon socket and return the response."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(socket_path)),
            timeout=daemon_mod._SOCKET_TIMEOUT,
        )
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        raise RuntimeError(f"Daemon socket not available: {exc}") from exc
    try:
        writer.write((json.dumps(request, ensure_ascii=False) + "\n").encode())
        await asyncio.wait_for(writer.drain(), timeout=daemon_mod._SOCKET_TIMEOUT)
        line = await asyncio.wait_for(reader.readline(), timeout=daemon_mod._SOCKET_TIMEOUT)
        return json.loads(line.decode())
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

async def get_daemon_status(cwd: Path) -> dict[str, Any]:
    """Query the running daemon for its status. Falls back to state file."""
    from invincat_cli.wecom import daemon as daemon_mod

    state = daemon_mod.read_daemon_state(cwd)
    if state is None:
        return {"running": False}
    pid = state.get("pid")
    alive = daemon_mod.is_daemon_running(cwd)
    if not alive:
        return {"running": False}
    socket_path = Path(state.get("socket_path", ""))
    try:
        resp = await daemon_mod._socket_rpc(socket_path, {"cmd": "status"})
        resp["running"] = True
        return resp
    except Exception:
        # Socket not yet up or temporarily unavailable
        fallback_pid = daemon_mod._verified_lock_owner_pid(cwd, state)
        return {
            "running": True,
            "pid": pid,
            "started_at": state.get("started_at", ""),
            "connected": None,
            "messages_handled": None,
            "control_socket": "unavailable",
            "verified_stop_fallback": fallback_pid is not None,
        }

async def stop_daemon(cwd: Path) -> bool:
    """Send a stop command to the running daemon. Returns True if accepted."""
    from invincat_cli.wecom import daemon as daemon_mod

    state = daemon_mod.read_daemon_state(cwd)
    if state is None or not daemon_mod.is_daemon_running(cwd):
        return False
    socket_path = Path(state.get("socket_path", ""))
    try:
        resp = await daemon_mod._socket_rpc(socket_path, {"cmd": "stop"})
        return bool(resp.get("ok"))
    except Exception as exc:
        logger.debug("stop_daemon socket rpc failed: %s", exc)
        return daemon_mod._signal_verified_daemon_owner(cwd, state)


# ---------------------------------------------------------------------------
# Daemon process launch (double-fork)
# ---------------------------------------------------------------------------

async def _handle_socket_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    bridge: Any,
    handler: Any,
    stop_event: asyncio.Event,
) -> None:
    from invincat_cli.wecom import daemon as daemon_mod

    pending_stop = False
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout=daemon_mod._SOCKET_TIMEOUT)
        request = json.loads(raw.decode())
        cmd = request.get("cmd", "")

        if cmd == "status":
            # Reflect the actual transport state (subscribe ACK seen) rather
            # than the bridge's intent flag, which is True from boot to stop.
            response: dict[str, Any] = {
                "ok": True,
                "pid": os.getpid(),
                "connected": bridge.ready.is_set(),
                "messages_handled": handler.messages_handled,
            }
        elif cmd == "stop":
            response = {"ok": True}
            pending_stop = True
        else:
            response = {"ok": False, "error": f"Unknown cmd: {cmd}"}

        writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
        await writer.drain()
    except Exception as exc:
        logger.debug("Socket client error: %s", exc)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        # Signal shutdown only AFTER the response has been fully delivered
        # and the connection closed, so the client always sees {"ok": true}.
        if pending_stop:
            stop_event.set()
