from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_TOKENS_DB_PATH = Path("data/google_tokens.db")


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
        self._ensure_parent()
        self._init_db()

    @classmethod
    def from_env(cls) -> "GoogleTokenStore":
        raw = os.getenv("GOOGLE_TOKENS_DB_PATH") or os.getenv("GOOGLE_TOKENS_PATH")
        path = Path(raw) if raw else DEFAULT_TOKENS_DB_PATH
        store = cls(path)
        store.load()
        return store

    def load(self) -> None:
        with self._lock:
            self._init_db()

    def get_tokens(self, user_id: int) -> GoogleTokens | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT access_token, refresh_token, expires_at, token_type, scope
                FROM google_tokens
                WHERE user_id = ?
                """,
                (str(user_id),),
            ).fetchone()
            if row is None:
                return None
            access_token, refresh_token, expires_at, token_type, scope = row
            if not isinstance(refresh_token, str) or not refresh_token:
                return None
            access_token_value = access_token if isinstance(access_token, str) else ""
            if not access_token_value:
                return None
            expires_at_value = expires_at if isinstance(expires_at, (int, float)) else None
            return GoogleTokens(
                access_token=access_token_value,
                refresh_token=refresh_token,
                expires_at=expires_at_value,
                token_type=token_type if isinstance(token_type, str) else None,
                scope=scope if isinstance(scope, str) else None,
            )

    def set_tokens(self, user_id: int, tokens: GoogleTokens) -> None:
        now_iso = _utcnow_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO google_tokens (
                    user_id,
                    refresh_token,
                    access_token,
                    expires_at,
                    token_type,
                    scope,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    refresh_token = excluded.refresh_token,
                    access_token = excluded.access_token,
                    expires_at = excluded.expires_at,
                    token_type = excluded.token_type,
                    scope = excluded.scope,
                    updated_at = excluded.updated_at
                """,
                (
                    str(user_id),
                    tokens.refresh_token,
                    tokens.access_token,
                    tokens.expires_at,
                    tokens.token_type,
                    tokens.scope,
                    now_iso,
                    now_iso,
                ),
            )
            conn.commit()

    def update_access_token(
        self,
        user_id: int,
        *,
        access_token: str,
        expires_at: float | None,
        scope: str | None = None,
        token_type: str | None = None,
    ) -> None:
        now_iso = _utcnow_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE google_tokens
                SET access_token = ?,
                    expires_at = ?,
                    scope = COALESCE(?, scope),
                    token_type = COALESCE(?, token_type),
                    updated_at = ?
                WHERE user_id = ?
                """,
                (
                    access_token,
                    expires_at,
                    scope,
                    token_type,
                    now_iso,
                    str(user_id),
                ),
            )
            conn.commit()

    def delete_tokens(self, user_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM google_tokens WHERE user_id = ?", (str(user_id),))
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS google_tokens (
                    user_id TEXT PRIMARY KEY,
                    refresh_token TEXT NOT NULL,
                    access_token TEXT,
                    expires_at REAL,
                    token_type TEXT,
                    scope TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
            conn.commit()

    def _ensure_parent(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
