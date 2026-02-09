"""Prometheus metrics collection."""

from __future__ import annotations

import logging
import time
from typing import Any

try:
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
except ImportError:
    CollectorRegistry = None
    Counter = None
    Gauge = None
    Histogram = None
    generate_latest = None

LOGGER = logging.getLogger(__name__)


class MetricsCollector:
    """Centralized metrics collector using prometheus_client."""

    def __init__(self, registry: Any = None) -> None:
        """Initialize metrics if prometheus_client is available.
        Uses a dedicated registry by default so multiple instances (e.g. in tests) do not conflict.
        """
        if Counter is None or CollectorRegistry is None:
            LOGGER.warning("prometheus_client not installed; metrics disabled")
            self._enabled = False
            self._registry = None
            return

        self._registry = registry if registry is not None else CollectorRegistry()
        self._enabled = True
        self._start_time = time.monotonic()

        # Counter: bot_updates_total{type}
        self._updates_counter = Counter(
            "bot_updates_total",
            "Total number of bot updates",
            ["type"],
            registry=self._registry,
        )

        # Counter: bot_errors_total{component}
        self._errors_counter = Counter(
            "bot_errors_total",
            "Total number of errors",
            ["component"],
            registry=self._registry,
        )

        # Histogram: bot_request_duration_seconds{intent}
        self._request_duration = Histogram(
            "bot_request_duration_seconds",
            "Request duration in seconds",
            ["intent"],
            buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
            registry=self._registry,
        )

        # Gauge: bot_uptime_seconds
        self._uptime_gauge = Gauge(
            "bot_uptime_seconds",
            "Bot uptime in seconds",
            registry=self._registry,
        )

        # Gauge: bot_active_wizards
        self._active_wizards_gauge = Gauge(
            "bot_active_wizards",
            "Number of active wizards",
            registry=self._registry,
        )

        LOGGER.info("Metrics collector initialized")

    @property
    def enabled(self) -> bool:
        """Check if metrics are enabled."""
        return self._enabled

    def record_update(self, update_type: str) -> None:
        """Record a bot update."""
        if not self._enabled:
            return
        self._updates_counter.labels(type=update_type).inc()

    def record_error(self, component: str) -> None:
        """Record an error."""
        if not self._enabled:
            return
        self._errors_counter.labels(component=component).inc()

    def record_request_duration(self, intent: str, duration_seconds: float) -> None:
        """Record request duration."""
        if not self._enabled:
            return
        self._request_duration.labels(intent=intent).observe(duration_seconds)

    def update_uptime(self) -> None:
        """Update uptime gauge."""
        if not self._enabled:
            return
        uptime = time.monotonic() - self._start_time
        self._uptime_gauge.set(uptime)

    def update_active_wizards(self, count: int) -> None:
        """Update active wizards count."""
        if not self._enabled:
            return
        self._active_wizards_gauge.set(max(0, count))

    def get_metrics_text(self) -> str:
        """Get metrics in Prometheus exposition format."""
        if not self._enabled or generate_latest is None or self._registry is None:
            return "# Metrics disabled\n"
        try:
            return generate_latest(self._registry).decode("utf-8")
        except Exception as exc:
            LOGGER.exception("Failed to generate metrics: %s", exc)
            return f"# Error generating metrics: {exc}\n"

    def get_metrics_count(self) -> int:
        """Get approximate number of metrics."""
        if not self._enabled or self._registry is None:
            return 0
        try:
            count = 0
            for collector in self._registry._collector_to_names:
                count += len(self._registry._collector_to_names[collector])
            return count
        except Exception:
            return 0
