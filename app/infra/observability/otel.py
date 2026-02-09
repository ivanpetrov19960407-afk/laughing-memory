"""OpenTelemetry support (optional)."""

from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger(__name__)

_otel_enabled = False
_tracer = None
_provider = None


def initialize_otel(
    *,
    enabled: bool = False,
    exporter: str = "console",
    otlp_endpoint: str | None = None,
) -> None:
    """Initialize OpenTelemetry SDK if enabled and available."""
    global _otel_enabled, _tracer, _provider

    if not enabled:
        LOGGER.debug("OpenTelemetry disabled")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

        if exporter == "otlp" and otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

                provider = TracerProvider()
                otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
                provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
                trace.set_tracer_provider(provider)
                _provider = provider
                _tracer = trace.get_tracer(__name__)
                _otel_enabled = True
                LOGGER.info("OpenTelemetry initialized with OTLP exporter: %s", otlp_endpoint)
                return
            except ImportError:
                LOGGER.warning("OTLP exporter not available; falling back to console")
                exporter = "console"

        if exporter == "console":
            provider = TracerProvider()
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            trace.set_tracer_provider(provider)
            _provider = provider
            _tracer = trace.get_tracer(__name__)
            _otel_enabled = True
            LOGGER.info("OpenTelemetry initialized with console exporter")
            return

        LOGGER.warning("Unknown OpenTelemetry exporter: %s", exporter)
    except ImportError:
        LOGGER.debug("OpenTelemetry packages not installed; OTEL disabled")


def get_tracer() -> Any:
    """Get OpenTelemetry tracer if available."""
    return _tracer


def is_otel_enabled() -> bool:
    """Check if OpenTelemetry is enabled."""
    return _otel_enabled


def trace_span(name: str, **attributes: Any) -> Any:
    """Create a trace span if OpenTelemetry is enabled."""
    if not _otel_enabled or _tracer is None:
        # Return a no-op context manager
        from contextlib import nullcontext

        return nullcontext()

    return _tracer.start_as_current_span(name, attributes=attributes)
