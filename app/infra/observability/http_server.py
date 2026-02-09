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
    """Prometheus text format. No secrets."""
    state: AppState = request.app["state"]
    collector = state.get("metrics_collector")
    if collector is None:
        text = "# No metrics collector\n"
    else:
        text = collector.format_prometheus()
    return web.Response(text=text, content_type="text/plain; charset=utf-8")


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
