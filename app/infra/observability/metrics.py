"""
Simple metrics collector for Prometheus-style /metrics. Created only when OBS is enabled.
API: enabled, record_update, record_error, record_request_duration, update_uptime,
     update_active_wizards, get_metrics_text. All methods no-op when disabled; no global registry.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import DefaultDict


def _sanitize_label(v: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in v) or "unknown"


class MetricsCollector:
    """In-memory counters; safe to use from async and sync. No secrets stored. Per-instance only."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = bool(enabled)
        self._lock = threading.Lock()
        self._counters: DefaultDict[str, int] = defaultdict(int)
        self._duration_sum: DefaultDict[str, float] = defaultdict(float)
        self._duration_count: DefaultDict[str, int] = defaultdict(int)
        self._start_time = time.monotonic()
        self._uptime_seconds: float = 0.0
        self._active_wizards: int = 0

    def record_update(self, update_type: str) -> None:
        if not self.enabled:
            return
        key = "updates." + _sanitize_label(update_type)
        with self._lock:
            self._counters[key] += 1

    def record_error(self, update_type: str) -> None:
        if not self.enabled:
            return
        key = "errors." + _sanitize_label(update_type)
        with self._lock:
            self._counters[key] += 1

    def record_request_duration(self, update_type: str, duration_seconds: float) -> None:
        if not self.enabled:
            return
        key = _sanitize_label(update_type)
        with self._lock:
            self._duration_sum[key] += duration_seconds
            self._duration_count[key] += 1

    def update_uptime(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._uptime_seconds = time.monotonic() - self._start_time

    def update_active_wizards(self, count: int) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._active_wizards = max(0, count)

    def get_metrics_count(self) -> int:
        """Total number of recorded events (for admin/metrics_status)."""
        with self._lock:
            return sum(self._counters.values()) + sum(self._duration_count.values())

    def get_metrics_text(self) -> str:
        if not self.enabled:
            return ""
        with self._lock:
            lines: list[str] = []
            # Counters
            for key in sorted(self._counters.keys()):
                val = self._counters[key]
                name = "msb_" + key.replace(".", "_")
                lines.append(f"# HELP {name} Counter")
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name} {val}")
            # Duration (sum/count per type)
            for key in sorted(self._duration_count.keys()):
                s = self._duration_sum[key]
                c = self._duration_count[key]
                base = "msb_request_duration_seconds_" + key
                lines.append(f"# HELP {base}_sum Request duration sum")
                lines.append(f"# TYPE {base}_sum counter")
                lines.append(f"{base}_sum {s}")
                lines.append(f"# HELP {base}_count Request duration count")
                lines.append(f"# TYPE {base}_count counter")
                lines.append(f"{base}_count {c}")
            # Uptime
            lines.append("# HELP msb_uptime_seconds Process uptime")
            lines.append("# TYPE msb_uptime_seconds gauge")
            lines.append(f"msb_uptime_seconds {self._uptime_seconds}")
            # Active wizards
            lines.append("# HELP msb_active_wizards Active wizards count")
            lines.append("# TYPE msb_active_wizards gauge")
            lines.append(f"msb_active_wizards {self._active_wizards}")
            return "\n".join(lines) + "\n" if lines else ""

    def inc(self, name: str, value: int = 1) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._counters[name] += value

    def get_counters(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)

    def format_prometheus(self) -> str:
        """Prometheus text format; backward compatible."""
        return self.get_metrics_text() if self.enabled else ""
