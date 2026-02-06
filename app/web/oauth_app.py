from __future__ import annotations

import os
import time

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from app.infra.google_oauth import build_authorization_url, exchange_code_for_tokens, load_google_oauth_config
from app.infra.oauth_state import SignedStateManager
from app.infra.telegram import send_telegram_message
from app.stores.google_tokens import GoogleTokenStore, GoogleTokens

app = FastAPI()


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "ok"


@app.get("/oauth2/start")
def oauth_start(state: str = Query(...)) -> RedirectResponse:
    config = load_google_oauth_config()
    if config is None:
        raise HTTPException(status_code=500, detail="OAuth not configured")
    if not state.isdigit():
        raise HTTPException(status_code=400, detail="state обязателен")
    user_id = int(state)
    signer = SignedStateManager(secret=config.client_secret)
    signed_state = signer.issue(user_id)
    auth_url = build_authorization_url(config, state=signed_state)
    return RedirectResponse(url=auth_url, status_code=302)


@app.get("/oauth2/callback", response_class=HTMLResponse)
def oauth_callback(
    background_tasks: BackgroundTasks,
    code: str | None = None,
    state: str | None = None,
) -> HTMLResponse:
    config = load_google_oauth_config()
    if config is None:
        raise HTTPException(status_code=500, detail="OAuth not configured")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Не хватает параметров OAuth (code/state).")
    signer = SignedStateManager(secret=config.client_secret)
    signed = signer.consume(state)
    if signed is None:
        raise HTTPException(status_code=400, detail="Неверный или просроченный state.")
    token_payload = exchange_code_for_tokens(config, code=code)
    refresh_token = token_payload.get("refresh_token")
    access_token = token_payload.get("access_token")
    expires_in = token_payload.get("expires_in")
    if not isinstance(refresh_token, str) or not isinstance(access_token, str):
        raise HTTPException(status_code=500, detail="Google не вернул refresh_token.")
    expires_at = None
    if isinstance(expires_in, (int, float)):
        expires_at = time.time() + float(expires_in)
    store = GoogleTokenStore.from_env()
    store.set_tokens(
        signed.user_id,
        GoogleTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            token_type=token_payload.get("token_type") if isinstance(token_payload.get("token_type"), str) else None,
            scope=token_payload.get("scope") if isinstance(token_payload.get("scope"), str) else None,
        ),
    )
    bot_token = os.getenv("BOT_TOKEN", "")
    if bot_token:
        background_tasks.add_task(
            send_telegram_message,
            bot_token=bot_token,
            chat_id=signed.user_id,
            text="Google Calendar подключён",
        )
    return HTMLResponse(content="<html><body>Готово, можно вернуться в Telegram.</body></html>")
