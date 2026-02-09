"""
Stage 7 observability: OFF by default, watchdog, no secrets in responses.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.infra.observability.config import load_observability_config
from app.infra.observability.http_server import create_app, healthz, readyz
from app.infra.observability.watchdog import (
    get_watchdog_interval_seconds,
    is_watchdog_available,
    run_watchdog_loop,
    sd_notify,
)


# ---- OFF by default: config ----
def test_observability_default_env_http_disabled() -> None:
    """Without OBS_HTTP_ENABLED, HTTP server must not be started (config reflects OFF)."""
    cfg = load_observability_config(env={})
    assert cfg.obs_http_enabled is False
    assert cfg.otel_enabled is False
    assert cfg.systemd_watchdog_enabled is False


def test_observability_explicit_zero_http_disabled() -> None:
    cfg = load_observability_config(env={"OBS_HTTP_ENABLED": "0"})
    assert cfg.obs_http_enabled is False


def test_observability_enabled_when_set() -> None:
    cfg = load_observability_config(env={"OBS_HTTP_ENABLED": "1"})
    assert cfg.obs_http_enabled is True
    assert cfg.obs_http_host == "127.0.0.1"
    assert cfg.obs_http_port == 8081


def test_observability_http_server_not_started_with_default_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With default ENV, no runner/app is created by main — we verify config alone."""
    monkeypatch.delenv("OBS_HTTP_ENABLED", raising=False)
    monkeypatch.delenv("OBS_HTTP_HOST", raising=False)
    monkeypatch.delenv("OBS_HTTP_PORT", raising=False)
    cfg = load_observability_config()
    assert cfg.obs_http_enabled is False


# ---- Watchdog: disabled = no-op ----
def test_watchdog_disabled_no_op() -> None:
    notified: list[str] = []

    def capture(_msg: str, _env: dict | None = None) -> bool:
        notified.append(_msg)
        return True

    async def run() -> None:
        await asyncio.wait_for(
            run_watchdog_loop(enabled=False, env={}, notify_fn=capture),
            timeout=0.5,
        )

    asyncio.run(run())
    assert notified == []


# ---- Watchdog: enabled but no NOTIFY_SOCKET = no-op ----
def test_watchdog_enabled_no_socket_no_op() -> None:
    notified: list[str] = []

    def capture(_msg: str, _env: dict | None = None) -> bool:
        notified.append(_msg)
        return True

    async def run() -> None:
        task = asyncio.create_task(
            run_watchdog_loop(enabled=True, env={}, notify_fn=capture),
        )
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert notified == []


# ---- Watchdog: enabled + socket present -> notify called ----
def test_watchdog_enabled_with_socket_calls_notify() -> None:
    notified: list[str] = []

    def capture(msg: str, _env: dict | None = None) -> bool:
        notified.append(msg)
        return True

    async def run() -> None:
        task = asyncio.create_task(
            run_watchdog_loop(
                enabled=True,
                env={"NOTIFY_SOCKET": "/run/fake/socket"},
                interval_override_seconds=0.2,
                notify_fn=capture,
            ),
        )
        await asyncio.sleep(0.35)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert any(m == "WATCHDOG=1" for m in notified)


# ---- sd_notify without socket ----
def test_sd_notify_no_socket_returns_false() -> None:
    assert sd_notify("WATCHDOG=1", env={}) is False


def test_is_watchdog_available_no_socket() -> None:
    assert is_watchdog_available(env={}) is False


def test_is_watchdog_available_with_socket() -> None:
    assert is_watchdog_available(env={"NOTIFY_SOCKET": "/run/foo"}) is True


def test_get_watchdog_interval_seconds() -> None:
    assert get_watchdog_interval_seconds(env={}) is None
    assert get_watchdog_interval_seconds(env={"WATCHDOG_USEC": "30000000"}) == 30.0


# ---- HTTP responses: no secrets, no correlation_id ----
def test_healthz_no_secrets_no_correlation_id() -> None:
    async def run() -> None:
        state = {
            "init_complete": True,
            "start_time": 0.0,
            "version": "1.0",
            "last_error_count": 0,
            "metrics_collector": None,
        }
        app = create_app(state)
        req = MagicMock()
        req.app = app
        resp = await healthz(req)
        assert resp.status == 200
        body = (resp.body or b"").decode("utf-8")
        assert "correlation" not in body.lower()
        assert "token" not in body.lower()
        assert "password" not in body.lower()
        assert "secret" not in body.lower()
        assert "env" not in body or "uptime" in body

    asyncio.run(run())


def test_readyz_returns_503_when_init_incomplete() -> None:
    async def run() -> None:
        state = {
            "init_complete": False,
            "start_time": 0.0,
            "version": "1.0",
            "last_error_count": 0,
            "critical_error_count_last_n_minutes": 0,
        }
        app = create_app(state)
        req = MagicMock()
        req.app = app
        resp = await readyz(req)
        assert resp.status == 503

    asyncio.run(run())


def test_readyz_200_when_init_complete_no_errors() -> None:
    async def run() -> None:
        state = {
            "init_complete": True,
            "start_time": 0.0,
            "version": "1.0",
            "last_error_count": 0,
            "critical_error_count_last_n_minutes": 0,
        }
        app = create_app(state)
        req = MagicMock()
        req.app = app
        resp = await readyz(req)
        assert resp.status == 200

    asyncio.run(run())


# ---- Smoke: HTTP server (when enabled) serves /healthz and /metrics ----
def test_smoke_observability_http_healthz_and_metrics() -> None:
    """With OBS enabled, server responds on /healthz and /metrics (smoke)."""
    import aiohttp
    from aiohttp import web

    from app.infra.observability.http_server import create_app
    from app.infra.observability.metrics import MetricsCollector

    async def run() -> None:
        state = {
            "init_complete": True,
            "start_time": 0.0,
            "version": "smoke",
            "last_error_count": 0,
            "critical_error_count_last_n_minutes": 0,
            "metrics_collector": MetricsCollector(),
        }
        app = create_app(state)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        try:
            port = site._server.sockets[0].getsockname()[1]
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{port}/healthz") as r:
                    assert r.status == 200
                    data = await r.json()
                    assert data.get("status") == "ok"
                    assert "uptime_seconds" in data
                async with session.get(f"http://127.0.0.1:{port}/metrics") as r:
                    assert r.status == 200
                    text = await r.text()
                    assert "msb_" in text or "No metrics" in text
        finally:
            await runner.cleanup()

    asyncio.run(run())
