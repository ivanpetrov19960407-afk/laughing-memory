from __future__ import annotations

import logging
import os

from app import main as main_module


def test_dry_run_mode_skips_telegram_polling(monkeypatch, caplog) -> None:
    # Ensure no real token is required in DRY_RUN
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("DRY_RUN", "1")

    called = {"run_polling": False}

    def _fake_run_polling(self, *args, **kwargs):  # type: ignore[override]
        called["run_polling"] = True

    monkeypatch.setattr(main_module.Application, "run_polling", _fake_run_polling)

    caplog.set_level(logging.INFO)

    # Should not raise even without BOT_TOKEN and should not call run_polling
    main_module.main()

    assert called["run_polling"] is False
    # startup.check is logged as part of the normal startup sequence
    assert any("startup.check" in record.message for record in caplog.records)
    # DRY_RUN marker should be clearly visible in logs
    assert any("DRY_RUN" in record.message for record in caplog.records)

