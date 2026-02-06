from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GoogleTokens:
    access_token: str
    refresh_token: str
    expires_at: float | None
    token_type: str | None = None
    scope: str | None = None

    def is_expired(self, *, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        current = now if now is not None else time.time()
        return self.expires_at <= current


class GoogleTokenStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._tokens: dict[str, dict[str, object]] = {}

    @classmethod
    def from_env(cls) -> "GoogleTokenStore":
        path = Path(os.getenv("GOOGLE_TOKENS_PATH", "data/google_tokens.json"))
        store = cls(path)
        store.load()
        return store

    def load(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._tokens = {}
                return
            try:
                with self._path.open("r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except json.JSONDecodeError:
                self._tokens = {}
                return
            tokens = raw.get("tokens") if isinstance(raw, dict) else None
            self._tokens = tokens if isinstance(tokens, dict) else {}

    def get_tokens(self, user_id: int) -> GoogleTokens | None:
        with self._lock:
            entry = self._tokens.get(str(user_id))
            if not isinstance(entry, dict):
                return None
            access_token = entry.get("access_token")
            refresh_token = entry.get("refresh_token")
            expires_at = entry.get("expires_at")
            token_type = entry.get("token_type")
            scope = entry.get("scope")
            if not isinstance(access_token, str) or not isinstance(refresh_token, str):
                return None
            expires_at_value = expires_at if isinstance(expires_at, (int, float)) else None
            return GoogleTokens(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at_value,
                token_type=token_type if isinstance(token_type, str) else None,
                scope=scope if isinstance(scope, str) else None,
            )

    def set_tokens(self, user_id: int, tokens: GoogleTokens) -> None:
        with self._lock:
            self._tokens[str(user_id)] = {
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
                "expires_at": tokens.expires_at,
                "token_type": tokens.token_type,
                "scope": tokens.scope,
                "updated_at": time.time(),
            }
            self._save()

    def update_access_token(
        self,
        user_id: int,
        *,
        access_token: str,
        expires_at: float | None,
        scope: str | None = None,
        token_type: str | None = None,
    ) -> None:
        with self._lock:
            entry = self._tokens.get(str(user_id))
            if not isinstance(entry, dict):
                return
            entry["access_token"] = access_token
            entry["expires_at"] = expires_at
            if scope is not None:
                entry["scope"] = scope
            if token_type is not None:
                entry["token_type"] = token_type
            entry["updated_at"] = time.time()
            self._save()

    def delete_tokens(self, user_id: int) -> None:
        with self._lock:
            self._tokens.pop(str(user_id), None)
            self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tokens": self._tokens, "updated_at": time.time()}
        tmp_path = self._path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(self._path)
