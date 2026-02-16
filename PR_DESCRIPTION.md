# PR: Stage 5 — аудит и доведение до готово в проде

## Checklist (где что находится)

| Компонент | Расположение |
|-----------|--------------|
| Recurring reminders (wizard, создание, recurrence) | `app/bot/wizard.py` (STEP_AWAIT_RECURRENCE, _reminder_recurrence_actions, _parse_recurrence_input); `app/core/calendar_store.py` (recurrence, _next_recurrence_trigger, mark_reminder_sent → next) |
| Notification inline keyboard (snooze / reschedule / delete) | `app/core/reminders.py` (_build_reminder_actions); `app/core/reminder_scheduler.py` (_build_reminder_actions); `app/bot/handlers.py` (_reminder_snooze_menu_actions, _handle_reminder_snooze, _handle_reminder_snooze_tomorrow, _handle_reminder_reschedule_start, _handle_reminder_delete) |
| Daily digest scheduler | `app/core/daily_digest.py` (run_daily_digest); запуск в `app/main.py` (job_queue.run_daily, 05:00 UTC = 08:00 МСК) |
| Toggles / профиль (digest вкл/выкл) | `app/core/user_profile.py` (digest_enabled, digest_chat_id, last_digest_sent_date); `app/bot/handlers.py` (_handle_digest_toggle); меню «Напоминания» — кнопка «📬 Дайджест: вкл/выкл» |
| Callback безопасность (cb:, ≤64 байт, answer_callback_query) | `app/bot/actions.py` (STATIC_CALLBACK_PREFIX, build_static_callback_data, проверка len(data.encode("utf-8")) > 64); `app/bot/handlers.py` (_safe_answer_callback в static_callback и action_callback) |
| Snooze от now, reschedule job | `app/core/calendar_store.py` (apply_snooze: base = max(current_now, base_trigger_at or current_trigger)); `app/bot/handlers.py` (_handle_reminder_snooze передаёт now=datetime.now(BOT_TZ)); после apply_snooze вызывается scheduler.schedule_reminder(updated) |

## Изменения в коде

- **UX список напоминаний**: в выводе списка добавлен признак повторяемости и следующий триггер (через `wizard._recurrence_label`).
- **reminder_scheduler.py**: в payload кнопок унифицирован ключ `reminder_id` (вместо `id`) для совместимости с handlers.
- **Daily digest**: добавлены поля профиля `digest_enabled`, `digest_chat_id`, `last_digest_sent_date`; `list_reminders_for_day` в calendar_store; модуль `daily_digest.py` и регистрация job в main; кнопка и обработчик переключения дайджеста в меню «Напоминания».
- **Документация**: README (секция напоминаний + ежедневный дайджест); `docs/stages.md` — список этапов, Stage 5 ✅.

## Как проверить вручную

1. **Разовое напоминание**: Меню → Напоминания → ➕ Создать → текст, дата/время, «Без повтора» → подтвердить. Дождаться срабатывания (или сдвинуть время в тестах). В сообщении нажать ⏸ Отложить → выбрать интервал; ✏ Перенести → ввести новое время; 🗑 Удалить — напоминание исчезнет.
2. **Повторяемое (daily)**: Создать с повтором «Ежедневно». После срабатывания должно перепланироваться следующее; кнопки не должны ломать серию.
3. **Дайджест**: Меню → Напоминания → 📬 Дайджест: выкл → включить. На следующий день около 8:00 МСК (или при ручном запуске job на тесте) должна прийти одна сводка на сегодня. Если напоминаний на сегодня нет — сообщение не отправляется.

## Тесты

На сервере после мержа: `pytest -q` — все тесты должны оставаться зелёными (379 passed). Новые поля профиля и digest совместимы со старым payload (from_dict/to_dict, apply_profile_patch).
