from __future__ import annotations

import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlencode, urljoin

import httpx

from app.stores.google_tokens import GoogleTokens, GoogleTokenStore

LOGGER = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]


@dataclass(frozen=True)
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    public_base_url: str
    redirect_path: str

    @property
    def redirect_uri(self) -> str:
        base = self.public_base_url.rstrip("/")
        path = self.redirect_path if self.redirect_path.startswith("/") else f"/{self.redirect_path}"
        return urljoin(f"{base}/", path.lstrip("/"))


def load_google_oauth_config() -> GoogleOAuthConfig | None:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    public_base_url = os.getenv("PUBLIC_BASE_URL")
    redirect_path = os.getenv("GOOGLE_OAUTH_REDIRECT_PATH", "/oauth2/callback")
    if not client_id or not client_secret or not public_base_url:
        return None
    return GoogleOAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        public_base_url=public_base_url,
        redirect_path=redirect_path,
    )


def build_authorization_url(config: GoogleOAuthConfig, *, state: str) -> str:
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "include_granted_scopes": "true",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


class OAuthStateStore:
    def __init__(self, *, ttl_seconds: int = 600) -> None:
        self._ttl_seconds = ttl_seconds
        self._states: dict[str, tuple[int, float]] = {}
        self._user_index: dict[int, str] = {}

    def issue_state(self, user_id: int) -> str:
        self._cleanup()
        state = secrets.token_urlsafe(24)
        expires_at = time.time() + self._ttl_seconds
        prior_state = self._user_index.get(user_id)
        if prior_state:
            self._states.pop(prior_state, None)
        self._states[state] = (user_id, expires_at)
        self._user_index[user_id] = state
        return state

    def consume_state(self, state: str) -> int | None:
        self._cleanup()
        entry = self._states.pop(state, None)
        if entry is None:
            return None
        user_id, _expires_at = entry
        if self._user_index.get(user_id) == state:
            self._user_index.pop(user_id, None)
        return user_id

    def _cleanup(self) -> None:
        now = time.time()
        expired = [state for state, (_user_id, expires_at) in self._states.items() if expires_at <= now]
        for state in expired:
            user_id, _expires_at = self._states.pop(state, (0, 0))
            if user_id and self._user_index.get(user_id) == state:
                self._user_index.pop(user_id, None)


def exchange_code_for_tokens(config: GoogleOAuthConfig, *, code: str) -> dict[str, object]:
    payload = {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": config.redirect_uri,
    }
    with httpx.Client(timeout=10.0) as client:
        response = client.post(GOOGLE_TOKEN_URL, data=payload)
        response.raise_for_status()
        return response.json()


def refresh_access_token(config: GoogleOAuthConfig, *, refresh_token: str) -> dict[str, object]:
    payload = {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    with httpx.Client(timeout=10.0) as client:
        response = client.post(GOOGLE_TOKEN_URL, data=payload)
        response.raise_for_status()
        return response.json()


def handle_oauth_callback(
    *,
    config: GoogleOAuthConfig,
    token_store: GoogleTokenStore,
    state_store: OAuthStateStore,
    query_params: Mapping[str, str],
) -> tuple[int, str, int | None]:
    """Process the OAuth callback.

    Returns ``(http_status, message, user_id | None)``.
    ``user_id`` is set only on success so the caller can send a Telegram notification.
    """
    if "error" in query_params:
        error_value = query_params.get("error", "")
        description = query_params.get("error_description", "")
        message = f"Google \u0432\u0435\u0440\u043d\u0443\u043b \u043e\u0448\u0438\u0431\u043a\u0443: {error_value}"
        if description:
            message = f"{message}. {description}"
        return 400, message, None
    code = query_params.get("code")
    state = query_params.get("state")
    if not code or not state:
        return 400, "\u041d\u0435 \u0445\u0432\u0430\u0442\u0430\u0435\u0442 \u043f\u0430\u0440\u0430\u043c\u0435\u0442\u0440\u043e\u0432 OAuth (code/state).", None
    user_id = state_store.consume_state(state)
    if user_id is None:
        return 400, "\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u0438\u043b\u0438 \u043f\u0440\u043e\u0441\u0440\u043e\u0447\u0435\u043d\u043d\u044b\u0439 state.", None
    try:
        token_payload = exchange_code_for_tokens(config, code=code)
    except httpx.HTTPError:
        LOGGER.exception("OAuth token exchange failed for user_id=%s", user_id)
        return 500, "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0431\u043c\u0435\u043d\u044f\u0442\u044c \u043a\u043e\u0434 \u043d\u0430 \u0442\u043e\u043a\u0435\u043d\u044b. \u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 OAuth.", None
    access_token = token_payload.get("access_token")
    refresh_token = token_payload.get("refresh_token")
    expires_in = token_payload.get("expires_in")
    if not isinstance(access_token, str) or not isinstance(refresh_token, str):
        LOGGER.error("OAuth exchange missing tokens for user_id=%s", user_id)
        return 500, "Google \u043d\u0435 \u0432\u0435\u0440\u043d\u0443\u043b refresh_token. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u0437\u0430\u043d\u043e\u0432\u043e.", None
    expires_at = time.time() + float(expires_in) if isinstance(expires_in, (int, float)) else None
    token_store.set_tokens(
        user_id,
        GoogleTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            token_type=token_payload.get("token_type") if isinstance(token_payload.get("token_type"), str) else None,
            scope=token_payload.get("scope") if isinstance(token_payload.get("scope"), str) else None,
        ),
    )
    return 200, "\u2705 \u041a\u0430\u043b\u0435\u043d\u0434\u0430\u0440\u044c \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0451\u043d. \u041c\u043e\u0436\u043d\u043e \u0432\u0435\u0440\u043d\u0443\u0442\u044c\u0441\u044f \u0432 Telegram.", user_id
