from __future__ import annotations

import logging
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

from app.infra.google_oauth import GoogleOAuthConfig, build_authorization_url, handle_oauth_callback
from app.stores.google_tokens import GoogleTokenStore

LOGGER = logging.getLogger(__name__)


class OAuthHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        config: GoogleOAuthConfig,
        token_store: GoogleTokenStore,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.config = config
        self.token_store = token_store


class GoogleOAuthHandler(BaseHTTPRequestHandler):
    server: OAuthHTTPServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_text(HTTPStatus.OK, "ok")
            return
        if parsed.path in {"/oauth2/start", "/oauth/google/start"}:
            self._handle_start(parsed.query)
            return
        if parsed.path in {"/oauth2/callback", "/oauth/google/callback"}:
            self._handle_callback(parsed.query)
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()
        self.wfile.write(b"Not found")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        # Never log query parameters (they may contain OAuth codes).
        parsed = urlparse(self.path)
        LOGGER.info("OAuthWeb: method=%s path=%s", self.command, parsed.path)

    def _handle_start(self, query: str) -> None:
        params = parse_qs(query)
        state = params.get("state", [None])[0]
        if not state:
            self._send_text(HTTPStatus.BAD_REQUEST, "state обязателен.")
            return
        if not state.isdigit():
            self._send_text(HTTPStatus.BAD_REQUEST, "state должен быть user_id.")
            return
        redirect_url = build_authorization_url(self.server.config, state=state)
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", redirect_url)
        self.end_headers()

    def _handle_callback(self, query: str) -> None:
        params = parse_qs(query)
        flat = {key: values[0] for key, values in params.items() if values}
        status, message = handle_oauth_callback(
            config=self.server.config,
            token_store=self.server.token_store,
            query_params=flat,
        )
        if status == 200:
            _notify_telegram_connected_best_effort(flat.get("state"))
            self._send_html(
                HTTPStatus.OK,
                "<html><head><meta charset='utf-8'></head>"
                "<body><h3>Готово</h3><p>Google Calendar подключён. Можно вернуться в Telegram.</p></body></html>",
            )
            return
        self._send_text(HTTPStatus(status), message)

    def _send_text(self, status: HTTPStatus, message: str) -> None:
        payload = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, status: HTTPStatus, html: str) -> None:
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_google_oauth_server(
    *,
    host: str,
    port: int,
    config: GoogleOAuthConfig,
    token_store: GoogleTokenStore,
) -> OAuthHTTPServer:
    if host != "127.0.0.1":
        LOGGER.warning("OAuth web server host overridden to 127.0.0.1 (was %s)", host)
        host = "127.0.0.1"
    server = OAuthHTTPServer(
        (host, port),
        GoogleOAuthHandler,
        config=config,
        token_store=token_store,
    )
    thread = threading.Thread(target=server.serve_forever, name="google-oauth-server", daemon=True)
    thread.start()
    LOGGER.info("Google OAuth server started on %s:%s", host, port)
    return server


def run_google_oauth_server(
    *,
    host: str,
    port: int,
    config: GoogleOAuthConfig,
    token_store: GoogleTokenStore,
) -> None:
    if host != "127.0.0.1":
        LOGGER.warning("OAuth web server host overridden to 127.0.0.1 (was %s)", host)
        host = "127.0.0.1"
    server = OAuthHTTPServer(
        (host, port),
        GoogleOAuthHandler,
        config=config,
        token_store=token_store,
    )
    LOGGER.info("Google OAuth server listening on %s:%s", host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _notify_telegram_connected_best_effort(state: str | None) -> None:
    """
    Best-effort notification to Telegram user that OAuth is connected.
    If notification fails, callback must still return 200.
    """
    if not state or not state.isdigit():
        return
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        return
    chat_id = int(state)
    try:
        with httpx.Client(timeout=5.0) as client:
            client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": str(chat_id), "text": "Google Calendar подключён"},
            )
    except Exception:
        # Silent best-effort
        return
