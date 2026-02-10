"""Единый модуль пресетов snooze для напоминаний. Один источник правды для 5m, 15m, 30m, 60m."""

from __future__ import annotations

from app.core.result import Action

# Пресеты в минутах; не дублировать в других модулях.
SNOOZE_PRESET_MINUTES = (5, 15, 30, 60)

# Максимальная длина callback data в Telegram — 64 байта. Payload хранится на сервере (action_store).
REMINDER_SNOOZE_OP = "reminder_snooze"
REMINDER_SNOOZE_MENU_OP = "reminder_snooze_menu"


def build_snooze_preset_actions(
    reminder_id: str,
    *,
    base_trigger_at: str | None = None,
    include_tomorrow: bool = False,
) -> list[Action]:
    """Строит список кнопок snooze: 5m, 15m, 30m, 60m (+ опционально «Завтра утром»)."""
    base_payload: dict[str, object] = {"op": REMINDER_SNOOZE_OP, "reminder_id": reminder_id}
    if base_trigger_at:
        base_payload["base_trigger_at"] = base_trigger_at
    actions = []
    for minutes in SNOOZE_PRESET_MINUTES:
        label = _preset_label(minutes)
        actions.append(
            Action(
                id=_snooze_action_id(reminder_id, minutes),
                label=label,
                payload={**base_payload, "minutes": minutes},
            )
        )
    if include_tomorrow:
        actions.append(
            Action(
                id=f"reminder_snooze:{reminder_id}:tomorrow",
                label="Завтра утром",
                payload={
                    "op": "reminder_snooze_tomorrow",
                    "reminder_id": reminder_id,
                    "base_trigger_at": base_trigger_at,
                },
            )
        )
    return actions


def _preset_label(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} мин"
    return "1 час"


def _snooze_action_id(reminder_id: str, minutes: int) -> str:
    """Короткий статичный id для callback; только reminder_id и число."""
    return f"snz:{reminder_id}:{minutes}"
