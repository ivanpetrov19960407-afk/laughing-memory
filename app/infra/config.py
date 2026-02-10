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
DEFAULT_UPLOADS_PATH = Path("data/uploads")
DEFAULT_DOCUMENT_TEXTS_PATH = Path("data/document_texts")
DEFAULT_DOCUMENT_SESSIONS_PATH = Path("data/document_sessions.json")


@dataclass(frozen=True)
class Settings:
    bot_token: str
    orchestrator_config_path: Path
    db_path: Path
    openai_api_key: str | None
    openai_model: str
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
    enable_wizards: bool
    enable_menu: bool
    strict_no_pseudo_sources: bool
    wizard_store_path: Path
    wizard_timeout_seconds: int
    feature_web_search: bool
    uploads_path: Path
    document_texts_path: Path
    document_sessions_path: Path
    ocr_enabled: bool
    calendar_backend: str
    caldav_url: str | None
    caldav_username: str | None
    caldav_password: str | None
    caldav_calendar_name: str | None


@dataclass(frozen=True)
class StartupFeatures:
    caldav_enabled: bool
    llm_enabled: bool


_DEV_ENVS = {"dev", "development", "local"}


def resolve_env_label(raw_env: dict[str, str] | None = None) -> str:
    source = raw_env if raw_env is not None else os.environ
    env = source.get("APP_ENV", "prod").strip().lower()
    return "dev" if env in _DEV_ENVS else "prod"


def validate_startup_env(
    settings: Settings,
    *,
    env_label: str | None = None,
    raw_env: dict[str, str] | None = None,
    logger: logging.Logger | None = None,
) -> StartupFeatures:
    log = logger or LOGGER
    env_source = raw_env if raw_env is not None else os.environ
    label = env_label or resolve_env_label(raw_env)

    # In DRY_RUN mode we allow missing BOT_TOKEN so Docker smoke tests
    # can run without real Telegram credentials.
    dry_run = _parse_optional_bool(env_source.get("DRY_RUN")) is True
    if not dry_run and not settings.bot_token:
        log.error("startup.env invalid: BOT_TOKEN missing")
        raise SystemExit("BOT_TOKEN is not set")
    if not settings.orchestrator_config_path.exists():
        log.error("startup.env invalid: config missing path=%s", settings.orchestrator_config_path)
        raise SystemExit("ORCHESTRATOR_CONFIG_PATH is invalid")

    dev_mode = _parse_optional_bool(env_source.get("DEV_MODE"))
    if label == "prod" and dev_mode is True:
        log.error("startup.env mismatch: prod env with DEV_MODE=true")
        raise SystemExit("DEV_MODE cannot be enabled in prod")
    if label == "dev" and dev_mode is False:
        log.error("startup.env mismatch: dev env with DEV_MODE=false")
        raise SystemExit("DEV_MODE must be enabled in dev")

    caldav_configured = bool(settings.caldav_url and settings.caldav_username and settings.caldav_password)
    caldav_enabled = settings.calendar_backend == "caldav" and caldav_configured
    if settings.calendar_backend == "caldav" and not caldav_configured:
        log.warning("startup.env caldav disabled: missing CALDAV_URL/USERNAME/PASSWORD")
        os.environ["CALENDAR_BACKEND"] = "local"

    llm_enabled = bool(settings.openai_api_key or settings.perplexity_api_key)
    if not llm_enabled:
        log.warning("startup.env llm disabled: no API key configured")

    return StartupFeatures(
        caldav_enabled=caldav_enabled,
        llm_enabled=llm_enabled,
    )


def load_settings() -> Settings:
    _load_dotenv()

    env = os.environ
    dry_run = _parse_optional_bool(env.get("DRY_RUN")) is True

    token = env.get("BOT_TOKEN")
    if not token:
        if dry_run:
            # For DRY_RUN we don't need a real Telegram token; use a placeholder
            # so downstream code that expects a non-empty string continues to work.
            token = "000000:DRY_RUN_TOKEN"
        else:
            raise RuntimeError("BOT_TOKEN is not set")

    config_path = Path(env.get("ORCHESTRATOR_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    db_path = Path(env.get("BOT_DB_PATH", DEFAULT_DB_PATH))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    openai_api_key = env.get("OPENAI_API_KEY") or None
    openai_model = env.get("OPENAI_MODEL", "gpt-3.5-turbo")
    openai_timeout_seconds = _parse_optional_float(env.get("OPENAI_TIMEOUT_SECONDS"), 30.0)
    perplexity_api_key = env.get("PERPLEXITY_API_KEY") or None
    perplexity_base_url = env.get("PERPLEXITY_BASE_URL", "https://api.perplexity.ai")
    perplexity_model = env.get("PERPLEXITY_MODEL", "sonar")
    perplexity_timeout_seconds = _parse_optional_float(env.get("PERPLEXITY_TIMEOUT_SECONDS"), 15.0)
    allowed_user_ids_raw = env.get("ALLOWED_USER_IDS")
    allowed_user_ids = _parse_int_set(allowed_user_ids_raw)
    admin_user_ids_raw = env.get("ADMIN_USER_IDS")
    admin_user_ids = _parse_int_set(admin_user_ids_raw)
    allowlist_path = Path(env.get("ALLOWLIST_PATH", DEFAULT_ALLOWLIST_PATH))
    llm_per_minute = _parse_optional_int(env.get("LLM_PER_MINUTE"))
    llm_per_day = _parse_optional_int(env.get("LLM_PER_DAY"))
    llm_history_turns = _parse_optional_int(env.get("LLM_HISTORY_TURNS"))
    facts_only_default = _parse_optional_bool(env.get("FACTS_ONLY_DEFAULT"))
    rate_limit_per_minute = _parse_int_with_default(env.get("RATE_LIMIT_PER_MINUTE"), 10)
    rate_limit_per_day = _parse_int_with_default(env.get("RATE_LIMIT_PER_DAY"), 200)
    history_size = _parse_int_with_default(env.get("HISTORY_SIZE"), 10)
    telegram_message_limit = _parse_int_with_default(env.get("TELEGRAM_MESSAGE_LIMIT"), 4000)
    dialog_memory_path = Path(env.get("DIALOG_MEMORY_PATH", DEFAULT_DIALOG_MEMORY_PATH))
    context_max_turns = _parse_int_with_default(env.get("CONTEXT_MAX_TURNS"), 5)
    reminders_enabled = _parse_optional_bool(env.get("REMINDERS_ENABLED"))
    if reminders_enabled is None:
        reminders_enabled = True
    reminder_default_offset_minutes = _parse_int_with_default(
        env.get("REMINDER_DEFAULT_OFFSET_MINUTES"),
        10,
    )
    reminder_max_future_days = _parse_int_with_default(
        env.get("REMINDER_MAX_FUTURE_DAYS"),
        365,
    )
    action_ttl_seconds = _parse_int_with_default(env.get("ACTION_TTL_SECONDS"), 900)
    action_max_size = _parse_int_with_default(env.get("ACTION_MAX_SIZE"), 2000)
    enable_wizards = _parse_optional_bool(env.get("ENABLE_WIZARDS"))
    if enable_wizards is None:
        enable_wizards = True
    enable_menu = _parse_optional_bool(env.get("ENABLE_MENU"))
    if enable_menu is None:
        enable_menu = True
    strict_no_pseudo_sources = _parse_optional_bool(env.get("STRICT_NO_PSEUDO_SOURCES"))
    if strict_no_pseudo_sources is None:
        strict_no_pseudo_sources = True
    wizard_store_path = Path(env.get("WIZARD_STORE_PATH", "data/wizards"))
    wizard_timeout_seconds = _parse_int_with_default(env.get("WIZARD_TIMEOUT_SECONDS"), 600)
    feature_web_search = _parse_optional_bool(env.get("FEATURE_WEB_SEARCH"))
    if feature_web_search is None:
        feature_web_search = True
    uploads_path = Path(env.get("UPLOADS_PATH", DEFAULT_UPLOADS_PATH))
    document_texts_path = Path(env.get("DOCUMENT_TEXTS_PATH", DEFAULT_DOCUMENT_TEXTS_PATH))
    document_sessions_path = Path(
        env.get("DOCUMENT_SESSIONS_PATH", DEFAULT_DOCUMENT_SESSIONS_PATH)
    )
    ocr_enabled = _parse_optional_bool(env.get("OCR_ENABLED"))
    if ocr_enabled is None:
        ocr_enabled = True
    calendar_backend = env.get("CALENDAR_BACKEND", "local").strip().lower()
    if calendar_backend not in {"local", "caldav"}:
        calendar_backend = "local"
    caldav_url = env.get("CALDAV_URL") or None
    caldav_username = env.get("CALDAV_USERNAME") or None
    caldav_password = env.get("CALDAV_PASSWORD") or None
    caldav_calendar_name = env.get("CALDAV_CALENDAR_NAME") or None
    return Settings(
        bot_token=token,
        orchestrator_config_path=config_path,
        db_path=db_path,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
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
        enable_wizards=enable_wizards,
        enable_menu=enable_menu,
        strict_no_pseudo_sources=strict_no_pseudo_sources,
        wizard_store_path=wizard_store_path,
        wizard_timeout_seconds=wizard_timeout_seconds,
        feature_web_search=feature_web_search,
        uploads_path=uploads_path,
        document_texts_path=document_texts_path,
        document_sessions_path=document_sessions_path,
        ocr_enabled=ocr_enabled,
        calendar_backend=calendar_backend,
        caldav_url=caldav_url,
        caldav_username=caldav_username,
        caldav_password=caldav_password,
        caldav_calendar_name=caldav_calendar_name,
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
