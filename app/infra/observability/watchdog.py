"""Systemd watchdog support (optional)."""

from __future__ import annotations

import logging
import os
from typing import Any

LOGGER = logging.getLogger(__name__)

_watchdog_enabled = False
_watchdog_fd: int | None = None


def initialize_watchdog(enabled: bool = False) -> None:
    """Initialize systemd watchdog if enabled and available."""
    global _watchdog_enabled, _watchdog_fd

    if not enabled:
        LOGGER.debug("Systemd watchdog disabled")
        return

    try:
        import systemd.daemon

        # Check if watchdog is active
        watchdog_usec = systemd.daemon.watchdog_enabled()
        if watchdog_usec is None or watchdog_usec == 0:
            LOGGER.debug("Systemd watchdog not active (WatchdogSec not set)")
            return

        _watchdog_fd = systemd.daemon.notify("READY=1")
        _watchdog_enabled = True
        LOGGER.info("Systemd watchdog initialized (interval: %d usec)", watchdog_usec)
    except ImportError:
        LOGGER.debug("systemd package not installed; watchdog disabled")
    except Exception as exc:
        LOGGER.warning("Failed to initialize systemd watchdog: %s", exc)


def notify_watchdog() -> None:
    """Send WATCHDOG=1 notification to systemd."""
    if not _watchdog_enabled:
        return

    try:
        import systemd.daemon

        systemd.daemon.notify("WATCHDOG=1")
    except Exception as exc:
        LOGGER.debug("Failed to notify watchdog: %s", exc)


def is_watchdog_enabled() -> bool:
    """Check if watchdog is enabled."""
    return _watchdog_enabled
