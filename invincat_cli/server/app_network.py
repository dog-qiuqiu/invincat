"""Network helpers for local LangGraph server startup."""

from __future__ import annotations


def port_in_use(host: str, port: int) -> bool:
    """Check if a port is already in use."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return True
        else:
            return False


def find_free_port(host: str) -> int:
    """Find a free port on the given host."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]
