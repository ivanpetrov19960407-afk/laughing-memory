"""
Observability HTTP server: /healthz, /readyz, /metrics. Only started when OBS_HTTP_ENABLED=1.
Binds to localhost only. No secrets or correlation_id in responses.
"""

from __future__ import annotations

import time
from typing import Any

from aiohttp import web

# Type alias for app state
AppState = dict[str, Any]


def _get_init_complete(state: AppState) -> bool:
    return state.get("init_complete", False)


def _get_last_error_count(state: AppState) -> int:
    """Only numeric count; no error details exposed."""
    v = state.get("last_error_count", 0)
    if isinstance(v, int) and v >= 0:
        return v
    return 0


def _get_start_time(state: AppState) -> float:
    return state.get("start_time", time.monotonic())


def _get_version(state: AppState) -> str:
    return state.get("version", "unknown")


async def healthz(request: web.Request) -> web.Response:
    """Liveness: process is up. No secrets, no correlation_id."""
    state: AppState = request.app["state"]
    # Safe payload: status, version, uptime_seconds only
    uptime = time.monotonic() - _get_start_time(state)
    body = {
        "status": "ok",
        "version": _get_version(state),
        "uptime_seconds": round(uptime, 2),
    }
    return web.json_response(body)


async def readyz(request: web.Request) -> web.Response:
    """
    Readiness: 200 only if init complete and no critical errors in recent window.
    503 if init not complete or critical errors. No blocking or network checks.
    """
    state: AppState = request.app["state"]
    init_ok = _get_init_complete(state)
    error_count = _get_last_error_count(state)
    # Consider "critical" if we track errors and threshold exceeded (e.g. last N minutes)
    critical_window_errors = state.get("critical_error_count_last_n_minutes", 0)
    if not isinstance(critical_window_errors, int):
        critical_window_errors = 0
    ready = init_ok and critical_window_errors == 0
    status = 200 if ready else 503
    body = {
        "ready": ready,
        "init_complete": init_ok,
        "last_error_count": error_count,
    }
    return web.json_response(body, status=status)


async def metrics(request: web.Request) -> web.Response:
    """Prometheus text format. No secrets. Content-Type: text/plain; version=0.0.4 (no charset)."""
    state: AppState = request.app["state"]
    collector = state.get("metrics_collector")
    if collector is None:
        text = "# No metrics collector\n"
    elif hasattr(collector, "get_metrics_text"):
        text = collector.get_metrics_text()
    else:
        text = getattr(collector, "format_prometheus", lambda: "# No metrics\n")()
    body = (text or "# No metrics\n").encode("utf-8")
    return web.Response(
        body=body,
        headers={"Content-Type": "text/plain; version=0.0.4"},
    )


def create_app(state: AppState) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/readyz", readyz)
    app.router.add_get("/health", healthz)
    app.router.add_get("/metrics", metrics)
    return app


async def start_observability_http(
    host: str,
    port: int,
    state: AppState,
) -> tuple[web.AppRunner, web.TCPSite]:
    """Create runner and start site. Caller must call runner.cleanup() on shutdown."""
    app = create_app(state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner, site


# --- ObservabilityHTTPServer: used by test_observability.py (HealthChecker + get_app_state) ---

from typing import Callable

from app.infra.observability.health import HealthChecker


async def _server_healthz(request: web.Request) -> web.Response:
    server: ObservabilityHTTPServer = request.app["observability_server"]
    state = server._get_app_state()
    status = server._health_checker.get_health_status(
        scheduler_ok=state.get("scheduler_ok", True),
        calendar_backend=str(state.get("calendar_backend", "local")),
        llm_client_configured=bool(state.get("llm_client_configured", False)),
        search_client_configured=bool(state.get("search_client_configured", False)),
    )
    body = {
        "app_version": status.app_version,
        "uptime_seconds": status.uptime_seconds,
        "status": status.status,
        "last_error_count": status.last_error_count,
    }
    return web.json_response(body)


async def _server_readyz(request: web.Request) -> web.Response:
    server: ObservabilityHTTPServer = request.app["observability_server"]
    state = server._get_app_state()
    status = server._health_checker.get_readiness_status(
        initialized=bool(state.get("initialized", True)),
        scheduler_active=bool(state.get("scheduler_active", True)),
        critical_dependencies_ok=bool(state.get("critical_dependencies_ok", True)),
    )
    return web.json_response({"ready": status.ready, "reason": status.reason})


async def _server_metrics(request: web.Request) -> web.Response:
    server: ObservabilityHTTPServer = request.app["observability_server"]
    text = server._metrics_collector.get_metrics_text()
    body = (text or "# No metrics\n").encode("utf-8")
    return web.Response(
        body=body,
        headers={"Content-Type": "text/plain; version=0.0.4"},
    )


class ObservabilityHTTPServer:
    """HTTP server for observability using HealthChecker and get_app_state. Used by tests."""

    def __init__(
        self,
        host: str,
        port: int,
        health_checker: HealthChecker,
        metrics_collector: Any,
        get_app_state: Callable[[], dict[str, object]],
    ) -> None:
        self._host = host
        self._port = port
        self._health_checker = health_checker
        self._metrics_collector = metrics_collector
        self._get_app_state_fn = get_app_state
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    def _get_app_state(self) -> dict[str, object]:
        return self._get_app_state_fn()

    async def start(self) -> None:
        app = web.Application()
        app["observability_server"] = self
        app.router.add_get("/healthz", _server_healthz)
        app.router.add_get("/readyz", _server_readyz)
        app.router.add_get("/metrics", _server_metrics)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
