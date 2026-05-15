"""WeCom daemon configuration model."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

from invincat_cli.wecom.daemon_constants import (
    _LOCK_FILENAME,
    _LOG_FILENAME,
    _SOCKET_FILENAME,
    _STATE_FILENAME,
)


@dataclasses.dataclass(frozen=True)
class WeComDaemonConfig:
    bot_id: str
    secret: str
    ws_url: str
    cwd: Path

    @property
    def state_file(self) -> Path:
        return self.cwd / _STATE_FILENAME

    @property
    def log_file(self) -> Path:
        return self.cwd / _LOG_FILENAME

    @property
    def socket_path(self) -> Path:
        return self.cwd / _SOCKET_FILENAME

    @property
    def lock_file(self) -> Path:
        return self.cwd / _LOCK_FILENAME

    @classmethod
    def from_env(cls, cwd: Path) -> WeComDaemonConfig:
        bot_id = os.getenv("WECOM_BOT_ID", "").strip()
        secret = os.getenv("WECOM_BOT_SECRET", "").strip()
        if not bot_id or not secret:
            raise ValueError(
                "WECOM_BOT_ID and WECOM_BOT_SECRET must be set to start the WeCom daemon."
            )
        ws_url = os.getenv("WECOM_WS_URL", "wss://openws.work.weixin.qq.com").strip()
        return cls(bot_id=bot_id, secret=secret, ws_url=ws_url, cwd=cwd)
