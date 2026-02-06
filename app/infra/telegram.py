from __future__ import annotations

import httpx


def send_telegram_message(*, bot_token: str, chat_id: int, text: str) -> None:
    if not bot_token:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    with httpx.Client(timeout=10.0) as client:
        client.post(url, json=payload)
