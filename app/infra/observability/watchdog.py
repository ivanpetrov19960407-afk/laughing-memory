"""
systemd watchdog: send WATCHDOG=1 to NOTIFY_SOCKET when enabled.
No hard dependency on systemd package; uses Unix datagram socket.
If NOTIFY_SOCKET is absent or interval not available → graceful no-op.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import Callable

LOGGER = logging.getLogger(__name__)

_NOTIFY_SOCKET = "NOTIFY_SOCKET"
_WATCHDOG_USEC = "WATCHDOG_USEC"


def _get_notify_socket(env: dict[str, str] | None = None) -> str | None:
    source = env if env is not None else os.environ
    path = source.get(_NOTIFY_SOCKET)
    if not path or not path.strip():
        return None
    path = path.strip()
    if path.startswith("@"):
        path = "\0" + path[1:]
    return path


def _get_watchdog_interval_usec(env: dict[str, str] | None = None) -> int | None:
    source = env if env is not None else os.environ
    raw = source.get(_WATCHDOG_USEC)
    if not raw or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def sd_notify(message: str, env: dict[str, str] | None = None) -> bool:
    """
    Send a message to systemd via NOTIFY_SOCKET (Unix datagram).
    Returns True if sent, False if socket missing or send failed (no exception).
    """
    path = _get_notify_socket(env)
    if not path:
        return False
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.sendto(message.encode("utf-8"), path)
        sock.close()
        return True
    except (OSError, TypeError) as e:
        LOGGER.debug("sd_notify failed: %s", e)
        return False


def is_watchdog_available(env: dict[str, str] | None = None) -> bool:
    """True if NOTIFY_SOCKET is set and we can use watchdog."""
    return _get_notify_socket(env) is not None


def get_watchdog_interval_seconds(env: dict[str, str] | None = None) -> float | None:
    """WatchdogSec in seconds (from WATCHDOG_USEC). None if not set."""
    usec = _get_watchdog_interval_usec(env)
    if usec is None or usec <= 0:
        return None
    return usec / 1_000_000.0


async def run_watchdog_loop(
    *,
    enabled: bool,
    env: dict[str, str] | None = None,
    interval_override_seconds: float | None = None,
    notify_fn: Callable[[str, dict[str, str] | None], bool] | None = None,
) -> None:
    """
    If enabled and NOTIFY_SOCKET is set, send WATCHDOG=1 every (interval/2) seconds.
    Otherwise no-op (graceful, no error).
    notify_fn(s, env) can be injected for tests; default is sd_notify.
    """
    if not enabled:
        return
    path = _get_notify_socket(env)
    if not path:
        return
    interval = interval_override_seconds
    if interval is None:
        interval = get_watchdog_interval_seconds(env)
    if interval is None or interval <= 0:
        interval = 15.0
    send_interval = max(0.5, interval / 2.0)
    def _notify(msg: str, e: dict[str, str] | None = None) -> bool:
        return sd_notify(msg, e)

    do_notify = notify_fn if notify_fn is not None else _notify

    while True:
        try:
            do_notify("WATCHDOG=1", env)
        except Exception as e:
            LOGGER.debug("watchdog notify error: %s", e)
        await asyncio.sleep(send_interval)
