# laughing-memory

Telegram-бот-оркестратор с реальными задачами обработки текста/JSON, историей запусков и тестируемой бизнес-логикой.

## Что делает бот
- Оркестратор v2: классифицирует запросы (smalltalk/utility/question/command), выбирает локальный обработчик или LLM и возвращает структурированный результат для логов.
- `/start` — приветствие и статус конфигурации.
- `/help` — список команд и примеры.
- `/ping` — проверка доступности (pong + версия/время).
- `/tasks` — список доступных задач.
- `/task <name> <payload>` — запуск зарегистрированной задачи.
- `/last` — последняя запись истории пользователя.
- `/ask <текст>` — тестовый ответ с контекстом последних сообщений.
- `/search <текст>` — тестовый ответ с контекстом последних сообщений.
- `/summary <текст>` — краткое резюме текста (маршрутизация через LLM).
- `/facts_on` и `/facts_off` — режим фактов (LLM отвечает только с источниками).
- `/selfcheck` — проверка конфигурации на сервере.

Обычный текст без команды маршрутизируется так:
- `task <name> <payload>` — запускает локальную задачу.
- `search <текст>` — тестовый ответ с контекстом.
- `summary: <текст>` — краткое резюме текста.
- любой другой текст — ответ с контекстом последних сообщений.

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
2. Задайте переменные окружения (пример в `.env.example`, можно скопировать в `.env`).

Linux/macOS:
```bash
export BOT_TOKEN="<ваш_токен>"
export ORCHESTRATOR_CONFIG_PATH="config/orchestrator.json"
export BOT_DB_PATH="data/bot.db"
export ALLOWED_USER_IDS="123456789,987654321"
export RATE_LIMIT_PER_MINUTE="6"
export RATE_LIMIT_PER_DAY="80"
export HISTORY_SIZE="10"
export TELEGRAM_MESSAGE_LIMIT="4000"
export FACTS_ONLY_DEFAULT="false"
```

Windows (PowerShell):
```powershell
$env:BOT_TOKEN="<ваш_токен>"
$env:ORCHESTRATOR_CONFIG_PATH="config/orchestrator.json"
$env:BOT_DB_PATH="data/bot.db"
$env:ALLOWED_USER_IDS="123456789,987654321"
$env:RATE_LIMIT_PER_MINUTE="6"
$env:RATE_LIMIT_PER_DAY="80"
$env:HISTORY_SIZE="10"
$env:TELEGRAM_MESSAGE_LIMIT="4000"
$env:FACTS_ONLY_DEFAULT="false"
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
- `/summary Кратко опиши документ`
- `search Путин биография`
- `summary: большой текст для сжатия`

## Команды бота
- `/start` — приветствие и краткая инструкция.
- `/help` — помощь.
- `/ping` — проверка доступности.
- `/tasks` — список задач.
- `/task <name> <payload>` — выполнить локальную задачу.
- `/last` — последняя запись истории.
- `/ask <текст>` — тестовый ответ с контекстом.
- `/search <текст>` — тестовый ответ с контекстом.
- `/summary <текст>` — краткое резюме текста.
- `/facts_on` — включить режим фактов.
- `/facts_off` — выключить режим фактов.
- `/selfcheck` — проверка конфигурации.
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
Whitelist пользователей обязателен: `ALLOWED_USER_IDS` должен быть задан. Если список пустой — доступ закрыт всем, а бот пишет ошибку в лог.

Ограничения запросов (на пользователя): `RATE_LIMIT_PER_MINUTE` и `RATE_LIMIT_PER_DAY`.

Контекст диалога хранит последние `HISTORY_SIZE` сообщений, а отправка длинных ответов дробится по `TELEGRAM_MESSAGE_LIMIT`. Всё хранится в памяти процесса.

## Проверка после обновления
1. Убедитесь, что задали `ALLOWED_USER_IDS` и остальные переменные.
2. Напишите в боте `/selfcheck` — команда покажет текущие лимиты и параметры контекста.
3. Отправьте обычное сообщение и убедитесь, что бот отвечает "Ок. Последние сообщения: ...".

## Тесты
```bash
pytest
```

## Примечания по версиям
- `python-telegram-bot` зафиксирован в `requirements.txt` как `==21.6`.
- Дополнительные зависимости имеют диапазоны (`pytest>=8,<9`, `python-dotenv>=1,<2`) для совместимости.
