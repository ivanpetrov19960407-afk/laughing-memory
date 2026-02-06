from __future__ import annotations

from pathlib import Path

from app.infra.google_oauth import GoogleOAuthConfig, handle_oauth_callback
from app.stores.google_tokens import GoogleTokenStore


def test_oauth_callback_saves_tokens(tmp_path, monkeypatch) -> None:
    tokens_path = tmp_path / "google_tokens.json"
    store = GoogleTokenStore(Path(tokens_path))
    store.load()
    config = GoogleOAuthConfig(
        client_id="client-id",
        client_secret="client-secret",
        public_base_url="http://localhost:8080",
        redirect_path="/oauth2/callback",
    )
    state = "42"

    def fake_exchange(*args, **kwargs) -> dict[str, object]:
        return {"access_token": "acc", "refresh_token": "ref", "expires_in": 3600}

    monkeypatch.setattr("app.infra.google_oauth.exchange_code_for_tokens", fake_exchange)

    status, _message = handle_oauth_callback(
        config=config,
        token_store=store,
        query_params={"code": "code-1", "state": state},
    )

    assert status == 200
    tokens = store.get_tokens(42)
    assert tokens is not None
    assert tokens.refresh_token == "ref"
