from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core import calendar_store
from app.core.daily_digest import DIGEST_TZ
from app.core import digest_scheduler
from app.infra.user_profile_store import UserProfileStore


@pytest.fixture
def calendar_path(tmp_path, monkeypatch) -> str:
    path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(path))
    return str(path)


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def test_digest_sends_only_when_enabled_and_has_items(calendar_path, tmp_path, monkeypatch) -> None:
    profile_store = UserProfileStore(tmp_path / "profiles.db")
    user_id = 1
    chat_id = 123
    profile_store.update(user_id, {"daily_digest_enabled": True})

    now = datetime(2026, 2, 5, 9, 0, tzinfo=DIGEST_TZ)
    trigger_at = (now + timedelta(hours=1)).astimezone(calendar_store.BOT_TZ)
    asyncio_run(
        calendar_store.add_reminder(
            trigger_at=trigger_at,
            text="Сделать отчёт",
            chat_id=chat_id,
            user_id=user_id,
        )
    )

    sent_messages: list[tuple[int, str]] = []

    async def fake_send(_bot, cid: int, text: str, reply_markup=None):
        sent_messages.append((cid, text))

    monkeypatch.setattr(digest_scheduler, "safe_send_bot_text", fake_send)

    application = SimpleNamespace(bot=SimpleNamespace(), bot_data={"profile_store": profile_store})
    sent = asyncio_run(digest_scheduler._send_digests_for_enabled_users(application, now=now))
    assert sent == 1
    assert sent_messages
    assert sent_messages[0][0] == chat_id
    assert "Утренний дайджест" in sent_messages[0][1]
    assert profile_store.get(user_id).daily_digest_last_sent_date == "2026-02-05"

    # second run same day => no spam
    sent_messages.clear()
    sent2 = asyncio_run(digest_scheduler._send_digests_for_enabled_users(application, now=now + timedelta(minutes=5)))
    assert sent2 == 0
    assert sent_messages == []


def test_digest_silent_when_empty(calendar_path, tmp_path, monkeypatch) -> None:
    profile_store = UserProfileStore(tmp_path / "profiles.db")
    user_id = 7
    profile_store.update(user_id, {"daily_digest_enabled": True})

    now = datetime(2026, 2, 5, 9, 0, tzinfo=DIGEST_TZ)
    sent_messages: list[tuple[int, str]] = []

    async def fake_send(_bot, cid: int, text: str, reply_markup=None):
        sent_messages.append((cid, text))

    monkeypatch.setattr(digest_scheduler, "safe_send_bot_text", fake_send)
    application = SimpleNamespace(bot=SimpleNamespace(), bot_data={"profile_store": profile_store})
    sent = asyncio_run(digest_scheduler._send_digests_for_enabled_users(application, now=now))
    assert sent == 0
    assert sent_messages == []
    assert profile_store.get(user_id).daily_digest_last_sent_date is None


def test_digest_respects_toggle(calendar_path, tmp_path, monkeypatch) -> None:
    profile_store = UserProfileStore(tmp_path / "profiles.db")
    user_id = 2
    chat_id = 55
    profile_store.update(user_id, {"daily_digest_enabled": False})

    now = datetime(2026, 2, 5, 9, 0, tzinfo=DIGEST_TZ)
    trigger_at = (now + timedelta(hours=1)).astimezone(calendar_store.BOT_TZ)
    asyncio_run(
        calendar_store.add_reminder(
            trigger_at=trigger_at,
            text="Тест",
            chat_id=chat_id,
            user_id=user_id,
        )
    )

    sent_messages: list[tuple[int, str]] = []

    async def fake_send(_bot, cid: int, text: str, reply_markup=None):
        sent_messages.append((cid, text))

    monkeypatch.setattr(digest_scheduler, "safe_send_bot_text", fake_send)
    application = SimpleNamespace(bot=SimpleNamespace(), bot_data={"profile_store": profile_store})
    sent = asyncio_run(digest_scheduler._send_digests_for_enabled_users(application, now=now))
    assert sent == 0
    assert sent_messages == []

