from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any


DEFAULT_LANGUAGE = "ru"
DEFAULT_TIMEZONE = "Europe/Vilnius"
DEFAULT_VERBOSITY = "normal"
DEFAULT_FACTS_MODE = False
DEFAULT_CONTEXT_DEFAULT = False
DEFAULT_DATE_FORMAT = "dd.mm.yyyy"
DEFAULT_ACTIONS_LOG_ENABLED = True
DEFAULT_REMINDER_OFFSET_MINUTES: int | None = None
DEFAULT_REMINDERS_ENABLED = False
DEFAULT_NOTES_LIMIT = 20


@dataclass(frozen=True)
class ReminderDefaults:
    enabled: bool
    offset_minutes: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "offset_minutes": self.offset_minutes,
        }

    @staticmethod
    def from_dict(payload: dict[str, Any] | None) -> ReminderDefaults:
        if not isinstance(payload, dict):
            return default_reminder_defaults()
        enabled = payload.get("enabled")
        offset_minutes = payload.get("offset_minutes")
        return ReminderDefaults(
            enabled=bool(DEFAULT_REMINDERS_ENABLED if enabled is None else enabled),
            offset_minutes=_coerce_optional_int(offset_minutes, DEFAULT_REMINDER_OFFSET_MINUTES, min_value=0),
        )


@dataclass(frozen=True)
class UserNote:
    id: str
    text: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "created_at": self.created_at}

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> UserNote | None:
        if not isinstance(payload, dict):
            return None
        note_id = payload.get("id")
        text = payload.get("text")
        created_at = payload.get("created_at")
        if not isinstance(note_id, str) or not note_id:
            return None
        if not isinstance(text, str) or not text.strip():
            return None
        if not isinstance(created_at, str) or not created_at:
            return None
        return UserNote(id=note_id, text=text.strip(), created_at=created_at)


@dataclass(frozen=True)
class UserProfile:
    user_id: int
    language: str
    timezone: str
    verbosity: str
    facts_mode_default: bool
    context_default: bool
    date_format: str
    actions_log_enabled: bool
    default_reminders: ReminderDefaults
    style: str | None
    notes: tuple[UserNote, ...]
    created_at: str | None
    updated_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "language": self.language,
            "timezone": self.timezone,
            "verbosity": self.verbosity,
            "facts_mode_default": self.facts_mode_default,
            "context_default": self.context_default,
            "date_format": self.date_format,
            "actions_log_enabled": self.actions_log_enabled,
            "default_reminders": self.default_reminders.to_dict(),
            "style": self.style,
            "notes": [note.to_dict() for note in self.notes],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(
        payload: dict[str, Any] | None,
        *,
        user_id: int = 0,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> UserProfile:
        if not isinstance(payload, dict):
            return default_profile(user_id, created_at=created_at, updated_at=updated_at)
        payload_user_id = payload.get("user_id")
        language = payload.get("language")
        timezone_value = payload.get("timezone")
        verbosity = payload.get("verbosity")
        facts_mode_default = payload.get("facts_mode_default")
        context_default = payload.get("context_default")
        date_format = payload.get("date_format")
        actions_log_enabled = payload.get("actions_log_enabled")
        style = payload.get("style")
        created_payload = payload.get("created_at")
        updated_payload = payload.get("updated_at")
        notes_payload = payload.get("notes")
        notes: list[UserNote] = []
        if isinstance(notes_payload, list):
            for item in notes_payload:
                note = UserNote.from_dict(item)
                if note:
                    notes.append(note)
        if user_id:
            normalized_user_id = user_id
        elif isinstance(payload_user_id, int):
            normalized_user_id = payload_user_id
        elif isinstance(payload_user_id, str) and payload_user_id.strip().isdigit():
            normalized_user_id = int(payload_user_id.strip())
        else:
            normalized_user_id = 0
        created_value = created_payload if isinstance(created_payload, str) and created_payload else created_at
        updated_value = updated_payload if isinstance(updated_payload, str) and updated_payload else updated_at
        return UserProfile(
            user_id=normalized_user_id,
            language=language if isinstance(language, str) and language else DEFAULT_LANGUAGE,
            timezone=timezone_value if isinstance(timezone_value, str) and timezone_value else DEFAULT_TIMEZONE,
            verbosity=verbosity if isinstance(verbosity, str) and verbosity else DEFAULT_VERBOSITY,
            facts_mode_default=bool(DEFAULT_FACTS_MODE if facts_mode_default is None else facts_mode_default),
            context_default=bool(DEFAULT_CONTEXT_DEFAULT if context_default is None else context_default),
            date_format=date_format if isinstance(date_format, str) and date_format else DEFAULT_DATE_FORMAT,
            actions_log_enabled=bool(DEFAULT_ACTIONS_LOG_ENABLED if actions_log_enabled is None else actions_log_enabled),
            default_reminders=ReminderDefaults.from_dict(payload.get("default_reminders")),
            style=style if isinstance(style, str) and style else None,
            notes=tuple(notes),
            created_at=created_value,
            updated_at=updated_value,
        )


def default_profile(
    user_id: int = 0,
    *,
    created_at: str | None = None,
    updated_at: str | None = None,
    now: datetime | None = None,
) -> UserProfile:
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    return UserProfile(
        user_id=user_id,
        language=DEFAULT_LANGUAGE,
        timezone=DEFAULT_TIMEZONE,
        verbosity=DEFAULT_VERBOSITY,
        facts_mode_default=DEFAULT_FACTS_MODE,
        context_default=DEFAULT_CONTEXT_DEFAULT,
        date_format=DEFAULT_DATE_FORMAT,
        actions_log_enabled=DEFAULT_ACTIONS_LOG_ENABLED,
        default_reminders=default_reminder_defaults(),
        style=None,
        notes=tuple(),
        created_at=created_at or timestamp,
        updated_at=updated_at or timestamp,
    )


def default_reminder_defaults() -> ReminderDefaults:
    return ReminderDefaults(enabled=DEFAULT_REMINDERS_ENABLED, offset_minutes=DEFAULT_REMINDER_OFFSET_MINUTES)


def apply_profile_patch(profile: UserProfile, patch: dict[str, Any]) -> UserProfile:
    if not isinstance(patch, dict):
        return profile
    language = patch.get("language")
    timezone_value = patch.get("timezone")
    verbosity = patch.get("verbosity")
    facts_mode_default = patch.get("facts_mode_default")
    context_default = patch.get("context_default")
    date_format = patch.get("date_format")
    actions_log_enabled = patch.get("actions_log_enabled")
    style = patch.get("style")
    defaults_patch = patch.get("default_reminders")
    updated_defaults = profile.default_reminders
    if isinstance(defaults_patch, dict):
        enabled = defaults_patch.get("enabled")
        offset = defaults_patch.get("offset_minutes")
        updated_defaults = ReminderDefaults(
            enabled=bool(profile.default_reminders.enabled if enabled is None else enabled),
            offset_minutes=_coerce_optional_int(
                offset,
                profile.default_reminders.offset_minutes,
                min_value=0,
            ),
        )
    updated = replace(
        profile,
        language=language.strip() if isinstance(language, str) and language.strip() else profile.language,
        timezone=timezone_value.strip()
        if isinstance(timezone_value, str) and timezone_value.strip()
        else profile.timezone,
        verbosity=verbosity.strip() if isinstance(verbosity, str) and verbosity.strip() else profile.verbosity,
        facts_mode_default=bool(profile.facts_mode_default if facts_mode_default is None else facts_mode_default),
        context_default=bool(profile.context_default if context_default is None else context_default),
        date_format=date_format.strip() if isinstance(date_format, str) and date_format.strip() else profile.date_format,
        actions_log_enabled=bool(profile.actions_log_enabled if actions_log_enabled is None else actions_log_enabled),
        default_reminders=updated_defaults,
        style=style.strip() if isinstance(style, str) and style.strip() else profile.style,
    )
    return updated


def add_profile_note(profile: UserProfile, text: str, *, now: datetime | None = None) -> UserProfile:
    trimmed = (text or "").strip()
    if not trimmed:
        return profile
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    note = UserNote(id=_generate_id(), text=trimmed, created_at=timestamp)
    notes = (note,) + profile.notes
    if len(notes) > DEFAULT_NOTES_LIMIT:
        notes = notes[:DEFAULT_NOTES_LIMIT]
    return replace(profile, notes=notes)


def remove_profile_note(profile: UserProfile, key: str) -> tuple[UserProfile, bool]:
    trimmed = (key or "").strip()
    if not trimmed:
        return profile, False
    filtered = [note for note in profile.notes if note.id != trimmed and trimmed.lower() not in note.text.lower()]
    if len(filtered) == len(profile.notes):
        return profile, False
    return replace(profile, notes=tuple(filtered)), True


def normalize_profile_payload(
    payload: dict[str, Any] | None,
    *,
    user_id: int = 0,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    profile = UserProfile.from_dict(payload, user_id=user_id, created_at=created_at, updated_at=updated_at)
    return profile.to_dict()


def _generate_id() -> str:
    return uuid.uuid4().hex[:8]


def _coerce_int(value: object, fallback: int, *, min_value: int | None = None) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
        parsed = int(value)
    else:
        return fallback
    if min_value is not None and parsed < min_value:
        return fallback
    return parsed


def _coerce_optional_int(
    value: object,
    fallback: int | None,
    *,
    min_value: int | None = None,
) -> int | None:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
        parsed = int(value)
    else:
        return fallback
    if min_value is not None and parsed < min_value:
        return fallback
    return parsed
