from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from app.infra.google_oauth import GOOGLE_AUTH_URL, GoogleOAuthConfig
from app.infra.google_oauth_server import start_google_oauth_server
from app.stores.google_tokens import GoogleTokenStore, GoogleTokens


def test_oauth2_start_redirect_contains_state_redirect_uri_scopes(tmp_path) -> None:
    store = GoogleTokenStore(Path(tmp_path / "tokens.db"))
    store.load()
    config = GoogleOAuthConfig(
        client_id="client-id",
        client_secret="client-secret",
        public_base_url="https://vanekpetrov1997.fvds.ru",
        redirect_path="/oauth2/callback",
    )
    server = start_google_oauth_server(host="127.0.0.1", port=0, config=config, token_store=store)
    try:
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        response = httpx.get(f"{base}/oauth2/start?state=123", follow_redirects=False, timeout=5.0)
        assert response.status_code == 302
        location = response.headers.get("Location") or ""
        assert location.startswith(GOOGLE_AUTH_URL)
        parsed = urlparse(location)
        params = parse_qs(parsed.query)
        assert params["state"] == ["123"]
        assert params["redirect_uri"] == ["https://vanekpetrov1997.fvds.ru/oauth2/callback"]
        scope = " ".join(params.get("scope") or [])
        assert "https://www.googleapis.com/auth/calendar" in scope
    finally:
        server.shutdown()
        server.server_close()


def test_oauth2_callback_exchanges_code_and_saves_refresh_token(tmp_path, monkeypatch) -> None:
    store = GoogleTokenStore(Path(tmp_path / "tokens.db"))
    store.load()
    config = GoogleOAuthConfig(
        client_id="client-id",
        client_secret="client-secret",
        public_base_url="https://vanekpetrov1997.fvds.ru",
        redirect_path="/oauth2/callback",
    )

    def fake_exchange(*args, **kwargs) -> dict[str, object]:
        return {"access_token": "acc-1", "refresh_token": "ref-1", "expires_in": 3600}

    monkeypatch.setattr("app.infra.google_oauth.exchange_code_for_tokens", fake_exchange)
    server = start_google_oauth_server(host="127.0.0.1", port=0, config=config, token_store=store)
    try:
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        response = httpx.get(f"{base}/oauth2/callback?code=code-1&state=123", timeout=5.0)
        assert response.status_code == 200
        assert "text/html" in (response.headers.get("Content-Type") or "")
        assert "Готово" in response.text
        tokens = store.get_tokens(123)
        assert tokens is not None
        assert tokens.refresh_token == "ref-1"
    finally:
        server.shutdown()
        server.server_close()


def test_google_token_store_persists_refresh_token(tmp_path) -> None:
    db_path = Path(tmp_path / "tokens.db")
    store1 = GoogleTokenStore(db_path)
    store1.load()
    store1.set_tokens(1, GoogleTokens(refresh_token="refresh-1", access_token="acc-1", expires_at=123.0))

    store2 = GoogleTokenStore(db_path)
    store2.load()
    tokens = store2.get_tokens(1)
    assert tokens is not None
    assert tokens.refresh_token == "refresh-1"

