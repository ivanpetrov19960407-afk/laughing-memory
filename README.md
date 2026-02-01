# laughing-memory

Telegram-бот-оркестратор с реальными задачами обработки текста/JSON, историей запусков и тестируемой бизнес-логикой.

## Что делает бот
- `/start` — приветствие и статус конфигурации.
- `/help` — список команд и примеры.
- `/ping` — проверка доступности (pong + версия/время).
- `/tasks` — список доступных задач.
- `/task <name> <payload>` — запуск зарегистрированной задачи.
- `/last` — последняя запись истории пользователя.
- `/ask <текст>` — задать вопрос LLM.
- `/search <текст>` — режим поиска через LLM (с источниками при наличии).

Обычный текст без команды маршрутизируется так:
- `task <name> <payload>` — запускает локальную задачу.
- `search <текст>` — поисковый режим LLM.
- любой другой текст — обычный LLM-запрос.

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
2. Получите ключ Perplexity:
   - Откройте [Perplexity API keys](https://docs.perplexity.ai/docs/admin/api-key-management).
   - Создайте ключ и сохраните его в `PERPLEXITY_API_KEY`.
3. Задайте переменные окружения (пример в `.env.example`, можно скопировать в `.env`).

Linux/macOS:
```bash
export BOT_TOKEN="<ваш_токен>"
export ORCHESTRATOR_CONFIG_PATH="config/orchestrator.json"
export BOT_DB_PATH="data/bot.db"
export PERPLEXITY_API_KEY="<ваш_ключ>"
export PERPLEXITY_MODEL="sonar"
export PERPLEXITY_TIMEOUT_SECONDS="15"
export ALLOWED_USER_IDS="123456789,987654321"
export LLM_PER_MINUTE="10"
export LLM_PER_DAY="200"
export LLM_HISTORY_TURNS="5"
```

Windows (PowerShell):
```powershell
$env:BOT_TOKEN="<ваш_токен>"
$env:ORCHESTRATOR_CONFIG_PATH="config/orchestrator.json"
$env:BOT_DB_PATH="data/bot.db"
$env:PERPLEXITY_API_KEY="<ваш_ключ>"
$env:PERPLEXITY_MODEL="sonar"
$env:PERPLEXITY_TIMEOUT_SECONDS="15"
$env:ALLOWED_USER_IDS="123456789,987654321"
$env:LLM_PER_MINUTE="10"
$env:LLM_PER_DAY="200"
$env:LLM_HISTORY_TURNS="5"
```

3. Запустите бота:
```bash
python bot.py
```

### Запуск в VSCode
1. Установите зависимости в виртуальном окружении (`python -m venv .venv`).
2. Активируйте окружение и установите зависимости (`pip install -r requirements.txt`).
3. Создайте `.env` на основе `.env.example` и заполните ключи.
4. Запустите `bot.py` через Run/Debug.

### Перезапуск на VPS (systemd)
```bash
sudo systemctl restart <имя_сервиса>
```

Проверка логов:
```bash
sudo journalctl -u <имя_сервиса> -f
```

## Примеры команд
- `/task upper hello`
- `/task json_pretty {"a":1}`
- `/ask Привет`
- `/search Новости`
- `search Путин биография`

## Команды бота
- `/start` — приветствие и краткая инструкция.
- `/help` — помощь.
- `/ping` — проверка доступности.
- `/tasks` — список задач.
- `/task <name> <payload>` — выполнить локальную задачу.
- `/last` — последняя запись истории.
- `/ask <текст>` — вопрос в Perplexity.
- `/search <текст>` — поиск с источниками (если есть).
- `echo <текст>` — вернуть текст.
- `upper <текст>` — текст в верхнем регистре.
- `json_pretty <json>` — красивое форматирование JSON.

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

## Доступ и лимиты
Доступ к боту можно ограничить whitelist пользователей через `ALLOWED_USER_IDS` или `config/orchestrator.json` (`access.allowed_user_ids`). Если список пустой или не задан — доступ открыт всем. При наличии whitelist другие пользователи получают ответ "Доступ запрещён.".

Лимиты LLM-запросов задаются через `LLM_PER_MINUTE` / `LLM_PER_DAY` или `rate_limits.llm` в `config/orchestrator.json`. Лимиты учитываются на пользователя и хранятся в памяти процесса.

Контекст LLM-запроса включает последние N успешных ходов (`ask`/`search`) — значение задаётся `LLM_HISTORY_TURNS` или `llm.history_turns`.

## Тесты
```bash
pytest
```

## Примечания по версиям
- `python-telegram-bot` зафиксирован в `requirements.txt` как `==21.6`.
- Дополнительные зависимости имеют диапазоны (`pytest>=8,<9`, `python-dotenv>=1,<2`) для совместимости.
