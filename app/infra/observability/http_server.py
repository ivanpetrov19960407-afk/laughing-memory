"""Local HTTP server for health checks and metrics."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

from app.infra.observability.health import HealthChecker, HealthStatus, ReadinessStatus
from app.infra.observability.metrics import MetricsCollector

LOGGER = logging.getLogger(__name__)


class ObservabilityHTTPServer:
    """Local HTTP server for observability endpoints."""

    def __init__(
        self,
        host: str,
        port: int,
        health_checker: HealthChecker,
        metrics_collector: MetricsCollector,
        get_app_state: Any,  # Callable that returns app state dict
    ) -> None:
        """Initialize HTTP server."""
        if web is None:
            raise RuntimeError("aiohttp is not installed; cannot start HTTP server")

        self._host = host
        self._port = port
        self._health_checker = health_checker
        self._metrics_collector = metrics_collector
        self._get_app_state = get_app_state
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def start(self) -> None:
        """Start the HTTP server."""
        if web is None:
            LOGGER.warning("aiohttp not installed; HTTP server disabled")
            return

        self._app = web.Application()
        self._app.router.add_get("/healthz", self._handle_healthz)
        self._app.router.add_get("/readyz", self._handle_readyz)
        self._app.router.add_get("/metrics", self._handle_metrics)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()

        LOGGER.info("Observability HTTP server started on %s:%d", self._host, self._port)

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()
        LOGGER.info("Observability HTTP server stopped")

    async def _handle_healthz(self, request: web.Request) -> web.Response:
        """Handle GET /healthz."""
        app_state = self._get_app_state()
        health_status = self._health_checker.get_health_status(
            scheduler_ok=app_state.get("scheduler_ok", True),
            calendar_backend=app_state.get("calendar_backend", "local"),
            llm_client_configured=app_state.get("llm_client_configured", False),
            search_client_configured=app_state.get("search_client_configured", False),
        )

        response_data = {
            "app_version": health_status.app_version,
            "uptime_seconds": round(health_status.uptime_seconds, 2),
            "status": health_status.status,
            "last_error_count": health_status.last_error_count,
        }

        return web.json_response(response_data, status=200)

    async def _handle_readyz(self, request: web.Request) -> web.Response:
        """Handle GET /readyz."""
        app_state = self._get_app_state()
        readiness_status = self._health_checker.get_readiness_status(
            initialized=app_state.get("initialized", True),
            scheduler_active=app_state.get("scheduler_active", True),
            critical_dependencies_ok=app_state.get("critical_dependencies_ok", True),
        )

        if readiness_status.ready:
            return web.json_response({"ready": True}, status=200)
        else:
            return web.json_response(
                {"ready": False, "reason": readiness_status.reason},
                status=503,
            )

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        """Handle GET /metrics."""
        metrics_text = self._metrics_collector.get_metrics_text()
        return web.Response(text=metrics_text, content_type="text/plain; version=0.0.4")
