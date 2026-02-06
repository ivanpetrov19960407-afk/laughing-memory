from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class GoogleTokens:
    refresh_token: str
    access_token: str | None = None
    expires_at: float | None = None
    token_type: str | None = None
    scope: str | None = None

    def is_expired(self, *, now: float | None = None) -> bool:
        if not self.access_token:
            return True
        if self.expires_at is None:
            return False
        current = now if now is not None else time.time()
        return self.expires_at <= current


class GoogleTokenStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._access_cache: dict[str, dict[str, object]] = {}

    @classmethod
    def from_env(cls) -> "GoogleTokenStore":
        path = Path(
            os.getenv("GOOGLE_TOKENS_DB_PATH")
            or os.getenv("GOOGLE_TOKENS_PATH", "data/google_tokens.db")
        )
        store = cls(path)
        store.load()
        return store

    def load(self) -> None:
        with self._lock:
            self._init_db()
            # Drop in-memory cache to reflect persistent state.
            self._access_cache = {}

    def get_tokens(self, user_id: int) -> GoogleTokens | None:
        user_key = str(user_id)
        with self._lock:
            self._init_db()
            refresh_token = self._get_refresh_token_locked(user_key)
            if refresh_token is None:
                return None
            cached = self._access_cache.get(user_key)
            if cached is None:
                cached = self._load_access_cache_locked(user_key)
                if cached:
                    self._access_cache[user_key] = cached
            cached = cached or {}
            access_token = cached.get("access_token") if isinstance(cached.get("access_token"), str) else None
            expires_at = cached.get("expires_at") if isinstance(cached.get("expires_at"), (int, float)) else None
            token_type = cached.get("token_type") if isinstance(cached.get("token_type"), str) else None
            scope = cached.get("scope") if isinstance(cached.get("scope"), str) else None
            return GoogleTokens(
                refresh_token=refresh_token,
                access_token=access_token,
                expires_at=expires_at,
                token_type=token_type,
                scope=scope,
            )

    def set_tokens(self, user_id: int, tokens: GoogleTokens) -> None:
        user_key = str(user_id)
        with self._lock:
            self._init_db()
            now = _utc_now_iso()
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO google_tokens(user_id, refresh_token, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                      refresh_token=excluded.refresh_token,
                      updated_at=excluded.updated_at
                    """,
                    (user_key, tokens.refresh_token, now, now),
                )
                if tokens.access_token:
                    conn.execute(
                        """
                        INSERT INTO google_access_tokens(user_id, access_token, expires_at, token_type, scope, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET
                          access_token=excluded.access_token,
                          expires_at=excluded.expires_at,
                          token_type=excluded.token_type,
                          scope=excluded.scope,
                          updated_at=excluded.updated_at
                        """,
                        (
                            user_key,
                            tokens.access_token,
                            tokens.expires_at,
                            tokens.token_type,
                            tokens.scope,
                            now,
                        ),
                    )
                conn.commit()
            self._access_cache[user_key] = {
                "access_token": tokens.access_token,
                "expires_at": tokens.expires_at,
                "token_type": tokens.token_type,
                "scope": tokens.scope,
            }

    def update_access_token(
        self,
        user_id: int,
        *,
        access_token: str,
        expires_at: float | None,
        scope: str | None = None,
        token_type: str | None = None,
    ) -> None:
        user_key = str(user_id)
        with self._lock:
            self._init_db()
            if self._get_refresh_token_locked(user_key) is None:
                return
            cached = self._access_cache.get(user_key) or {}
            cached["access_token"] = access_token
            cached["expires_at"] = expires_at
            if scope is not None:
                cached["scope"] = scope
            if token_type is not None:
                cached["token_type"] = token_type
            self._access_cache[user_key] = cached
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO google_access_tokens(user_id, access_token, expires_at, token_type, scope, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                      access_token=excluded.access_token,
                      expires_at=excluded.expires_at,
                      token_type=excluded.token_type,
                      scope=excluded.scope,
                      updated_at=excluded.updated_at
                    """,
                    (
                        user_key,
                        access_token,
                        expires_at,
                        token_type,
                        scope,
                        _utc_now_iso(),
                    ),
                )
                conn.execute(
                    "UPDATE google_tokens SET updated_at=? WHERE user_id=?",
                    (_utc_now_iso(), user_key),
                )
                conn.commit()

    def delete_tokens(self, user_id: int) -> None:
        user_key = str(user_id)
        with self._lock:
            self._init_db()
            self._access_cache.pop(user_key, None)
            with self._connect() as conn:
                conn.execute("DELETE FROM google_tokens WHERE user_id=?", (user_key,))
                conn.execute("DELETE FROM google_access_tokens WHERE user_id=?", (user_key,))
                conn.commit()

    def _init_db(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS google_tokens (
                  user_id TEXT PRIMARY KEY,
                  refresh_token TEXT NOT NULL,
                  created_at TEXT,
                  updated_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS google_access_tokens (
                  user_id TEXT PRIMARY KEY,
                  access_token TEXT NOT NULL,
                  expires_at REAL,
                  token_type TEXT,
                  scope TEXT,
                  updated_at TEXT
                )
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _get_refresh_token_locked(self, user_key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT refresh_token FROM google_tokens WHERE user_id=?",
                (user_key,),
            ).fetchone()
        if not row:
            return None
        refresh_token = row[0]
        return refresh_token if isinstance(refresh_token, str) and refresh_token else None

    def _load_access_cache_locked(self, user_key: str) -> dict[str, object]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT access_token, expires_at, token_type, scope FROM google_access_tokens WHERE user_id=?",
                (user_key,),
            ).fetchone()
        if not row:
            return {}
        access_token, expires_at, token_type, scope = row
        return {
            "access_token": access_token if isinstance(access_token, str) else None,
            "expires_at": expires_at if isinstance(expires_at, (int, float)) else None,
            "token_type": token_type if isinstance(token_type, str) else None,
            "scope": scope if isinstance(scope, str) else None,
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
