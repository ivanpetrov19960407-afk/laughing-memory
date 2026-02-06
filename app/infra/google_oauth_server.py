from __future__ import annotations

import logging
import os
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
        path_only = urlparse(self.path).path
        LOGGER.info("OAuthServer: %s %s", self.command, path_only)

    def _handle_start(self, query: str) -> None:
        params = parse_qs(query)
        state = params.get("state", [None])[0]
        if not state:
            self._send_text(HTTPStatus.BAD_REQUEST, "state обязателен.")
            return
        if not state.isdigit():
            self._send_text(HTTPStatus.BAD_REQUEST, "state должен быть числом.")
            return
        user_id = int(state)
        self.server.state_store.register_state(user_id=user_id, state=state)
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
        self._send_html(HTTPStatus(status), message)

    def _send_text(self, status: HTTPStatus, message: str) -> None:
        payload = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, status: HTTPStatus, message: str) -> None:
        payload = message.encode("utf-8")
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
    state_store = OAuthStateStore()
    bind_host = "127.0.0.1"
    if host != bind_host:
        LOGGER.warning("OAuth server host override (%s) ignored; binding to %s", host, bind_host)
    server = OAuthHTTPServer(
        (bind_host, port),
        GoogleOAuthHandler,
        config=config,
        token_store=token_store,
        state_store=state_store,
    )
    thread = threading.Thread(target=server.serve_forever, name="google-oauth-server", daemon=True)
    thread.start()
    LOGGER.info("Google OAuth server started on %s:%s", bind_host, port)
    return server


def run_google_oauth_server(
    *,
    host: str,
    port: int,
    config: GoogleOAuthConfig,
    token_store: GoogleTokenStore,
) -> None:
    state_store = OAuthStateStore()
    bind_host = "127.0.0.1"
    if host != bind_host:
        LOGGER.warning("OAuth server host override (%s) ignored; binding to %s", host, bind_host)
    server = OAuthHTTPServer(
        (bind_host, port),
        GoogleOAuthHandler,
        config=config,
        token_store=token_store,
        state_store=state_store,
    )
    LOGGER.info("Google OAuth server listening on %s:%s", bind_host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("OAuth server остановлен.")
    finally:
        server.server_close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from app.infra.google_oauth import load_google_oauth_config

    config = load_google_oauth_config()
    if config is None:
        LOGGER.error("Google OAuth config missing. Set GOOGLE_OAUTH_CLIENT_ID/SECRET and PUBLIC_BASE_URL.")
        raise SystemExit(1)
    token_store = GoogleTokenStore.from_env()
    port = int(os.getenv("GOOGLE_OAUTH_SERVER_PORT", "8000"))
    run_google_oauth_server(host="127.0.0.1", port=port, config=config, token_store=token_store)


if __name__ == "__main__":
    main()
