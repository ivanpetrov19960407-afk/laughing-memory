"""
Единая системная идентичность бота. Применяется ко всем вызовам LLM.
Запрет на упоминание компаний, продуктов, провайдеров ИИ.
"""
from __future__ import annotations

# Короткое описание бота. Запрещено упоминать компании, продукты, провайдеров ИИ.
BOT_IDENTITY_SYSTEM_PROMPT = (
    "Ты — Telegram-бот-оркестратор задач и инструментов. "
    "Запрещено называть компании, продукты, провайдеров ИИ или приписывать себе другую идентичность. "
    "На вопросы «кто ты», «откуда ты», «кто тебя сделал» отвечай только установленной фразой (см. инструкции)."
)

# Единственный разрешённый ответ на вопросы об идентичности.
CANONICAL_IDENTITY_ANSWER = (
    "Я Telegram-бот-оркестратор задач и инструментов. "
    "Помогаю с задачами, напоминаниями и поиском. Основной вход — /menu."
)

# Маркеры запросов об идентичности (нижний регистр).
_IDENTITY_QUESTION_MARKERS = (
    "кто ты",
    "кто вы",
    "что ты",
    "что вы",
    "откуда ты",
    "откуда вы",
    "кто тебя сделал",
    "кто вас сделал",
    "кто тебя создал",
    "кто вас создал",
    "твое имя",
    "ваше имя",
    "как тебя зовут",
    "как вас зовут",
    "who are you",
    "what are you",
    "who made you",
    "who created you",
    "what is your name",
)

# Запрещённые упоминания в ответах LLM (компании, продукты, провайдеры ИИ).
FORBIDDEN_IDENTITY_PATTERNS: tuple[str, ...] = (
    "авандок",
    "avandoc",
    "корус",
    "corus",
    "perplexity",
    "openai",
    "chatgpt",
    "локальный российский ии",
    "российский ии",
)


def is_identity_question(text: str) -> bool:
    """Возвращает True, если запрос — вопрос об идентичности («кто ты», «откуда» и т.п.)."""
    if not text or not isinstance(text, str):
        return False
    lowered = text.strip().lower()
    if len(lowered) > 200:
        return False
    return any(m in lowered for m in _IDENTITY_QUESTION_MARKERS)


def contains_forbidden_identity_mention(text: str) -> bool:
    """True, если в тексте есть запрещённые упоминания (компании, продукты, провайдеры)."""
    if not text or not isinstance(text, str):
        return False
    lowered = text.lower()
    return any(p in lowered for p in FORBIDDEN_IDENTITY_PATTERNS)
