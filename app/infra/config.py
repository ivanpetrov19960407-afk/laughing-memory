from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/orchestrator.json")
DEFAULT_DB_PATH = Path("data/bot.db")
DEFAULT_ALLOWLIST_PATH = Path("data/allowlist.json")
DEFAULT_DIALOG_MEMORY_PATH = Path("data/dialog_memory.json")


@dataclass(frozen=True)
class Settings:
    bot_token: str
    orchestrator_config_path: Path
    db_path: Path
    openai_api_key: str | None
    openai_model: str
    openai_image_model: str
    openai_timeout_seconds: float
    perplexity_api_key: str | None
    perplexity_base_url: str
    perplexity_model: str
    perplexity_timeout_seconds: float
    allowed_user_ids: set[int]
    admin_user_ids: set[int]
    allowlist_path: Path
    llm_per_minute: int | None
    llm_per_day: int | None
    llm_history_turns: int | None
    facts_only_default: bool | None
    rate_limit_per_minute: int
    rate_limit_per_day: int
    history_size: int
    telegram_message_limit: int
    dialog_memory_path: Path
    context_max_turns: int
    reminders_enabled: bool
    reminder_default_offset_minutes: int
    reminder_max_future_days: int
    action_ttl_seconds: int
    action_max_size: int


def load_settings() -> Settings:
    _load_dotenv()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")

    config_path = Path(os.getenv("ORCHESTRATOR_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    db_path = Path(os.getenv("BOT_DB_PATH", DEFAULT_DB_PATH))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    openai_api_key = os.getenv("OPENAI_API_KEY") or None
    openai_model = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
    openai_image_model = os.getenv("OPENAI_IMAGE_MODEL", "dall-e-3")
    openai_timeout_seconds = _parse_optional_float(os.getenv("OPENAI_TIMEOUT_SECONDS"), 30.0)
    perplexity_api_key = os.getenv("PERPLEXITY_API_KEY") or None
    perplexity_base_url = os.getenv("PERPLEXITY_BASE_URL", "https://api.perplexity.ai")
    perplexity_model = os.getenv("PERPLEXITY_MODEL", "sonar")
    perplexity_timeout_seconds = _parse_optional_float(os.getenv("PERPLEXITY_TIMEOUT_SECONDS"), 15.0)
    allowed_user_ids_raw = os.getenv("ALLOWED_USER_IDS")
    allowed_user_ids = _parse_int_set(allowed_user_ids_raw)
    admin_user_ids_raw = os.getenv("ADMIN_USER_IDS")
    admin_user_ids = _parse_int_set(admin_user_ids_raw)
    allowlist_path = Path(os.getenv("ALLOWLIST_PATH", DEFAULT_ALLOWLIST_PATH))
    llm_per_minute = _parse_optional_int(os.getenv("LLM_PER_MINUTE"))
    llm_per_day = _parse_optional_int(os.getenv("LLM_PER_DAY"))
    llm_history_turns = _parse_optional_int(os.getenv("LLM_HISTORY_TURNS"))
    facts_only_default = _parse_optional_bool(os.getenv("FACTS_ONLY_DEFAULT"))
    rate_limit_per_minute = _parse_int_with_default(os.getenv("RATE_LIMIT_PER_MINUTE"), 10)
    rate_limit_per_day = _parse_int_with_default(os.getenv("RATE_LIMIT_PER_DAY"), 200)
    history_size = _parse_int_with_default(os.getenv("HISTORY_SIZE"), 10)
    telegram_message_limit = _parse_int_with_default(os.getenv("TELEGRAM_MESSAGE_LIMIT"), 4000)
    dialog_memory_path = Path(os.getenv("DIALOG_MEMORY_PATH", DEFAULT_DIALOG_MEMORY_PATH))
    context_max_turns = _parse_int_with_default(os.getenv("CONTEXT_MAX_TURNS"), 5)
    reminders_enabled = _parse_optional_bool(os.getenv("REMINDERS_ENABLED"))
    if reminders_enabled is None:
        reminders_enabled = True
    reminder_default_offset_minutes = _parse_int_with_default(
        os.getenv("REMINDER_DEFAULT_OFFSET_MINUTES"),
        10,
    )
    reminder_max_future_days = _parse_int_with_default(
        os.getenv("REMINDER_MAX_FUTURE_DAYS"),
        365,
    )
    action_ttl_seconds = _parse_int_with_default(os.getenv("ACTION_TTL_SECONDS"), 600)
    action_max_size = _parse_int_with_default(os.getenv("ACTION_MAX_SIZE"), 2000)

    return Settings(
        bot_token=token,
        orchestrator_config_path=config_path,
        db_path=db_path,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        openai_image_model=openai_image_model,
        openai_timeout_seconds=openai_timeout_seconds,
        perplexity_api_key=perplexity_api_key,
        perplexity_base_url=perplexity_base_url,
        perplexity_model=perplexity_model,
        perplexity_timeout_seconds=perplexity_timeout_seconds,
        allowed_user_ids=allowed_user_ids,
        admin_user_ids=admin_user_ids,
        allowlist_path=allowlist_path,
        llm_per_minute=llm_per_minute,
        llm_per_day=llm_per_day,
        llm_history_turns=llm_history_turns,
        facts_only_default=facts_only_default,
        rate_limit_per_minute=rate_limit_per_minute,
        rate_limit_per_day=rate_limit_per_day,
        history_size=history_size,
        telegram_message_limit=telegram_message_limit,
        dialog_memory_path=dialog_memory_path,
        context_max_turns=context_max_turns,
        reminders_enabled=reminders_enabled,
        reminder_default_offset_minutes=reminder_default_offset_minutes,
        reminder_max_future_days=reminder_max_future_days,
        action_ttl_seconds=action_ttl_seconds,
        action_max_size=action_max_size,
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


def _parse_optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    trimmed = value.strip().lower()
    if not trimmed:
        return None
    return trimmed in {"1", "true", "yes", "on"}


def _parse_int_with_default(value: str | None, default: int) -> int:
    if value is None:
        return default
    trimmed = value.strip()
    if not trimmed:
        return default
    return int(trimmed)
