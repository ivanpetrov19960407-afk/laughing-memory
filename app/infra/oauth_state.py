from __future__ import annotations

import hmac
import time
from dataclasses import dataclass
from hashlib import sha256


@dataclass
class SignedOAuthState:
    user_id: int
    issued_at: int


class SignedStateManager:
    def __init__(self, *, secret: str, ttl_seconds: int = 600) -> None:
        self._secret = secret.encode("utf-8")
        self._ttl_seconds = ttl_seconds

    def issue(self, user_id: int) -> str:
        issued_at = int(time.time())
        body = f"{user_id}:{issued_at}"
        signature = self._sign(body)
        return f"{body}:{signature}"

    def consume(self, state: str) -> SignedOAuthState | None:
        parts = state.split(":")
        if len(parts) != 3:
            return None
        user_id_raw, issued_raw, signature = parts
        if not user_id_raw.isdigit() or not issued_raw.isdigit():
            return None
        body = f"{user_id_raw}:{issued_raw}"
        if not hmac.compare_digest(self._sign(body), signature):
            return None
        issued_at = int(issued_raw)
        if issued_at + self._ttl_seconds < int(time.time()):
            return None
        return SignedOAuthState(user_id=int(user_id_raw), issued_at=issued_at)

    def _sign(self, value: str) -> str:
        return hmac.new(self._secret, value.encode("utf-8"), sha256).hexdigest()
