#!/usr/bin/env python3
"""Standalone Google Calendar OAuth web server.

Intended to be run as a separate systemd service behind nginx.
Listens on 127.0.0.1:$OAUTH_SERVER_PORT (default 8000).

Required environment variables:
    GOOGLE_OAUTH_CLIENT_ID
    GOOGLE_OAUTH_CLIENT_SECRET
    PUBLIC_BASE_URL           (e.g. https://vanekpetrov1997.fvds.ru)

Optional:
    OAUTH_SERVER_PORT         (default 8000)
    GOOGLE_OAUTH_REDIRECT_PATH (default /oauth2/callback)
    GOOGLE_TOKENS_PATH        (default data/google_tokens.db)
    BOT_TOKEN                 (for best-effort Telegram notification)
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Ensure repository root is on PYTHONPATH
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.infra.google_oauth import GoogleOAuthConfig
from app.infra.google_oauth_server import start_google_oauth_server
from app.stores.google_tokens import GoogleTokenStore


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Load dotenv if available
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    public_base_url = os.getenv("PUBLIC_BASE_URL")

    if not client_id or not client_secret or not public_base_url:
        logging.error(
            "Missing required env: GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, PUBLIC_BASE_URL"
        )
        sys.exit(1)

    redirect_path = os.getenv("GOOGLE_OAUTH_REDIRECT_PATH", "/oauth2/callback")
    tokens_path = Path(os.getenv("GOOGLE_TOKENS_PATH", "data/google_tokens.db"))
    bot_token = os.getenv("BOT_TOKEN")
    port = int(os.getenv("OAUTH_SERVER_PORT", "8000"))

    config = GoogleOAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        public_base_url=public_base_url,
        redirect_path=redirect_path,
    )
    token_store = GoogleTokenStore(tokens_path)

    server = start_google_oauth_server(
        host="127.0.0.1",
        port=port,
        config=config,
        token_store=token_store,
        bot_token=bot_token,
    )

    logging.info("OAuth server running on 127.0.0.1:%s – press Ctrl+C to stop", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down OAuth server")
        server.shutdown()


if __name__ == "__main__":
    main()
