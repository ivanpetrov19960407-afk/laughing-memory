"""
Единая системная идентичность бота. Применяется ко всем вызовам LLM.
Запрет на упоминание компаний, продуктов, провайдеров ИИ.
Ответы на «кто ты» / «откуда» — только по эталонному шаблону.
"""
from __future__ import annotations

import re

# Короткое описание бота для system prompt и приветствия
BOT_IDENTITY_DESCRIPTION = "Ты Telegram-бот-оркестратор задач и инструментов."

# Единственный допустимый ответ на вопросы об идентичности (кто ты, откуда, кто сделал)
IDENTITY_ANSWER_TEMPLATE = (
    "Я Telegram-бот-оркестратор задач и инструментов. "
    "Помогаю с задачами, напоминаниями и поиском. Основной вход — /menu."
)

# Правила для LLM: запрет на выдумывание идентичности
SYSTEM_IDENTITY_RULES = (
    "Запрещено называть себя другими именами, компаниями или продуктами. "
    "Запрещено упоминать провайдеров ИИ, бренды (Perplexity, OpenAI, Авандок, КОРУС и т.п.). "
    "На вопросы «кто ты», «откуда», «кто тебя сделал» не придумывай ответ — их обрабатывает система отдельно."
)

# Минимальная длина осмысленного поискового запроса (символы)
SEARCH_QUERY_MIN_LENGTH = 10

_IDENTITY_PATTERNS = [
    re.compile(r"(?i)\b(кто\s+ты|кто\s+такой|что\s+ты\s+за\s+бот|ты\s+кто)\b"),
    re.compile(r"(?i)\b(откуда\s+ты|кто\s+тебя\s+сделал|кто\s+тебя\s+создал|кто\s+тебя\s+написал)\b"),
    re.compile(r"(?i)\b(who\s+are\s+you|what\s+are\s+you|who\s+made\s+you|who\s+created\s+you)\b"),
    re.compile(r"(?i)\b(какой\s+ты\s+ии|какой\s+ии\s+ты|ты\s+perplexity|ты\s+chatgpt)\b"),
]


def is_identity_question(text: str) -> bool:
    """Проверяет, является ли запрос вопросом об идентичности бота."""
    if not text or not text.strip():
        return False
    lowered = text.strip().lower()
    for pattern in _IDENTITY_PATTERNS:
        if pattern.search(lowered):
            return True
    return False


def get_system_prompt_for_llm(extra_instructions: str = "") -> str:
    """
    Единый system prompt для всех вызовов LLM.
    extra_instructions — доп. инструкции для конкретного режима (поиск, переписывание и т.д.).
    """
    parts = [BOT_IDENTITY_DESCRIPTION, SYSTEM_IDENTITY_RULES]
    if extra_instructions:
        parts.append(extra_instructions)
    return "\n".join(parts)


def is_search_query_ambiguous(query: str) -> bool:
    """True, если запрос слишком короткий или неоднозначный для поиска."""
    trimmed = (query or "").strip()
    if len(trimmed) < SEARCH_QUERY_MIN_LENGTH:
        return True
    # Один-два слова без вопроса — часто неоднозначно
    words = trimmed.split()
    if len(words) <= 2 and "?" not in trimmed and "что" not in trimmed.lower() and "как" not in trimmed.lower():
        return True
    return False


# Подстроки, наличие которых в ответе LLM трактуется как галлюцинация идентичности → refused
_FORBIDDEN_IDENTITY_MENTIONS = (
    "авандок",
    "корус",
    "perplexity",
    "openai",
    "chatgpt",
    "gpt-",
    "локальный российский ии",
    "российский ии",
    "сделал меня",
    "создал меня",
    "написал меня",
)


def contains_forbidden_identity_mention(text: str) -> bool:
    """True, если в тексте есть упоминание компаний/провайдеров/выдуманной идентичности."""
    if not text:
        return False
    lowered = text.lower()
    return any(mention in lowered for mention in _FORBIDDEN_IDENTITY_MENTIONS)
