from __future__ import annotations

import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.infra.google_oauth import GoogleOAuthConfig, OAuthStateStore, build_authorization_url, handle_oauth_callback
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
        state_store: OAuthStateStore,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.config = config
        self.token_store = token_store
        self.state_store = state_store


class GoogleOAuthHandler(BaseHTTPRequestHandler):
    server: OAuthHTTPServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/oauth/google/start":
            self._handle_start(parsed.query)
            return
        if parsed.path == "/oauth/google/callback":
            self._handle_callback(parsed.query)
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()
        self.wfile.write(b"Not found")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        LOGGER.info("OAuthServer: " + format, *args)

    def _handle_start(self, query: str) -> None:
        params = parse_qs(query)
        user_id_raw = params.get("user_id", [None])[0]
        if not user_id_raw or not user_id_raw.isdigit():
            self._send_text(HTTPStatus.BAD_REQUEST, "user_id обязателен.")
            return
        user_id = int(user_id_raw)
        state = self.server.state_store.issue_state(user_id)
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
            state_store=self.server.state_store,
            query_params=flat,
        )
        self._send_text(HTTPStatus(status), message)

    def _send_text(self, status: HTTPStatus, message: str) -> None:
        payload = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
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
    state_store = OAuthStateStore()
    server = OAuthHTTPServer(
        (host, port),
        GoogleOAuthHandler,
        config=config,
        token_store=token_store,
        state_store=state_store,
    )
    thread = threading.Thread(target=server.serve_forever, name="google-oauth-server", daemon=True)
    thread.start()
    LOGGER.info("Google OAuth server started on %s:%s", host, port)
    return server
