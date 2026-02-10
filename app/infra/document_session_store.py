from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal
from uuid import uuid4

LOGGER = logging.getLogger(__name__)


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
    expires_at: datetime


class DocumentSessionStore:
    def __init__(
        self,
        path: Path,
        *,
        ttl_seconds: int = 7200,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = path
        self._ttl_seconds = max(60, ttl_seconds)
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
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
        for item in sessions:
            if not isinstance(item, dict):
                continue
            session = _deserialize_session(item, ttl_seconds=self._ttl_seconds)
            if session:
                self._sessions[session.doc_id] = session

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
        expires_at = now + timedelta(seconds=self._ttl_seconds)
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
            expires_at=expires_at,
        )
        self._sessions[session.doc_id] = session
        self._active_by_key[_active_key(user_id, chat_id)] = session.doc_id
        self.save()
        LOGGER.info(
            "doc_session_started user_id=%s chat_id=%s doc_id=%s",
            user_id,
            chat_id,
            session.doc_id,
        )
        return session

    def _is_expired(self, session: DocumentSession) -> bool:
        return self._now_provider() >= session.expires_at

    def get_session(self, doc_id: str) -> DocumentSession | None:
        session, _ = self.get_session_with_status(doc_id)
        return session

    def get_session_with_status(
        self, doc_id: str
    ) -> tuple[DocumentSession | None, Literal["ok", "expired", "none"]]:
        session = self._sessions.get(doc_id)
        if session is None:
            return None, "none"
        if self._is_expired(session):
            LOGGER.info(
                "doc_session_expired doc_id=%s user_id=%s chat_id=%s",
                doc_id,
                session.user_id,
                session.chat_id,
            )
            self._drop_session(session)
            return None, "expired"
        return session, "ok"

    def get_active(self, *, user_id: int, chat_id: int) -> DocumentSession | None:
        session, _ = self.get_active_with_status(user_id=user_id, chat_id=chat_id)
        return session

    def get_active_with_status(
        self, *, user_id: int, chat_id: int
    ) -> tuple[DocumentSession | None, Literal["ok", "expired", "none"]]:
        """Returns (session, status). status is 'expired' when TTL exceeded."""
        key = _active_key(user_id, chat_id)
        doc_id = self._active_by_key.get(key)
        if not doc_id:
            return None, "none"
        session = self._sessions.get(doc_id)
        if session is None:
            self._active_by_key.pop(key, None)
            return None, "none"
        if self._is_expired(session):
            LOGGER.info(
                "doc_session_expired user_id=%s chat_id=%s doc_id=%s",
                user_id,
                chat_id,
                doc_id,
            )
            self._drop_session(session)
            self._active_by_key.pop(key, None)
            return None, "expired"
        return session, "ok"

    def _drop_session(self, session: DocumentSession) -> None:
        key = _active_key(session.user_id, session.chat_id)
        self._active_by_key.pop(key, None)
        self._sessions.pop(session.doc_id, None)
        self.save()

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
        session = self._sessions.pop(doc_id, None)
        if session:
            LOGGER.info(
                "doc_session_stopped user_id=%s chat_id=%s doc_id=%s",
                user_id,
                chat_id,
                doc_id,
            )
            self.save()
        return session

    def cleanup_expired(
        self,
        ttl_seconds: int,
        *,
        delete_text_files: bool = True,
        delete_upload_files: bool = True,
    ) -> None:
        """Remove sessions older than ttl_seconds; best-effort delete text/upload files."""
        if ttl_seconds <= 0:
            return
        now = self._now_provider()
        cutoff = now - timedelta(seconds=ttl_seconds)
        to_remove: list[str] = []
        for doc_id, session in list(self._sessions.items()):
            if session.updated_at < cutoff:
                to_remove.append(doc_id)
        for key, doc_id in list(self._active_by_key.items()):
            if doc_id in to_remove:
                del self._active_by_key[key]
        for doc_id in to_remove:
            session = self._sessions.pop(doc_id, None)
            if session and (delete_text_files or delete_upload_files):
                try:
                    if delete_text_files and session.text_path:
                        Path(session.text_path).unlink(missing_ok=True)
                    if delete_upload_files and session.file_path:
                        Path(session.file_path).unlink(missing_ok=True)
                except OSError:
                    pass
        if to_remove:
            self.save()


def _active_key(user_id: int, chat_id: int) -> str:
    return f"{user_id}:{chat_id}"


def _serialize_session(session: DocumentSession) -> dict[str, object]:
    data = asdict(session)
    data["created_at"] = session.created_at.isoformat()
    data["updated_at"] = session.updated_at.isoformat()
    data["expires_at"] = session.expires_at.isoformat()
    return data


def _deserialize_session(
    raw: dict[str, object], *, ttl_seconds: int = 7200
) -> DocumentSession | None:
    try:
        created_at = datetime.fromisoformat(str(raw.get("created_at")))
        updated_at = datetime.fromisoformat(str(raw.get("updated_at")))
    except ValueError:
        return None
    raw_expires = raw.get("expires_at")
    if isinstance(raw_expires, str) and raw_expires:
        try:
            expires_at = datetime.fromisoformat(raw_expires)
        except ValueError:
            expires_at = created_at + timedelta(seconds=ttl_seconds)
    else:
        expires_at = created_at + timedelta(seconds=ttl_seconds)
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
        expires_at=expires_at,
    )
