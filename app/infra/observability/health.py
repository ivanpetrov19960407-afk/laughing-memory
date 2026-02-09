"""Health and readiness checks."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.infra.version import resolve_app_version

LOGGER = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    """Health check status."""

    app_version: str
    uptime_seconds: float
    status: dict[str, str]
    last_error_count: int = 0


@dataclass
class ReadinessStatus:
    """Readiness check status."""

    ready: bool
    reason: str | None = None


class HealthChecker:
    """Health and readiness checker."""

    def __init__(self, start_time: float, app_config: dict[str, Any] | None = None) -> None:
        """Initialize health checker."""
        self._start_time = start_time
        self._app_config = app_config or {}
        self._error_counts: list[tuple[float, str]] = []  # (timestamp, component)
        self._error_window_seconds = 300  # 5 minutes

    def record_error(self, component: str) -> None:
        """Record an error for health checks."""
        now = time.monotonic()
        self._error_counts.append((now, component))
        # Clean old errors
        cutoff = now - self._error_window_seconds
        self._error_counts = [(ts, comp) for ts, comp in self._error_counts if ts > cutoff]

    def get_health_status(
        self,
        *,
        scheduler_ok: bool = True,
        calendar_backend: str = "local",
        llm_client_configured: bool = False,
        search_client_configured: bool = False,
    ) -> HealthStatus:
        """Get health status."""
        uptime = time.monotonic() - self._start_time
        app_version = resolve_app_version(self._app_config.get("system_metadata", {}))

        # Count errors in the last window
        now = time.monotonic()
        cutoff = now - self._error_window_seconds
        recent_errors = [ts for ts, _ in self._error_counts if ts > cutoff]
        last_error_count = len(recent_errors)

        status = {
            "scheduler": "ok" if scheduler_ok else "error",
            "calendar_backend": calendar_backend,
            "llm_client_configured": "true" if llm_client_configured else "false",
            "search_client_configured": "true" if search_client_configured else "false",
        }

        return HealthStatus(
            app_version=app_version,
            uptime_seconds=uptime,
            status=status,
            last_error_count=last_error_count,
        )

    def get_readiness_status(
        self,
        *,
        initialized: bool = True,
        scheduler_active: bool = True,
        critical_dependencies_ok: bool = True,
    ) -> ReadinessStatus:
        """Get readiness status."""
        if not initialized:
            return ReadinessStatus(ready=False, reason="not_initialized")
        if not scheduler_active:
            return ReadinessStatus(ready=False, reason="scheduler_not_active")
        if not critical_dependencies_ok:
            return ReadinessStatus(ready=False, reason="critical_dependencies_unavailable")
        return ReadinessStatus(ready=True)
