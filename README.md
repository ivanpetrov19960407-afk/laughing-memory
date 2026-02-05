# laughing-memory

Telegram-бот на архитектуре **Orchestrator v2**.

## Что поддерживается сейчас
- Единый контракт ответа `OrchestratorResult` для всех обработчиков и инструментов.
- Маршрутизация: команды, smalltalk, summary, обычные вопросы.
- Локальные задачи: `echo`, `upper`, `json_pretty`.
- Меню на inline-кнопках (`/menu`) и wizard-сценарии календаря/напоминаний.
- Напоминания (список, snooze, перенос, отключение).
- Режим фактов (`/facts_on`, `/facts_off`) и контекст диалога.

## Команды
- `/start`
- `/help`
- `/menu`
- `/ping`
- `/tasks`
- `/task <name> <payload>`
- `/reminders [N]`
- Обычный текст (маршрутизируется оркестратором).

> Неподдерживаемые на текущем этапе функции (веб-поиск, OCR/FileReader, legacy WizardRuntime) удалены/отключены.

## Result Contract
Поля `OrchestratorResult`:
- `text`, `status`, `mode`, `intent`, `request_id`
- `sources`, `attachments`, `actions`, `debug`

Правила:
- Любой handler/tool возвращает `OrchestratorResult`.
- Перед отправкой в UI применяется `ensure_valid`.

## Запуск
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

## Переменные окружения
См. `.env.example` — в файле оставлены только актуальные переменные.

## Тесты
```bash
pytest
```
