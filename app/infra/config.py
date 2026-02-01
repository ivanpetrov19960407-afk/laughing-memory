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

    return Settings(
        bot_token=token,
        orchestrator_config_path=config_path,
        db_path=db_path,
        perplexity_api_key=perplexity_api_key,
        perplexity_base_url=perplexity_base_url,
    )


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        LOGGER.debug("python-dotenv is not installed; skipping .env loading")
        return
    load_dotenv()
