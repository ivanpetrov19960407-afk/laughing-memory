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
- Google Calendar OAuth (публичный HTTPS через nginx → локальный web-сервис).

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

### Google Calendar OAuth
Добавьте в `.env`:
- `GOOGLE_OAUTH_CLIENT_ID`
- `GOOGLE_OAUTH_CLIENT_SECRET`
- `PUBLIC_BASE_URL` (публичный базовый URL, например `https://vanekpetrov1997.fvds.ru`)
- `GOOGLE_OAUTH_REDIRECT_PATH` (по умолчанию `/oauth2/callback`)
- `GOOGLE_TOKENS_PATH` (по умолчанию `data/google_tokens.db`)
- `OAUTH_SERVER_PORT` (по умолчанию `8000`)
- `BOT_TOKEN` (нужен также для best-effort Telegram-уведомления при подключении)

## Подключение Google Calendar
1. В Google Cloud Console создайте OAuth Client (тип "Web application").
2. В "Authorized redirect URIs" укажите:
   - `${PUBLIC_BASE_URL}/oauth2/callback`
     (например `https://vanekpetrov1997.fvds.ru/oauth2/callback`).
3. Заполните переменные окружения из секции выше.
4. Запустите OAuth web-сервер (встроенный вместе с ботом или отдельный: `python oauth_server.py`).
5. Настройте nginx (пример: `deploy/nginx-oauth.conf`) для проксирования `/oauth2/` и `/health` → `http://127.0.0.1:8000`.
6. В Telegram откройте **Menu → Settings → 📅 Google Calendar → Подключить**, пройдите авторизацию.

> Токены хранятся в SQLite (по умолчанию `data/google_tokens.db`).
> При деплое задайте права `chmod 600` для защиты.

## Деплой OAuth web-сервиса
```bash
# Установить systemd unit
sudo cp deploy/telegram-bot-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot-web
sudo systemctl start telegram-bot-web

# Настроить nginx
sudo cp deploy/nginx-oauth.conf /etc/nginx/sites-available/telegram-bot-oauth
sudo ln -sf /etc/nginx/sites-available/telegram-bot-oauth /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### Эндпоинты OAuth web-сервиса
| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | Проверка работоспособности → `200 ok` |
| GET | `/oauth2/start?state=<user_id>` | Редирект на Google OAuth |
| GET | `/oauth2/callback?code=...&state=...` | Обмен code→token, сохранение refresh token |

## Тесты
```bash
pytest
```

## Поиск и строгий facts-mode
- `/search` без аргументов возвращает отказ с подсказкой: `Использование: /search <запрос>`.
- `/search <запрос>` выполняет веб-поиск, затем формирует ответ со сносками `[N]` и блоком `Источники:`.
- В режиме фактов (`/facts_on`) ответ допустим только при реальных `sources[]`; если источники не найдены — `refused` без выдумок.
- Анти-псевдоцитаты: ссылки вида `[1]` и блок `Источники:` запрещены, если `sources[]` пустой.
