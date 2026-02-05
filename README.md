# laughing-memory

Telegram-бот на архитектуре **Orchestrator v2**.

## Что поддерживается сейчас
- Единый контракт ответа `OrchestratorResult` для всех обработчиков и инструментов.
- Маршрутизация: команды, smalltalk, summary, обычные вопросы.
- Локальные задачи: `echo`, `upper`, `json_pretty`.
- Меню на inline-кнопках (`/menu`) и wizard-сценарии календаря/напоминаний.
- Напоминания (список, snooze, перенос, отключение).
- Режим фактов (`/facts_on`, `/facts_off`) и контекст диалога.
- Веб-поиск `/search <запрос>` с ответом по источникам и списком источников внизу.

## Команды
- `/start`
- `/help`
- `/menu`
- `/ping`
- `/tasks`
- `/task <name> <payload>`
- `/reminders [N]`
- `/search <запрос>`
- Обычный текст (маршрутизируется оркестратором).


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

## Поиск и строгий facts-mode
- `/search` без аргументов возвращает отказ с подсказкой: `Использование: /search <запрос>`.
- `/search <запрос>` выполняет веб-поиск, затем формирует ответ со сносками `[N]` и блоком `Источники:`.
- В режиме фактов (`/facts_on`) ответ допустим только при реальных `sources[]`; если источники не найдены — `refused` без выдумок.
- Анти-псевдоцитаты: ссылки вида `[1]` и блок `Источники:` запрещены, если `sources[]` пустой.
