from __future__ import annotations

import asyncio
import time
from datetime import datetime

from app.core import calendar_store
from app.core.tools_calendar import create_event
from app.stores.google_tokens import GoogleTokenStore, GoogleTokens


def test_calendar_tool_refuses_when_not_connected(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    tokens_path = tmp_path / "google_tokens.db"
    monkeypatch.setenv("GOOGLE_TOKENS_PATH", str(tokens_path))

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


def test_calendar_tool_refreshes_token_when_expired(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    tokens_path = tmp_path / "google_tokens.db"
    monkeypatch.setenv("GOOGLE_TOKENS_PATH", str(tokens_path))
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_PATH", "/oauth2/callback")
    store = GoogleTokenStore(tokens_path)
    store.set_tokens(
        1,
        GoogleTokens(
            access_token="expired-token",
            refresh_token="refresh-1",
            expires_at=time.time() - 10,
        ),
    )
    refreshed_tokens = {"access_token": "new-token", "expires_in": 3600}
    monkeypatch.setattr("app.core.tools_calendar.refresh_access_token", lambda *args, **kwargs: refreshed_tokens)
    captured: dict[str, str] = {}

    async def fake_create_event(*, access_token: str, start_at: datetime, title: str) -> dict[str, object]:
        captured["access_token"] = access_token
        return {"id": "evt-1"}

    monkeypatch.setattr("app.core.tools_calendar._create_google_event", fake_create_event)

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
    assert captured["access_token"] == "new-token"
    updated = store.get_tokens(1)
    assert updated is not None
    assert updated.access_token == "new-token"


def test_calendar_tool_calls_google_api(tmp_path, monkeypatch) -> None:
    calendar_path = tmp_path / "calendar.json"
    monkeypatch.setenv("CALENDAR_PATH", str(calendar_path))
    tokens_path = tmp_path / "google_tokens.db"
    monkeypatch.setenv("GOOGLE_TOKENS_PATH", str(tokens_path))
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_PATH", "/oauth2/callback")
    store = GoogleTokenStore(tokens_path)
    store.set_tokens(
        1,
        GoogleTokens(
            access_token="valid-token",
            refresh_token="refresh-1",
            expires_at=time.time() + 3600,
        ),
    )

    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"id": "evt-2"}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs
            self.captured: dict[str, object] = {}

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, json: dict[str, object], headers: dict[str, str]) -> FakeResponse:
            self.captured = {"url": url, "json": json, "headers": headers}
            return FakeResponse()

    fake_client = FakeClient()

    monkeypatch.setattr("app.core.tools_calendar.httpx.AsyncClient", lambda *args, **kwargs: fake_client)

    result = asyncio.run(
        create_event(
            start_at=datetime(2026, 2, 5, 18, 30, tzinfo=calendar_store.BOT_TZ),
            title="Врач",
            chat_id=10,
            user_id=1,
            request_id="req-3",
            intent="utility_calendar.add",
        )
    )

    assert result.status == "ok"
    assert fake_client.captured["url"].endswith("/calendars/primary/events")
    assert fake_client.captured["headers"]["Authorization"] == "Bearer valid-token"
    payload = fake_client.captured["json"]
    assert payload["start"]["timeZone"] == "Europe/Vilnius"
