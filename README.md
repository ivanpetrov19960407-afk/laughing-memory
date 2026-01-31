# laughing-memory

Telegram-бот с загрузкой конфигурации оркестратора и базовыми командами.

## Возможности
- `/start` — приветствие и краткий статус конфигурации.
- `/help` — список команд.
- `/config` — показать ключевые параметры конфигурации.
- `/task` — принять описание задачи одним сообщением.

## Быстрый старт

1. Создайте бота через [@BotFather](https://t.me/BotFather) и получите токен.
2. Установите зависимости:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Настройте переменные окружения (пример в `.env.example`):

```bash
export TELEGRAM_BOT_TOKEN="<ваш_токен>"
export ORCHESTRATOR_CONFIG_PATH="config/orchestrator.json"
```

4. Запустите бота:

```bash
python bot.py
```

## Конфигурация
Файл `config/orchestrator.json` содержит JSON-конфигурацию мультиагентного оркестратора.
Вы можете заменить его своим вариантом и указать путь через `ORCHESTRATOR_CONFIG_PATH`.
