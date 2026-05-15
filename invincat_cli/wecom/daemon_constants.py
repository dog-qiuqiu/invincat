"""Constants for the WeCom daemon."""

_STATE_FILENAME = ".invincat/wecom_daemon.json"
_LOG_FILENAME = ".invincat/wecom_daemon.log"
_SOCKET_FILENAME = ".invincat/wecom_daemon.sock"
_LOCK_FILENAME = ".invincat/wecom_daemon.lock"
_SOCKET_TIMEOUT = 5.0
_STARTUP_TIMEOUT = 120.0
_BRIDGE_STARTUP_READY_TIMEOUT = 45.0
_DELIVERY_RETRIES = 8  # 1 initial attempt + 7 retries
_DELIVERY_RETRY_DELAY = 15  # seconds between retries
_DELIVERY_READY_TIMEOUT = 30  # per-attempt wait for bridge subscribe ACK
_DELIVERY_REQUEST_TIMEOUT = 30  # per-attempt WeCom request response timeout
_FILE_PERMS = 0o600  # log / socket / state / lock - owner-only
