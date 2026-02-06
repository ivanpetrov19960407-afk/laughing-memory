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
    """SQLite-backed store for Google OAuth tokens.

    Required table schema:
        google_tokens(
            user_id TEXT PRIMARY KEY,
            refresh_token TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )

    Additional columns (access_token, expires_at, token_type, scope) are stored
    for runtime use but are not strictly required for persistence.
    """

    _CREATE_TABLE = """
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

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._init_db()

    @classmethod
    def from_env(cls) -> "GoogleTokenStore":
        path = Path(os.getenv("GOOGLE_TOKENS_PATH", "data/google_tokens.db"))
        return cls(path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = sqlite3.connect(str(self._path))
            try:
                conn.execute(self._CREATE_TABLE)
                conn.commit()
            finally:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._path))

    # ------------------------------------------------------------------
    # Backward-compatibility shim
    # ------------------------------------------------------------------

    def load(self) -> None:  # noqa: D102
        """No-op kept for backward compatibility. SQLite is queried on demand."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_tokens(self, user_id: int) -> GoogleTokens | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT refresh_token, access_token, expires_at, token_type, scope "
                    "FROM google_tokens WHERE user_id = ?",
                    (str(user_id),),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        refresh_token, access_token, expires_at, token_type, scope = row
        if not isinstance(refresh_token, str):
            return None
        return GoogleTokens(
            access_token=access_token if isinstance(access_token, str) else "",
            refresh_token=refresh_token,
            expires_at=float(expires_at) if expires_at is not None else None,
            token_type=token_type if isinstance(token_type, str) else None,
            scope=scope if isinstance(scope, str) else None,
        )

    def set_tokens(self, user_id: int, tokens: GoogleTokens) -> None:
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._lock:
            conn = self._connect()
            try:
                existing = conn.execute(
                    "SELECT created_at FROM google_tokens WHERE user_id = ?",
                    (str(user_id),),
                ).fetchone()
                created_at = existing[0] if existing else now_iso
                conn.execute(
                    """
                    INSERT INTO google_tokens
                        (user_id, refresh_token, access_token, expires_at,
                         token_type, scope, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        refresh_token = excluded.refresh_token,
                        access_token  = excluded.access_token,
                        expires_at    = excluded.expires_at,
                        token_type    = excluded.token_type,
                        scope         = excluded.scope,
                        updated_at    = excluded.updated_at
                    """,
                    (
                        str(user_id),
                        tokens.refresh_token,
                        tokens.access_token,
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
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._lock:
            conn = self._connect()
            try:
                parts = ["access_token = ?", "expires_at = ?", "updated_at = ?"]
                values: list[object] = [access_token, expires_at, now_iso]
                if scope is not None:
                    parts.append("scope = ?")
                    values.append(scope)
                if token_type is not None:
                    parts.append("token_type = ?")
                    values.append(token_type)
                values.append(str(user_id))
                conn.execute(
                    f"UPDATE google_tokens SET {', '.join(parts)} WHERE user_id = ?",
                    values,
                )
                conn.commit()
            finally:
                conn.close()

    def delete_tokens(self, user_id: int) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM google_tokens WHERE user_id = ?",
                    (str(user_id),),
                )
                conn.commit()
            finally:
                conn.close()
