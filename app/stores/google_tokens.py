from __future__ import annotations

import os
import sqlite3
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
        self._access_cache: dict[str, GoogleTokens] = {}
        self._init_db()

    @classmethod
    def from_env(cls) -> "GoogleTokenStore":
        path = Path(os.getenv("GOOGLE_TOKENS_PATH", "data/google_tokens.db"))
        return cls(path)

    def load(self) -> None:
        self._init_db()

    def get_tokens(self, user_id: int) -> GoogleTokens | None:
        with self._lock:
            refresh_token = self._get_refresh_token(str(user_id))
            if refresh_token is None:
                return None
            cached = self._access_cache.get(str(user_id))
            if cached is not None:
                return GoogleTokens(
                    access_token=cached.access_token,
                    refresh_token=refresh_token,
                    expires_at=cached.expires_at,
                    token_type=cached.token_type,
                    scope=cached.scope,
                )
            return GoogleTokens(
                access_token="",
                refresh_token=refresh_token,
                expires_at=0.0,
                token_type=None,
                scope=None,
            )

    def set_tokens(self, user_id: int, tokens: GoogleTokens) -> None:
        now = time.time()
        with self._lock:
            self._upsert_refresh_token(str(user_id), tokens.refresh_token, now)
            self._access_cache[str(user_id)] = tokens

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
            refresh_token = self._get_refresh_token(str(user_id))
            if refresh_token is None:
                return
            self._access_cache[str(user_id)] = GoogleTokens(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                token_type=token_type,
                scope=scope,
            )

    def delete_tokens(self, user_id: int) -> None:
        with self._lock:
            self._delete_refresh_token(str(user_id))
            self._access_cache.pop(str(user_id), None)

    def _init_db(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS google_tokens (
                    user_id TEXT PRIMARY KEY,
                    refresh_token TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def _get_refresh_token(self, user_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT refresh_token FROM google_tokens WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row[0] if row else None

    def _upsert_refresh_token(self, user_id: str, refresh_token: str, now: float) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM google_tokens WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE google_tokens SET refresh_token = ?, updated_at = ? WHERE user_id = ?",
                    (refresh_token, now, user_id),
                )
            else:
                conn.execute(
                    "INSERT INTO google_tokens (user_id, refresh_token, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (user_id, refresh_token, now, now),
                )
            conn.commit()

    def _delete_refresh_token(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM google_tokens WHERE user_id = ?", (user_id,))
            conn.commit()
