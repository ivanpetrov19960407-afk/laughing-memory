from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4


@dataclass
class DocumentSession:
    doc_id: str
    user_id: int
    chat_id: int
    file_path: str
    file_type: str
    text_path: str
    state: str
    created_at: datetime
    updated_at: datetime


class DocumentSessionStore:
    def __init__(
        self,
        path: Path,
        *,
        now_provider: Callable[[], datetime] | None = None,
        ttl_hours: int = 24,
    ) -> None:
        self._path = path
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._ttl_hours = ttl_hours
        self._sessions: dict[str, DocumentSession] = {}
        self._active_by_key: dict[str, str] = {}

    def load(self) -> None:
        if not self._path.exists():
            return
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        sessions = payload.get("sessions", [])
        active = payload.get("active_by_key", {})
        if isinstance(active, dict):
            self._active_by_key = {str(key): str(value) for key, value in active.items()}
        now = self._now_provider()
        ttl_delta = timedelta(hours=self._ttl_hours)
        for item in sessions:
            if not isinstance(item, dict):
                continue
            session = _deserialize_session(item)
            if session:
                # Проверяем TTL при загрузке
                if now - session.updated_at > ttl_delta:
                    continue  # Пропускаем истёкшие сессии
                self._sessions[session.doc_id] = session
        # Очищаем активные сессии, если они истекли
        expired_keys = []
        for key, doc_id in self._active_by_key.items():
            if doc_id not in self._sessions:
                expired_keys.append(key)
        for key in expired_keys:
            self._active_by_key.pop(key, None)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "sessions": [_serialize_session(session) for session in self._sessions.values()],
            "active_by_key": dict(self._active_by_key),
        }
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def create_session(
        self,
        *,
        user_id: int,
        chat_id: int,
        file_path: str,
        file_type: str,
        text_path: str,
        state: str = "action_select",
    ) -> DocumentSession:
        now = self._now_provider()
        session = DocumentSession(
            doc_id=str(uuid4()),
            user_id=user_id,
            chat_id=chat_id,
            file_path=file_path,
            file_type=file_type,
            text_path=text_path,
            state=state,
            created_at=now,
            updated_at=now,
        )
        self._sessions[session.doc_id] = session
        self._active_by_key[_active_key(user_id, chat_id)] = session.doc_id
        self.save()
        return session

    def get_session(self, doc_id: str) -> DocumentSession | None:
        return self._sessions.get(doc_id)

    def get_active(self, *, user_id: int, chat_id: int) -> DocumentSession | None:
        doc_id = self._active_by_key.get(_active_key(user_id, chat_id))
        if not doc_id:
            return None
        session = self._sessions.get(doc_id)
        if session is None:
            return None
        # Проверяем TTL
        now = self._now_provider()
        ttl_delta = timedelta(hours=self._ttl_hours)
        if now - session.updated_at > ttl_delta:
            # Сессия истекла, удаляем
            self._sessions.pop(doc_id, None)
            self._active_by_key.pop(_active_key(user_id, chat_id), None)
            self.save()
            return None
        return session

    def set_state(self, *, doc_id: str, state: str) -> DocumentSession | None:
        session = self._sessions.get(doc_id)
        if not session:
            return None
        session.state = state
        session.updated_at = self._now_provider()
        self.save()
        return session

    def close_active(self, *, user_id: int, chat_id: int) -> DocumentSession | None:
        key = _active_key(user_id, chat_id)
        doc_id = self._active_by_key.pop(key, None)
        if not doc_id:
            return None
        session = self._sessions.get(doc_id)
        if session:
            session.state = "idle"
            session.updated_at = self._now_provider()
            self.save()
        return session


def _active_key(user_id: int, chat_id: int) -> str:
    return f"{user_id}:{chat_id}"


def _serialize_session(session: DocumentSession) -> dict[str, object]:
    data = asdict(session)
    data["created_at"] = session.created_at.isoformat()
    data["updated_at"] = session.updated_at.isoformat()
    return data


def _deserialize_session(raw: dict[str, object]) -> DocumentSession | None:
    try:
        created_at = datetime.fromisoformat(str(raw.get("created_at")))
        updated_at = datetime.fromisoformat(str(raw.get("updated_at")))
    except ValueError:
        return None
    return DocumentSession(
        doc_id=str(raw.get("doc_id", "")),
        user_id=int(raw.get("user_id", 0)),
        chat_id=int(raw.get("chat_id", 0)),
        file_path=str(raw.get("file_path", "")),
        file_type=str(raw.get("file_type", "")),
        text_path=str(raw.get("text_path", "")),
        state=str(raw.get("state", "idle")),
        created_at=created_at,
        updated_at=updated_at,
    )
