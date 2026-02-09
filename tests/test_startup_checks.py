from __future__ import annotations

import logging
import os
from dataclasses import replace
from pathlib import Path

import pytest

from app.infra.config import Settings, validate_startup_env


def _build_settings(tmp_path: Path) -> Settings:
    config_path = tmp_path / "orchestrator.json"
    config_path.write_text("{}", encoding="utf-8")
    return Settings(
        bot_token="token",
        orchestrator_config_path=config_path,
        db_path=tmp_path / "bot.db",
        openai_api_key=None,
        openai_model="gpt-3.5-turbo",
        openai_timeout_seconds=30.0,
        perplexity_api_key=None,
        perplexity_base_url="https://api.perplexity.ai",
        perplexity_model="sonar",
        perplexity_timeout_seconds=15.0,
        allowed_user_ids=set(),
        admin_user_ids=set(),
        allowlist_path=tmp_path / "allowlist.json",
        llm_per_minute=None,
        llm_per_day=None,
        llm_history_turns=None,
        facts_only_default=None,
        rate_limit_per_minute=10,
        rate_limit_per_day=200,
        history_size=10,
        telegram_message_limit=4000,
        dialog_memory_path=tmp_path / "dialog_memory.json",
        context_max_turns=5,
        reminders_enabled=True,
        reminder_default_offset_minutes=10,
        reminder_max_future_days=365,
        action_ttl_seconds=900,
        action_max_size=2000,
        actions_log_ttl_days=30,
        enable_wizards=True,
        enable_menu=True,
        strict_no_pseudo_sources=True,
        wizard_store_path=tmp_path / "wizards",
        wizard_timeout_seconds=600,
        feature_web_search=True,
        uploads_path=tmp_path / "uploads",
        document_texts_path=tmp_path / "document_texts",
        document_sessions_path=tmp_path / "document_sessions.json",
        ocr_enabled=True,
        calendar_backend="local",
        caldav_url=None,
        caldav_username=None,
        caldav_password=None,
        caldav_calendar_name=None,
        obs_http_enabled=False,
        obs_http_host="127.0.0.1",
        obs_http_port=8080,
        otel_enabled=False,
        otel_exporter="console",
        otel_otlp_endpoint=None,
        systemd_watchdog_enabled=False,
    )


def test_validate_startup_env_missing_critical(tmp_path) -> None:
    settings = _build_settings(tmp_path)
    broken = replace(settings, bot_token="")

    with pytest.raises(SystemExit):
        validate_startup_env(broken, env_label="prod")


def test_validate_startup_env_optional_warning(tmp_path, caplog, monkeypatch) -> None:
    settings = _build_settings(tmp_path)
    settings = replace(settings, calendar_backend="caldav")

    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("CALENDAR_BACKEND", "caldav")
    features = validate_startup_env(settings, env_label="prod")

    assert features.caldav_enabled is False
    assert any("caldav disabled" in record.message for record in caplog.records)
    assert os.environ.get("CALENDAR_BACKEND") == "local"
