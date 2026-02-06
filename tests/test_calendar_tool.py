from __future__ import annotations

import asyncio
from datetime import datetime

from app.core import calendar_store
from app.core.tools_calendar import create_event


def test_calendar_tool_refuses_when_not_connected(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.delenv("CALENDAR_CONNECTED", raising=False)

    result = asyncio.run(
        create_event(
            start_at=datetime(2026, 2, 5, 18, 30, tzinfo=calendar_store.BOT_TZ),
            title="Врач",
            chat_id=10,
            user_id=1,
            request_id="req-1",
            intent="utility_calendar.add",
        )
    )

    assert result.status == "refused"
    assert "Календарь не подключён" in result.text


def test_calendar_tool_creates_event_when_connected(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    monkeypatch.setenv("CALENDAR_CONNECTED", "1")

    result = asyncio.run(
        create_event(
            start_at=datetime(2026, 2, 5, 18, 30, tzinfo=calendar_store.BOT_TZ),
            title="Врач",
            chat_id=10,
            user_id=1,
            request_id="req-2",
            intent="utility_calendar.add",
        )
    )

    assert result.status == "ok"
    assert "Событие добавлено" in result.text
