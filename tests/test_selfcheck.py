from __future__ import annotations

from types import SimpleNamespace

from app.bot import handlers


class DummyAllowlistStore:
    def __init__(self, allowed_user_ids: set[int] | None = None) -> None:
        self._allowed_user_ids = allowed_user_ids or set()

    def snapshot(self) -> SimpleNamespace:
        return SimpleNamespace(allowed_user_ids=self._allowed_user_ids)


def _build_context(settings: SimpleNamespace, *, llm_client: object | None) -> SimpleNamespace:
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "settings": settings,
                "allowlist_store": DummyAllowlistStore({1, 2}),
                "llm_client": llm_client,
            }
        )
    )


def test_selfcheck_includes_only_active_integrations() -> None:
    settings = SimpleNamespace(
        allowlist_path="data/allowlist.json",
        rate_limit_per_minute=10,
        rate_limit_per_day=200,
        history_size=10,
        telegram_message_limit=4000,
        calendar_backend="local",
        caldav_url=None,
        caldav_username=None,
        caldav_password=None,
    )
    context = _build_context(settings, llm_client=None)
    message = handlers._build_selfcheck_message(context)

    assert "INTEGRATIONS: none" in message
    assert "google" not in message


def test_selfcheck_reports_active_integrations() -> None:
    settings = SimpleNamespace(
        allowlist_path="data/allowlist.json",
        rate_limit_per_minute=10,
        rate_limit_per_day=200,
        history_size=10,
        telegram_message_limit=4000,
        calendar_backend="caldav",
        caldav_url="https://caldav.example.com",
        caldav_username="user",
        caldav_password="pass",
    )
    context = _build_context(settings, llm_client=object())
    message = handlers._build_selfcheck_message(context)

    assert "INTEGRATIONS: caldav, llm" in message
    assert "google" not in message
