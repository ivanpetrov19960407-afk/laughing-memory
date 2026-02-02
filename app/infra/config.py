from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/orchestrator.json")
DEFAULT_DB_PATH = Path("data/bot.db")


@dataclass(frozen=True)
class Settings:
    bot_token: str
    orchestrator_config_path: Path
    db_path: Path
    perplexity_api_key: str | None
    perplexity_base_url: str
    perplexity_model: str
    perplexity_timeout_seconds: float
    allowed_user_ids: set[int]
    llm_per_minute: int | None
    llm_per_day: int | None
    llm_history_turns: int | None
    rate_limit_per_minute: int
    rate_limit_per_day: int
    history_size: int
    telegram_message_limit: int


def load_settings() -> Settings:
    _load_dotenv()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")

    config_path = Path(os.getenv("ORCHESTRATOR_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    db_path = Path(os.getenv("BOT_DB_PATH", DEFAULT_DB_PATH))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    perplexity_api_key = os.getenv("PERPLEXITY_API_KEY") or None
    perplexity_base_url = os.getenv("PERPLEXITY_BASE_URL", "https://api.perplexity.ai")
    perplexity_model = os.getenv("PERPLEXITY_MODEL", "sonar")
    perplexity_timeout_seconds = _parse_optional_float(os.getenv("PERPLEXITY_TIMEOUT_SECONDS"), 15.0)
    allowed_user_ids_raw = os.getenv("ALLOWED_USER_IDS")
    allowed_user_ids = _parse_int_set(allowed_user_ids_raw)
    if not allowed_user_ids:
        LOGGER.error("ALLOWED_USER_IDS is empty; denying all users.")
    llm_per_minute = _parse_optional_int(os.getenv("LLM_PER_MINUTE"))
    llm_per_day = _parse_optional_int(os.getenv("LLM_PER_DAY"))
    llm_history_turns = _parse_optional_int(os.getenv("LLM_HISTORY_TURNS"))
    rate_limit_per_minute = _parse_int_with_default(os.getenv("RATE_LIMIT_PER_MINUTE"), 6)
    rate_limit_per_day = _parse_int_with_default(os.getenv("RATE_LIMIT_PER_DAY"), 80)
    history_size = _parse_int_with_default(os.getenv("HISTORY_SIZE"), 10)
    telegram_message_limit = _parse_int_with_default(os.getenv("TELEGRAM_MESSAGE_LIMIT"), 4000)

    return Settings(
        bot_token=token,
        orchestrator_config_path=config_path,
        db_path=db_path,
        perplexity_api_key=perplexity_api_key,
        perplexity_base_url=perplexity_base_url,
        perplexity_model=perplexity_model,
        perplexity_timeout_seconds=perplexity_timeout_seconds,
        allowed_user_ids=allowed_user_ids,
        llm_per_minute=llm_per_minute,
        llm_per_day=llm_per_day,
        llm_history_turns=llm_history_turns,
        rate_limit_per_minute=rate_limit_per_minute,
        rate_limit_per_day=rate_limit_per_day,
        history_size=history_size,
        telegram_message_limit=telegram_message_limit,
    )


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        LOGGER.debug("python-dotenv is not installed; skipping .env loading")
        return
    load_dotenv()


def _parse_int_set(value: str | None) -> set[int]:
    if value is None:
        return set()
    raw = [item.strip() for item in value.split(",") if item.strip()]
    return {int(item) for item in raw}


def _parse_optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return int(trimmed)


def _parse_optional_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    trimmed = value.strip()
    if not trimmed:
        return default
    return float(trimmed)


def _parse_int_with_default(value: str | None, default: int) -> int:
    if value is None:
        return default
    trimmed = value.strip()
    if not trimmed:
        return default
    return int(trimmed)
