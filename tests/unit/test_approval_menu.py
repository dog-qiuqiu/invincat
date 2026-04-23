from __future__ import annotations

import asyncio

from invincat_cli.widgets.approval import ApprovalMenu


def _action_request() -> dict[str, object]:
    return {"name": "approve_plan", "args": {"todos": []}}


def test_approval_menu_without_auto_approve_maps_to_approve_and_reject() -> None:
    loop = asyncio.new_event_loop()
    try:
        menu = ApprovalMenu(_action_request(), allow_auto_approve=False)

        approve_future: asyncio.Future[dict[str, str]] = loop.create_future()
        menu.set_future(approve_future)
        menu._handle_selection(0)
        assert approve_future.result() == {"type": "approve"}

        reject_future: asyncio.Future[dict[str, str]] = loop.create_future()
        menu.set_future(reject_future)
        menu._handle_selection(1)
        assert reject_future.result() == {"type": "reject"}
    finally:
        loop.close()


def test_approval_menu_without_auto_approve_ignores_auto_shortcut() -> None:
    loop = asyncio.new_event_loop()
    try:
        menu = ApprovalMenu(_action_request(), allow_auto_approve=False)
        pending_future: asyncio.Future[dict[str, str]] = loop.create_future()
        menu.set_future(pending_future)
        menu.action_select_auto()
        assert not pending_future.done()
    finally:
        loop.close()
