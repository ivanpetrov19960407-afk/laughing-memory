from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from app.core.models import TaskExecutionResult


LOGGER = logging.getLogger(__name__)


class TaskStorage:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS task_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                task_name TEXT NOT NULL,
                payload TEXT NOT NULL,
                result TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def record_execution(self, execution: TaskExecutionResult) -> None:
        self._connection.execute(
            """
            INSERT INTO task_executions (
                timestamp, user_id, task_name, payload, result, status
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                execution.executed_at.isoformat(),
                execution.user_id,
                execution.task_name,
                execution.payload,
                execution.result,
                execution.status,
            ),
        )
        self._connection.commit()

    def get_last_execution(self, user_id: int) -> sqlite3.Row | None:
        cursor = self._connection.execute(
            """
            SELECT timestamp, task_name, payload, result, status
            FROM task_executions
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        return cursor.fetchone()

    def close(self) -> None:
        try:
            self._connection.close()
        except sqlite3.Error:
            LOGGER.exception("Failed to close database connection")
