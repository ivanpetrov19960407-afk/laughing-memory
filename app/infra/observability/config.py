from __future__ import annotations

import os
from dataclasses import dataclass


def _parse_bool_default_false(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ObservabilityConfig:
    """Observability flags. All OFF by default; no HTTP server, no watchdog, no OTEL."""

    obs_http_enabled: bool
    obs_http_host: str
    obs_http_port: int
    otel_enabled: bool
    systemd_watchdog_enabled: bool

    @classmethod
    def default(cls) -> ObservabilityConfig:
        return cls(
            obs_http_enabled=False,
            obs_http_host="127.0.0.1",
            obs_http_port=8081,
            otel_enabled=False,
            systemd_watchdog_enabled=False,
        )


def load_observability_config(env: dict[str, str] | None = None) -> ObservabilityConfig:
    """Load from ENV. If OBS_HTTP_ENABLED is not 1/true, HTTP and metrics are off."""
    source = env if env is not None else os.environ
    obs_http_enabled = _parse_bool_default_false(source.get("OBS_HTTP_ENABLED"))
    host = (source.get("OBS_HTTP_HOST") or "127.0.0.1").strip()
    port_str = source.get("OBS_HTTP_PORT", "8081").strip()
    try:
        port = int(port_str)
    except ValueError:
        port = 8081
    if port <= 0 or port > 65535:
        port = 8081
    otel_enabled = _parse_bool_default_false(source.get("OTEL_ENABLED"))
    systemd_watchdog_enabled = _parse_bool_default_false(source.get("SYSTEMD_WATCHDOG"))
    return ObservabilityConfig(
        obs_http_enabled=obs_http_enabled,
        obs_http_host=host,
        obs_http_port=port,
        otel_enabled=otel_enabled,
        systemd_watchdog_enabled=systemd_watchdog_enabled,
    )
