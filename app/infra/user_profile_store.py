from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.user_profile import (
    UserProfile,
    add_profile_note,
    apply_profile_patch,
    default_profile,
    normalize_profile_payload,
    remove_profile_note,
)

LOGGER = logging.getLogger(__name__)

PROFILE_SCHEMA_VERSION = 1


class UserProfileStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id INTEGER PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def get(self, user_id: int) -> UserProfile:
        row = self._fetch_row(user_id)
        if row is None:
            return default_profile()
        payload, schema_version = self._load_payload(row)
        migrated_payload, updated_version, changed = self._migrate_payload(payload, schema_version)
        if changed:
            self._save_payload(user_id, migrated_payload, updated_version)
        return UserProfile.from_dict(migrated_payload)

    def update(self, user_id: int, patch: dict[str, Any]) -> UserProfile:
        profile = self.get(user_id)
        updated = apply_profile_patch(profile, patch)
        self._save_payload(user_id, updated.to_dict(), PROFILE_SCHEMA_VERSION)
        return updated

    def add_note(self, user_id: int, text: str) -> UserProfile:
        profile = self.get(user_id)
        updated = add_profile_note(profile, text)
        self._save_payload(user_id, updated.to_dict(), PROFILE_SCHEMA_VERSION)
        return updated

    def remove_note(self, user_id: int, key: str) -> tuple[UserProfile, bool]:
        profile = self.get(user_id)
        updated, removed = remove_profile_note(profile, key)
        if removed:
            self._save_payload(user_id, updated.to_dict(), PROFILE_SCHEMA_VERSION)
        return updated, removed

    def set_defaults(self, user_id: int, profile: UserProfile) -> None:
        self._save_payload(user_id, profile.to_dict(), PROFILE_SCHEMA_VERSION)

    def close(self) -> None:
        try:
            self._connection.close()
        except sqlite3.Error:
            LOGGER.exception("Failed to close profile database connection")

    def _fetch_row(self, user_id: int) -> sqlite3.Row | None:
        cursor = self._connection.execute(
            """
            SELECT user_id, schema_version, payload, updated_at
            FROM user_profiles
            WHERE user_id = ?
            """,
            (user_id,),
        )
        return cursor.fetchone()

    def _load_payload(self, row: sqlite3.Row) -> tuple[dict[str, Any], int]:
        schema_version = row["schema_version"]
        raw_payload = row["payload"]
        if not isinstance(schema_version, int):
            schema_version = 0
        try:
            payload = json.loads(raw_payload) if isinstance(raw_payload, str) else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return payload, schema_version

    def _save_payload(self, user_id: int, payload: dict[str, Any], schema_version: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            """
            INSERT INTO user_profiles (user_id, schema_version, payload, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                schema_version=excluded.schema_version,
                payload=excluded.payload,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                schema_version,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now,
            ),
        )
        self._connection.commit()

    def _migrate_payload(
        self,
        payload: dict[str, Any],
        schema_version: int,
    ) -> tuple[dict[str, Any], int, bool]:
        normalized = normalize_profile_payload(payload)
        changed = schema_version != PROFILE_SCHEMA_VERSION or normalized != payload
        if schema_version > PROFILE_SCHEMA_VERSION:
            LOGGER.warning(
                "Profile schema version newer than expected: %s > %s",
                schema_version,
                PROFILE_SCHEMA_VERSION,
            )
            return payload, schema_version, False
        return normalized, PROFILE_SCHEMA_VERSION, changed

