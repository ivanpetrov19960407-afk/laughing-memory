from __future__ import annotations

from pathlib import Path

from app.stores.google_tokens import GoogleTokenStore, GoogleTokens


def test_google_token_store_persists_refresh_token(tmp_path) -> None:
    db_path = tmp_path / "google_tokens.db"
    store = GoogleTokenStore(Path(db_path))
    store.set_tokens(
        123,
        GoogleTokens(
            access_token="access",
            refresh_token="refresh",
            expires_at=None,
            token_type=None,
            scope=None,
        ),
    )

    store_reloaded = GoogleTokenStore(Path(db_path))
    tokens = store_reloaded.get_tokens(123)

    assert tokens is not None
    assert tokens.refresh_token == "refresh"
