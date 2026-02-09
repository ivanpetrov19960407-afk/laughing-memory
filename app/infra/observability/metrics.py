"""
Simple metrics collector for Prometheus-style /metrics. Created only when OBS is enabled.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import DefaultDict


class MetricsCollector:
    """In-memory counters; safe to use from async and sync. No secrets stored."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: DefaultDict[str, int] = defaultdict(int)

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def get_counters(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)

    def format_prometheus(self) -> str:
        """Prometheus text format; counter names sanitized (alphanumeric + underscore)."""
        lines = ["# HELP msb_requests_total Total requests", "# TYPE msb_requests_total counter"]
        for name, value in sorted(self.get_counters().items()):
            safe = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
            if not safe:
                safe = "unknown"
            lines.append(f"msb_{safe} {value}")
        return "\n".join(lines) + "\n"
