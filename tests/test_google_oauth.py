from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.infra.google_oauth import (
    GOOGLE_AUTH_URL,
    GOOGLE_SCOPES,
    GoogleOAuthConfig,
    OAuthStateStore,
    build_authorization_url,
    handle_oauth_callback,
)
from app.stores.google_tokens import GoogleTokenStore, GoogleTokens


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides: str) -> GoogleOAuthConfig:
    defaults = {
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "public_base_url": "https://example.com",
        "redirect_path": "/oauth2/callback",
    }
    defaults.update(overrides)
    return GoogleOAuthConfig(**defaults)


def _make_store(tmp_path: Path) -> GoogleTokenStore:
    return GoogleTokenStore(tmp_path / "tokens.db")


# ---------------------------------------------------------------------------
# 1. Auth URL generation
# ---------------------------------------------------------------------------


def test_build_authorization_url_contains_state() -> None:
    config = _make_config()
    url = build_authorization_url(config, state="abc123")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert params["state"] == ["abc123"]


def test_build_authorization_url_contains_redirect_uri() -> None:
    config = _make_config()
    url = build_authorization_url(config, state="s1")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert params["redirect_uri"] == ["https://example.com/oauth2/callback"]


def test_build_authorization_url_contains_scopes() -> None:
    config = _make_config()
    url = build_authorization_url(config, state="s1")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    expected_scope = " ".join(GOOGLE_SCOPES)
    assert params["scope"] == [expected_scope]


def test_build_authorization_url_base() -> None:
    config = _make_config()
    url = build_authorization_url(config, state="s1")
    assert url.startswith(GOOGLE_AUTH_URL)


def test_redirect_uri_from_public_base_url() -> None:
    config = _make_config(
        public_base_url="https://vanekpetrov1997.fvds.ru",
        redirect_path="/oauth2/callback",
    )
    assert config.redirect_uri == "https://vanekpetrov1997.fvds.ru/oauth2/callback"


def test_redirect_uri_strips_trailing_slash() -> None:
    config = _make_config(public_base_url="https://example.com/")
    assert config.redirect_uri == "https://example.com/oauth2/callback"


# ---------------------------------------------------------------------------
# 2. OAuthStateStore
# ---------------------------------------------------------------------------


def test_state_store_issue_and_consume() -> None:
    store = OAuthStateStore()
    state = store.issue_state(42)
    assert isinstance(state, str)
    assert len(state) > 10
    user_id = store.consume_state(state)
    assert user_id == 42


def test_state_store_consume_twice_returns_none() -> None:
    store = OAuthStateStore()
    state = store.issue_state(42)
    store.consume_state(state)
    assert store.consume_state(state) is None


def test_state_store_invalid_state_returns_none() -> None:
    store = OAuthStateStore()
    assert store.consume_state("bogus") is None


# ---------------------------------------------------------------------------
# 3. SQLite token store: save / read / delete
# ---------------------------------------------------------------------------


def test_token_store_set_and_get(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_tokens(42, GoogleTokens(
        access_token="acc",
        refresh_token="ref",
        expires_at=9999999999.0,
    ))
    tokens = store.get_tokens(42)
    assert tokens is not None
    assert tokens.refresh_token == "ref"
    assert tokens.access_token == "acc"
    assert tokens.expires_at == 9999999999.0


def test_token_store_get_missing_returns_none(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.get_tokens(999) is None


def test_token_store_delete(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_tokens(42, GoogleTokens(
        access_token="a", refresh_token="r", expires_at=None,
    ))
    assert store.get_tokens(42) is not None
    store.delete_tokens(42)
    assert store.get_tokens(42) is None


def test_token_store_update_access_token(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_tokens(42, GoogleTokens(
        access_token="old", refresh_token="ref", expires_at=100.0,
    ))
    store.update_access_token(42, access_token="new", expires_at=200.0)
    tokens = store.get_tokens(42)
    assert tokens is not None
    assert tokens.access_token == "new"
    assert tokens.expires_at == 200.0
    assert tokens.refresh_token == "ref"


def test_token_store_overwrite_preserves_created_at(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.set_tokens(42, GoogleTokens(
        access_token="a1", refresh_token="r1", expires_at=None,
    ))
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "tokens.db"))
    created1 = conn.execute(
        "SELECT created_at FROM google_tokens WHERE user_id = '42'"
    ).fetchone()[0]
    conn.close()

    store.set_tokens(42, GoogleTokens(
        access_token="a2", refresh_token="r2", expires_at=None,
    ))
    conn = sqlite3.connect(str(tmp_path / "tokens.db"))
    created2 = conn.execute(
        "SELECT created_at FROM google_tokens WHERE user_id = '42'"
    ).fetchone()[0]
    conn.close()
    assert created1 == created2


def test_token_store_load_is_noop(tmp_path: Path) -> None:
    """load() should not raise and is a no-op for SQLite."""
    store = _make_store(tmp_path)
    store.load()  # should not raise


def test_token_store_from_env(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "custom.db"
    monkeypatch.setenv("GOOGLE_TOKENS_PATH", str(db_path))
    store = GoogleTokenStore.from_env()
    store.set_tokens(1, GoogleTokens(
        access_token="a", refresh_token="r", expires_at=None,
    ))
    assert store.get_tokens(1) is not None


# ---------------------------------------------------------------------------
# 4. OAuth callback: code→token exchange (mocked)
# ---------------------------------------------------------------------------


def test_oauth_callback_saves_tokens(tmp_path: Path, monkeypatch) -> None:
    store = _make_store(tmp_path)
    config = _make_config()
    state_store = OAuthStateStore()
    state = state_store.issue_state(42)

    def fake_exchange(*args, **kwargs) -> dict[str, object]:
        return {"access_token": "acc", "refresh_token": "ref", "expires_in": 3600}

    monkeypatch.setattr("app.infra.google_oauth.exchange_code_for_tokens", fake_exchange)

    status, _message, user_id = handle_oauth_callback(
        config=config,
        token_store=store,
        state_store=state_store,
        query_params={"code": "code-1", "state": state},
    )

    assert status == 200
    assert user_id == 42
    tokens = store.get_tokens(42)
    assert tokens is not None
    assert tokens.refresh_token == "ref"


def test_oauth_callback_invalid_state(tmp_path: Path, monkeypatch) -> None:
    store = _make_store(tmp_path)
    config = _make_config()
    state_store = OAuthStateStore()

    status, message, user_id = handle_oauth_callback(
        config=config,
        token_store=store,
        state_store=state_store,
        query_params={"code": "code-1", "state": "bad-state"},
    )

    assert status == 400
    assert user_id is None


def test_oauth_callback_missing_code(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    config = _make_config()
    state_store = OAuthStateStore()
    state = state_store.issue_state(42)

    status, _message, user_id = handle_oauth_callback(
        config=config,
        token_store=store,
        state_store=state_store,
        query_params={"state": state},  # no code
    )

    assert status == 400
    assert user_id is None


def test_oauth_callback_google_error(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    config = _make_config()
    state_store = OAuthStateStore()

    status, message, user_id = handle_oauth_callback(
        config=config,
        token_store=store,
        state_store=state_store,
        query_params={"error": "access_denied", "error_description": "User denied"},
    )

    assert status == 400
    assert "access_denied" in message
    assert user_id is None


def test_oauth_callback_exchange_failure(tmp_path: Path, monkeypatch) -> None:
    store = _make_store(tmp_path)
    config = _make_config()
    state_store = OAuthStateStore()
    state = state_store.issue_state(42)

    import httpx

    def failing_exchange(*args, **kwargs) -> dict[str, object]:
        raise httpx.HTTPError("network error")

    monkeypatch.setattr("app.infra.google_oauth.exchange_code_for_tokens", failing_exchange)

    status, _message, user_id = handle_oauth_callback(
        config=config,
        token_store=store,
        state_store=state_store,
        query_params={"code": "code-1", "state": state},
    )

    assert status == 500
    assert user_id is None


def test_oauth_callback_missing_refresh_token(tmp_path: Path, monkeypatch) -> None:
    store = _make_store(tmp_path)
    config = _make_config()
    state_store = OAuthStateStore()
    state = state_store.issue_state(42)

    def incomplete_exchange(*args, **kwargs) -> dict[str, object]:
        return {"access_token": "acc"}  # no refresh_token

    monkeypatch.setattr("app.infra.google_oauth.exchange_code_for_tokens", incomplete_exchange)

    status, _message, user_id = handle_oauth_callback(
        config=config,
        token_store=store,
        state_store=state_store,
        query_params={"code": "code-1", "state": state},
    )

    assert status == 500
    assert user_id is None
