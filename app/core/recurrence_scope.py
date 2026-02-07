from __future__ import annotations

from enum import Enum
import re


class RecurrenceScope(str, Enum):
    THIS = "THIS"
    ALL = "ALL"
    FUTURE = "FUTURE"


_THIS_PHRASES = (
    "только это",
    "только сегодня",
    "только в этот раз",
    "только этот раз",
    "в этот раз",
)
_ALL_PHRASES = (
    "всю серию",
    "вся серия",
    "всегда",
)
_FUTURE_PHRASES = (
    "начиная с",
    "начиная со",
    "дальше",
    "со следующего раза",
    "со следующей недели",
    "в будущем",
)


def normalize_scope(value: RecurrenceScope | str | None) -> RecurrenceScope | None:
    if value is None:
        return None
    if isinstance(value, RecurrenceScope):
        return value
    if isinstance(value, str):
        normalized = value.strip().upper()
        for scope in RecurrenceScope:
            if scope.value == normalized:
                return scope
    return None


def parse_recurrence_scope(text: str | None) -> RecurrenceScope | None:
    if not text:
        return None
    lowered = text.lower()
    if _contains_phrase(lowered, _THIS_PHRASES):
        return RecurrenceScope.THIS
    if _contains_phrase(lowered, _FUTURE_PHRASES):
        return RecurrenceScope.FUTURE
    if _contains_phrase(lowered, _ALL_PHRASES) or re.search(r"\bвсе\b|\bвсё\b", lowered):
        return RecurrenceScope.ALL
    return None


def _contains_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)
