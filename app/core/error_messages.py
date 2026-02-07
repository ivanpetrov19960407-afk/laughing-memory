from __future__ import annotations

from typing import Final

TIMEOUT_TEXT: Final[str] = "Время ожидания истекло. Попробуйте позже."
TEMP_UNAVAILABLE_TEXT: Final[str] = "Сервис временно недоступен. Попробуйте позже."
RATE_LIMITED_TEXT: Final[str] = "Слишком много запросов. Попробуйте позже."
AUTH_REQUIRED_TEXT: Final[str] = "Требуется повторная авторизация. Проверьте подключение."


def map_error_text(kind: str) -> str:
    normalized = kind.strip().lower()
    if normalized == "timeout":
        return TIMEOUT_TEXT
    if normalized == "rate_limited":
        return RATE_LIMITED_TEXT
    if normalized == "auth_required":
        return AUTH_REQUIRED_TEXT
    return TEMP_UNAVAILABLE_TEXT
