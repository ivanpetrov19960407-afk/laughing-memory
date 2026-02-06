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

_SUCCESS_HTML = """\
<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"><title>Google Calendar</title></head>
<body style="font-family:sans-serif;text-align:center;padding:60px;">
<h2>&#9989; Google Calendar &#1087;&#1086;&#1076;&#1082;&#1083;&#1102;&#1095;&#1105;&#1085;!</h2>
<p>&#1052;&#1086;&#1078;&#1085;&#1086; &#1074;&#1077;&#1088;&#1085;&#1091;&#1090;&#1100;&#1089;&#1103; &#1074; Telegram.</p>
</body>
</html>
"""

_ERROR_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"><title>&#1054;&#1096;&#1080;&#1073;&#1082;&#1072;</title></head>
<body style="font-family:sans-serif;text-align:center;padding:60px;">
<h2>&#1054;&#1096;&#1080;&#1073;&#1082;&#1072;</h2>
<p>{message}</p>
</body>
</html>
"""


class OAuthHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        config: GoogleOAuthConfig,
        token_store: GoogleTokenStore,
        state_store: OAuthStateStore,
        bot_token: str | None = None,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.config = config
        self.token_store = token_store
        self.state_store = state_store
        self.bot_token = bot_token


class GoogleOAuthHandler(BaseHTTPRequestHandler):
    server: OAuthHTTPServer

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._handle_health()
            return
        if parsed.path == "/oauth2/start":
            self._handle_start(parsed.query)
            return
        if parsed.path == "/oauth2/callback":
            self._handle_callback(parsed.query)
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()
        self.wfile.write(b"Not found")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        LOGGER.info("OAuthServer: " + format, *args)

    # ------------------------------------------------------------------
    # /health
    # ------------------------------------------------------------------

    def _handle_health(self) -> None:
        self._send_text(HTTPStatus.OK, "ok")

    # ------------------------------------------------------------------
    # /oauth2/start?state=<user_id>
    # ------------------------------------------------------------------

    def _handle_start(self, query: str) -> None:
        params = parse_qs(query)
        state_raw = params.get("state", [None])[0]
        if not state_raw or not state_raw.strip():
            self._send_html(HTTPStatus.BAD_REQUEST, _ERROR_HTML_TEMPLATE.format(message="state обязателен."))
            return
        user_id_str = state_raw.strip()
        if not user_id_str.isdigit():
            self._send_html(HTTPStatus.BAD_REQUEST, _ERROR_HTML_TEMPLATE.format(message="state должен быть числовым user_id."))
            return
        user_id = int(user_id_str)
        oauth_state = self.server.state_store.issue_state(user_id)
        redirect_url = build_authorization_url(self.server.config, state=oauth_state)
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", redirect_url)
        self.end_headers()

    # ------------------------------------------------------------------
    # /oauth2/callback?code=...&state=...
    # ------------------------------------------------------------------

    def _handle_callback(self, query: str) -> None:
        params = parse_qs(query)
        flat = {key: values[0] for key, values in params.items() if values}
        status, message, user_id = handle_oauth_callback(
            config=self.server.config,
            token_store=self.server.token_store,
            state_store=self.server.state_store,
            query_params=flat,
        )
        if status == 200:
            self._send_html(HTTPStatus.OK, _SUCCESS_HTML)
            # Best-effort Telegram notification
            self._try_telegram_notify(user_id)
        else:
            self._send_html(HTTPStatus(status), _ERROR_HTML_TEMPLATE.format(message=message))

    def _try_telegram_notify(self, user_id: int | None) -> None:
        """Best-effort: send 'Google Calendar подключён' to user via Telegram."""
        if user_id is None:
            return
        bot_token = self.server.bot_token
        if not bot_token:
            return
        try:
            import httpx

            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": user_id,
                "text": "\u2705 Google Calendar \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0451\u043d!",
            }
            with httpx.Client(timeout=5.0) as client:
                client.post(url, json=payload)
        except Exception:
            LOGGER.debug("Failed to send Telegram notification to user_id=%s", user_id, exc_info=True)

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

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
    host: str = "127.0.0.1",
    port: int = 8000,
    config: GoogleOAuthConfig,
    token_store: GoogleTokenStore,
    bot_token: str | None = None,
) -> OAuthHTTPServer:
    state_store = OAuthStateStore()
    server = OAuthHTTPServer(
        (host, port),
        GoogleOAuthHandler,
        config=config,
        token_store=token_store,
        state_store=state_store,
        bot_token=bot_token,
    )
    thread = threading.Thread(target=server.serve_forever, name="google-oauth-server", daemon=True)
    thread.start()
    LOGGER.info("Google OAuth server started on %s:%s", host, port)
    return server
