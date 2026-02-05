# Аудит репозитория (Orchestrator v2) — Feb 5, 2026

Цель: автоматическая проверка репозитория Telegram-бота на архитектуре **Orchestrator v2** и минимальные исправления без изменения публичных контрактов.

## Базовая проверка (до изменений)
- `pytest`: **104 passed**

## Что проверено
- **Архитектура**: границы `core` (оркестрация/инструменты/контракты) и `bot` (Telegram UI), единый контракт результата.
- **Контракт `OrchestratorResult`**: все поля (`text`, `status`, `mode`, `intent`, `sources`, `actions`, `attachments`, `debug`) и места, где результат формируется/нормализуется.
- **Валидация**: что перед отправкой в UI всегда применяется `ensure_valid`, и нет обходов отправки результата “сырым dict/None”.
- **Безопасность/стабильность**: пустые сообщения, слишком длинный ввод, пустые `sources/actions/attachments`, статусы `ok/refused/error` (и фактическое использование `ratelimited`).

## Найдено и исправлено

### 1) `/search` мог падать с `TypeError` (неверный вызов `refused()`)
В `app/core/orchestrator.py` в нескольких ветках использовался вызов вида `refused("text", "usage", intent=...)`, что приводит к runtime-ошибке при выполнении (позиционный аргумент интерпретируется как `intent`, плюс конфликт с `intent=`).

- **Исправление**: приведено к одному `text` с корректным `intent`, например: `refused("Использование: /search <запрос>", intent=..., ...)`.
- **Покрыто тестом**: добавлен тест на `orchestrator.handle("/search", ...)` без payload.

Связанные изменения: `app/core/orchestrator.py`, `tests/test_search_pipeline.py`.

### 2) `ensure_valid()` некорректно нормализовал `ratelimited` и мог пропускать невалидный `mode`
Контракт `OrchestratorResult` (см. `app/core/result.py`) допускает `status="ratelimited"` (используется UI rate limiter в `app/bot/handlers.py`), но `ensure_valid()` раньше “тихо” переводил это в `error`. Также `mode` мог оставаться произвольной строкой, что нарушает `ResultMode`.

- **Исправление**:
  - `ensure_valid()` теперь **сохраняет** `status="ratelimited"` как часть контракта.
  - `ensure_valid()` теперь **клампит** `mode` к одному из `local/llm/tool`, иначе ставит `local`.
- **Покрыто тестами**: добавлены проверки на сохранение `ratelimited` и кламп `mode`.

Связанные изменения: `app/core/result.py`, `tests/test_result_contract.py`.

### 3) Telegram handler `/task` переписывал результат (менял intent/status/text и терял поля)
В `app/bot/handlers.py` команда `/task` брала `tool_result` и строила новый `OrchestratorResult` вручную (меняя `intent` на `command.task`, меняя `status`, формируя новый `text`). Это:
- нарушает правило Orchestrator v2 “handlers только отображают результат”,
- потенциально теряет `sources/actions/attachments/debug` оригинального результата задачи.

- **Исправление**: `/task` теперь **pass-through** отправляет `tool_result` в `send_result()` без перепаковки.

Связанные изменения: `app/bot/handlers.py`.

### 4) Отправка attachments могла падать при bytes-вложениях
В `app/bot/handlers.py` использовался `io.BytesIO(...)` без `import io`, а чтение полей attachments обращалось к `.get()` без проверки, что объект — dict.

- **Исправление**:
  - добавлен `import io`;
  - чтение полей сделано безопасным для `dict` и dataclass-like объектов.

Связанные изменения: `app/bot/handlers.py`.

## Валидация перед отправкой в UI
- **Основной путь**: `app/bot/handlers.py:send_result()` всегда вызывает `ensure_valid(...)` перед рендерингом текста/кнопок/вложений.
- **Важно**: в коде есть UI-вызовы `safe_send_text(...)` вне `send_result()` (например, для `ReplyKeyboardRemove`). Это **не** обход контракта `OrchestratorResult`, а отдельные UI-сообщения. Перевод их на `send_result()` потребовал бы переработки дедупликации по `request_id` (иначе основной ответ мог бы быть “пропущен” как дубль). Оставлено как осознанное исключение.

## Архитектура (Orchestrator v2)
- **Соответствие**: `core` формирует `OrchestratorResult` через `ok/refused/error/ratelimited`, инструменты (`tools_*`) возвращают `OrchestratorResult`.
- **UI слой**: Telegram handlers в основном вызывают `send_result()` и не трогают контракт результата.
- **Исправленная утечка**: `/task` больше не переписывает результаты задач.

## Безопасность и стабильность
- **Пустые сообщения**: `Orchestrator` и UI handlers защищены от пустого ввода (не падают).
- **Слишком длинный ввод**: `Orchestrator` ограничивает вход (`_MAX_INPUT_LENGTH`).
- **Пустые списки**: `ensure_valid()` нормализует `sources/actions/attachments` к спискам; UI рендерер устойчив к пустым значениям.
- **Статусы**:
  - проектный baseline использует `ok/refused/error` и дополнительно `ratelimited` в UI rate-limit ветках;
  - нормализация теперь не ломает `ratelimited`.

## Что осознанно не тронуто (чтобы не ломать архитектуру/контракты)
- **Единый стиль `intent`**: в проекте встречаются разные стили (`utility_summary` vs `command.search` vs `utility_calc`). Полная унификация может считаться изменением публичного поведения/телеметрии и требует отдельной миграции — оставлено без изменений.
- **Глубокая декомпозиция логики UI-команд** (например, `/calendar add|del` содержит часть прикладной логики в handler): перенос в `core/tools_*` возможен, но это уже “архитектурная операция” и должна делаться отдельным шагом с согласованными контрактами.

## Результат
- Исправлены реальные runtime-проблемы, которые могли проявиться в проде, при этом сохранён контракт `OrchestratorResult`.
- Добавлены минимальные unit-тесты на критичные ветки.

## Коммиты
- `6ee3ead` — Fix result validation and /search payload handling

