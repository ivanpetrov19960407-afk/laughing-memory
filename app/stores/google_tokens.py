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
        self._init_db()

    @classmethod
    def from_env(cls) -> "GoogleTokenStore":
        path = Path(os.getenv("GOOGLE_TOKENS_DB_PATH", "data/google_tokens.db"))
        return cls(path)

    def _init_db(self) -> None:
        """Initialize SQLite database and create table if it doesn't exist."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = sqlite3.connect(str(self._path))
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS google_tokens (
                        user_id TEXT PRIMARY KEY,
                        access_token TEXT NOT NULL,
                        refresh_token TEXT NOT NULL,
                        expires_at REAL,
                        token_type TEXT,
                        scope TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                conn.commit()
            finally:
                conn.close()

    def load(self) -> None:
        """Load is a no-op for SQLite-based storage (kept for API compatibility)."""
        pass

    def get_tokens(self, user_id: int) -> GoogleTokens | None:
        with self._lock:
            conn = sqlite3.connect(str(self._path))
            try:
                cursor = conn.execute(
                    "SELECT access_token, refresh_token, expires_at, token_type, scope FROM google_tokens WHERE user_id = ?",
                    (str(user_id),),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                access_token, refresh_token, expires_at, token_type, scope = row
                return GoogleTokens(
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expires_at=expires_at,
                    token_type=token_type,
                    scope=scope,
                )
            finally:
                conn.close()

    def set_tokens(self, user_id: int, tokens: GoogleTokens) -> None:
        now = time.time()
        now_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))
        with self._lock:
            conn = sqlite3.connect(str(self._path))
            try:
                # Check if token exists
                cursor = conn.execute("SELECT created_at FROM google_tokens WHERE user_id = ?", (str(user_id),))
                row = cursor.fetchone()
                created_at = row[0] if row else now_iso
                
                conn.execute(
                    """
                    INSERT OR REPLACE INTO google_tokens 
                    (user_id, access_token, refresh_token, expires_at, token_type, scope, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(user_id),
                        tokens.access_token,
                        tokens.refresh_token,
                        tokens.expires_at,
                        tokens.token_type,
                        tokens.scope,
                        created_at,
                        now_iso,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def update_access_token(
        self,
        user_id: int,
        *,
        access_token: str,
        expires_at: float | None,
        scope: str | None = None,
        token_type: str | None = None,
    ) -> None:
        now_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time()))
        with self._lock:
            conn = sqlite3.connect(str(self._path))
            try:
                updates = ["access_token = ?", "expires_at = ?", "updated_at = ?"]
                params = [access_token, expires_at, now_iso]
                if scope is not None:
                    updates.append("scope = ?")
                    params.append(scope)
                if token_type is not None:
                    updates.append("token_type = ?")
                    params.append(token_type)
                params.append(str(user_id))
                
                conn.execute(
                    f"UPDATE google_tokens SET {', '.join(updates)} WHERE user_id = ?",
                    params,
                )
                conn.commit()
            finally:
                conn.close()

    def delete_tokens(self, user_id: int) -> None:
        with self._lock:
            conn = sqlite3.connect(str(self._path))
            try:
                conn.execute("DELETE FROM google_tokens WHERE user_id = ?", (str(user_id),))
                conn.commit()
            finally:
                conn.close()
