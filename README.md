# laughing-memory

Telegram-бот-оркестратор с реальными задачами обработки текста/JSON, историей запусков и тестируемой бизнес-логикой.

## Что делает бот
- `/start` — приветствие и статус конфигурации.
- `/help` — список команд и примеры.
- `/ping` — проверка доступности (pong + версия/время).
- `/tasks` — список доступных задач.
- `/task <name> <payload>` — запуск зарегистрированной задачи.
- `/last` — последняя запись истории пользователя.

Поддерживаемые задачи:
- `echo` — вернуть payload как есть.
- `upper` — привести текст к UPPERCASE.
- `json_pretty` — форматировать JSON (ошибка при невалидном JSON).

## Установка

### Linux/macOS
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Windows (PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Запуск

1. Создайте бота через [@BotFather](https://t.me/BotFather) и получите токен.
2. Задайте переменные окружения (пример в `.env.example`).

Linux/macOS:
```bash
export BOT_TOKEN="<ваш_токен>"
export ORCHESTRATOR_CONFIG_PATH="config/orchestrator.json"
export BOT_DB_PATH="data/bot.db"
```

Windows (PowerShell):
```powershell
$env:BOT_TOKEN="<ваш_токен>"
$env:ORCHESTRATOR_CONFIG_PATH="config/orchestrator.json"
$env:BOT_DB_PATH="data/bot.db"
```

3. Запустите бота:
```bash
python bot.py
```

## Примеры команд
- `/task upper hello`
- `/task json_pretty {"a":1}`

## Хранилище
История запуска задач сохраняется в SQLite. Таблица: `task_executions` с полями timestamp, user_id, task_name, payload, result, status.

## Конфигурация задач через orchestrator.json
Файл `config/orchestrator.json` может содержать секцию `tasks` для включения/выключения задач:
```json
{
  "tasks": {
    "enabled": ["echo", "upper", "json_pretty"],
    "disabled": []
  }
}
```
Если секции нет — доступны все задачи по умолчанию.

## Тесты
```bash
pytest
```

## Примечания по версиям
- `python-telegram-bot` зафиксирован в `requirements.txt` как `==21.6`.
- Дополнительные зависимости имеют диапазоны (`pytest>=8,<9`, `python-dotenv>=1,<2`) для совместимости.
