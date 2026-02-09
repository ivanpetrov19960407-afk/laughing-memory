from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.actions_log import ActionLogEntry

LOGGER = logging.getLogger(__name__)

ACTION_LOG_SCHEMA_VERSION = 1
DEFAULT_TTL_DAYS = 60


class ActionsLogStore:
    def __init__(self, db_path: Path, *, ttl_days: int = DEFAULT_TTL_DAYS) -> None:
        self._db_path = db_path
        self._ttl_days = max(1, min(365, ttl_days))
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                action_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                correlation_id TEXT,
                schema_version INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        columns = self._connection.execute("PRAGMA table_info(user_actions)").fetchall()
        column_names = {row[1] for row in columns}
        if "schema_version" not in column_names:
            self._connection.execute(
                "ALTER TABLE user_actions ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1"
            )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_actions_user_ts ON user_actions (user_id, ts DESC)"
        )
        self._connection.commit()

    def append(
        self,
        *,
        user_id: int,
        action_type: str,
        payload: dict[str, Any],
        ts: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ActionLogEntry:
        if ts is None:
            ts = datetime.now(timezone.utc)
        elif ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        timestamp = ts.isoformat()
        if not isinstance(payload, dict):
            payload = {}
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        cursor = self._connection.execute(
            """
            INSERT INTO user_actions (user_id, ts, action_type, payload, correlation_id, schema_version)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, timestamp, action_type, encoded, correlation_id, ACTION_LOG_SCHEMA_VERSION),
        )
        self._connection.commit()
        entry_id = cursor.lastrowid if cursor.lastrowid else 0
        return ActionLogEntry(
            id=entry_id,
            user_id=user_id,
            ts=ts,
            action_type=action_type,
            payload=payload,
            correlation_id=correlation_id,
        )

    def list_recent(
        self,
        *,
        user_id: int,
        limit: int = 10,
        since: datetime | None = None,
    ) -> list[ActionLogEntry]:
        if limit <= 0:
            return []
        params: list[object] = [user_id]
        sql = """
            SELECT id, user_id, ts, action_type, payload, correlation_id
            FROM user_actions
            WHERE user_id = ?
        """
        if since is not None:
            sql += " AND ts >= ?"
            params.append(since.astimezone(timezone.utc).isoformat())
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(limit)
        cursor = self._connection.execute(sql, params)
        rows = cursor.fetchall()
        return [_row_to_entry(row) for row in rows]

    def _cleanup_old_lazy(self) -> None:
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=self._ttl_days)).isoformat()
            cursor = self._connection.execute(
                "DELETE FROM user_actions WHERE ts < ?",
                (cutoff,),
            )
            self._connection.commit()
            if cursor.rowcount and cursor.rowcount > 0:
                LOGGER.debug("Actions log TTL cleanup: removed %s rows", cursor.rowcount)
        except sqlite3.Error:
            LOGGER.exception("Actions log TTL cleanup failed")
            self._connection.rollback()

    def cleanup_old(self, *, ttl_days: int | None = None) -> int:
        days = ttl_days if ttl_days is not None else self._ttl_days
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
        cursor = self._connection.execute(
            "DELETE FROM user_actions WHERE ts < ?",
            (cutoff,),
        )
        deleted = cursor.rowcount or 0
        self._connection.commit()
        return deleted

    def search(
        self,
        *,
        user_id: int,
        query: str | None = None,
        limit: int = 10,
    ) -> list[ActionLogEntry]:
        if limit <= 0:
            return []
        normalized_query = (query or "").strip()
        params: list[object] = [user_id]
        sql = """
            SELECT id, user_id, ts, action_type, payload, correlation_id
            FROM user_actions
            WHERE user_id = ?
        """
        if normalized_query:
            if normalized_query.startswith("type:"):
                action_type = normalized_query.replace("type:", "", 1).strip()
                if action_type:
                    sql += " AND action_type LIKE ?"
                    params.append(f"%{action_type}%")
            else:
                sql += " AND (action_type LIKE ? OR payload LIKE ?)"
                params.extend([f"%{normalized_query}%", f"%{normalized_query}%"])
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(limit)
        cursor = self._connection.execute(sql, params)
        rows = cursor.fetchall()
        return [_row_to_entry(row) for row in rows]

<<<<<<< Current (Your changes)
=======
    def list_recent(self, *, user_id: int, limit: int = 10) -> list[ActionLogEntry]:
        return self.search(user_id=user_id, query=None, limit=limit)

    def list(
        self,
        *,
        user_id: int,
        limit: int = 10,
        since: datetime | None = None,
    ) -> list[ActionLogEntry]:
        if limit <= 0:
            return []
        params: list[object] = [user_id]
        sql = """
            SELECT id, user_id, ts, action_type, payload, correlation_id
            FROM user_actions
            WHERE user_id = ?
        """
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            else:
                since = since.astimezone(timezone.utc)
            since_iso = since.isoformat()
            sql += " AND ts >= ?"
            params.append(since_iso)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(limit)
        cursor = self._connection.execute(sql, params)
        rows = cursor.fetchall()
        return [_row_to_entry(row) for row in rows]

>>>>>>> Incoming (Background Agent changes)
    def clear(self, *, user_id: int) -> None:
        self._connection.execute("DELETE FROM user_actions WHERE user_id = ?", (user_id,))
        self._connection.commit()

    def close(self) -> None:
        try:
            self._connection.close()
        except sqlite3.Error:
            LOGGER.exception("Failed to close actions log database connection")


def _row_to_entry(row: sqlite3.Row) -> ActionLogEntry:
    raw_payload = row["payload"] if isinstance(row, sqlite3.Row) else None
    try:
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return ActionLogEntry(
        id=row["id"],
        user_id=row["user_id"],
        ts=_parse_datetime(row["ts"]),
        action_type=row["action_type"],
        payload=payload,
        correlation_id=row["correlation_id"],
    )


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
