from __future__ import annotations

import logging
import os

from app.infra.google_oauth import load_google_oauth_config
from app.infra.google_oauth_server import run_google_oauth_server
from app.stores.google_tokens import GoogleTokenStore


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_google_oauth_config()
    if config is None:
        raise RuntimeError("Google OAuth is not configured (GOOGLE_OAUTH_CLIENT_ID/SECRET/PUBLIC_BASE_URL).")
    token_store = GoogleTokenStore.from_env()
    port_raw = os.getenv("OAUTH_WEB_PORT", "8000").strip()
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid OAUTH_WEB_PORT: {port_raw!r}") from exc
    run_google_oauth_server(
        host="127.0.0.1",
        port=port,
        config=config,
        token_store=token_store,
    )


if __name__ == "__main__":
    main()

