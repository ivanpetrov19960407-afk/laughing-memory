from __future__ import annotations

import logging
from types import SimpleNamespace

from app.infra.request_context import log_request, start_request
from app.infra.rate_limiter import RateLimiter


class DummyContext:
    def __init__(self) -> None:
        self.application = SimpleNamespace(
            bot_data={
                "ui_rate_limiter": RateLimiter(),
                "rate_limiter": RateLimiter(),
            }
        )
        self.chat_data: dict[str, object] = {}


class DummyUpdate:
    def __init__(self) -> None:
        self.effective_user = SimpleNamespace(id=1, username="tester")
        self.effective_chat = SimpleNamespace(id=10)
        self.message = SimpleNamespace(text="Меню:")
        self.effective_message = self.message
        self.callback_query = SimpleNamespace(data="a:token")


def test_request_log_uses_callback_input_text(caplog) -> None:
    logger = logging.getLogger("test.request")
    caplog.set_level(logging.INFO, logger="test.request")
    update = DummyUpdate()
    context = DummyContext()
    request_context = start_request(update, context)

    log_request(logger, request_context)

    assert "text=\"a:token\"" in caplog.text
    assert "Меню:" not in caplog.text
