# Observability: HTTP health/metrics (OFF by default), systemd watchdog, optional OTEL.
# Enable via OBS_HTTP_ENABLED=1, SYSTEMD_WATCHDOG=1, OTEL_ENABLED=1.

from app.infra.observability.config import ObservabilityConfig, load_observability_config

__all__ = ["ObservabilityConfig", "load_observability_config"]
