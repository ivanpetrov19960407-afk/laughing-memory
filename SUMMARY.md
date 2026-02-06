# Итоговый отчёт по реализации Google Calendar OAuth

## ✅ Выполненные задачи

### 1. Изменение эндпоинтов OAuth
- ✅ `/oauth/google/start?user_id=<id>` → `/oauth2/start?state=<id>`
- ✅ `/oauth/google/callback` → `/oauth2/callback`
- ✅ Добавлен `/health` → возвращает `200 ok`
- ✅ Параметр `user_id` заменён на `state` для соответствия OAuth стандарту

### 2. Хранилище токенов
- ✅ Миграция с JSON на SQLite
- ✅ Создана таблица `google_tokens` с полями:
  - `user_id` TEXT PRIMARY KEY
  - `access_token` TEXT NOT NULL  
  - `refresh_token` TEXT NOT NULL
  - `expires_at` REAL
  - `token_type` TEXT
  - `scope` TEXT
  - `created_at` TEXT NOT NULL
  - `updated_at` TEXT NOT NULL
- ✅ Автоматическая инициализация БД при старте
- ✅ Путь к БД конфигурируется через `GOOGLE_TOKENS_DB_PATH`

### 3. OAuth сервер
- ✅ Слушает только на `127.0.0.1` (не наружу)
- ✅ Порт по умолчанию `8000` (конфигурируется через `OAUTH_SERVER_PORT`)
- ✅ Запускается автоматически вместе с Telegram ботом
- ✅ Никакие токены/секреты не логируются

### 4. Интеграция с Telegram
- ✅ Команда "📅 Google Calendar → Подключить" в меню Settings
- ✅ Генерация правильного URL: `https://vanekpetrov1997.fvds.ru/oauth2/start?state=<telegram_user_id>`
- ✅ Уведомление в Telegram "✅ Календарь подключён" (best-effort)
- ✅ Callback возвращает HTML 200 даже если уведомление в Telegram не удалось

### 5. Systemd unit файлы
- ✅ Создан `systemd/telegram-bot.service` для бота и веб-сервиса (в одном процессе)
- ✅ Не ломает существующую логику (всё в одном сервисе)
- ✅ Добавлен `systemd/README.md` с инструкциями по установке
- ✅ Настроен автозапуск и restart on failure

### 6. Nginx конфигурация
- ✅ Создан шаблон `nginx/telegram-bot-oauth.conf`
- ✅ Проксирование `/oauth2/` на `http://127.0.0.1:8000`
- ✅ Проксирование `/health` на `http://127.0.0.1:8000`
- ✅ Добавлен `nginx/README.md` с инструкциями по установке и SSL

### 7. Тесты
- ✅ Все 117 тестов проходят (`pytest -q`)
- ✅ Добавлены тесты для:
  - Генерации authorization URL с state, redirect_uri, scopes
  - SQLite хранилища (CRUD операции)
  - OAuth callback с моком обмена code→token
- ✅ Обновлены существующие тесты для новой структуры

### 8. Документация
- ✅ Обновлён `README.md` с новыми переменными окружения
- ✅ Обновлён `.env.example` с актуальными значениями
- ✅ Создан `DEPLOYMENT.md` с полной инструкцией по деплою
- ✅ Инструкции по миграции с JSON на SQLite

## 📝 Что было сломано и исправлено

### Сломанные тесты (4 шт.)
1. `test_calendar_command_add_does_not_create_reminder` - использовал `GOOGLE_TOKENS_PATH`
2. `test_calendar_tool_refreshes_token_when_expired` - использовал `GOOGLE_TOKENS_PATH`
3. `test_calendar_tool_calls_google_api` - использовал `GOOGLE_TOKENS_PATH`
4. `test_wizard_add_event_flow` - использовал `GOOGLE_TOKENS_PATH`

### Исправления
- Заменил все `GOOGLE_TOKENS_PATH` → `GOOGLE_TOKENS_DB_PATH`
- Изменил расширение файла `.json` → `.db`
- Обновил redirect path `/oauth/google/callback` → `/oauth2/callback`

## 🔧 Переменные окружения для продакшна

### Обязательные
```bash
BOT_TOKEN="your-telegram-bot-token"
ALLOWED_USER_IDS="123,456"
GOOGLE_OAUTH_CLIENT_ID="your-google-client-id"
GOOGLE_OAUTH_CLIENT_SECRET="your-google-client-secret"
PUBLIC_BASE_URL="https://vanekpetrov1997.fvds.ru"
```

### Опциональные (со значениями по умолчанию)
```bash
GOOGLE_OAUTH_REDIRECT_PATH="/oauth2/callback"
GOOGLE_TOKENS_DB_PATH="data/google_tokens.db"
OAUTH_SERVER_HOST="127.0.0.1"
OAUTH_SERVER_PORT="8000"
```

## 🚀 Порядок деплоя на сервере

1. **Pull изменений:**
   ```bash
   cd /home/ubuntu/laughing-memory
   git fetch origin cursor/google-calendar-oauth-e6f0
   git checkout cursor/google-calendar-oauth-e6f0
   ```

2. **Обновить .env:**
   ```bash
   nano .env
   # Проверить/добавить все переменные из DEPLOYMENT.md
   ```

3. **Установить systemd service:**
   ```bash
   sudo cp systemd/telegram-bot.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl restart telegram-bot.service
   sudo systemctl status telegram-bot.service
   ```

4. **Настроить nginx:**
   ```bash
   sudo cp nginx/telegram-bot-oauth.conf /etc/nginx/sites-available/
   # Отредактировать server_name если нужно
   sudo ln -s /etc/nginx/sites-available/telegram-bot-oauth.conf /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl reload nginx
   ```

5. **Проверить работу:**
   ```bash
   curl https://vanekpetrov1997.fvds.ru/health
   # Должно вернуть: ok
   ```

6. **Тест OAuth в Telegram:**
   - Открыть бот
   - Menu → Settings → Подключить Google Calendar
   - Пройти авторизацию

## 📊 Статистика

- **Изменено файлов:** 17
- **Добавлено строк:** 747
- **Удалено строк:** 111
- **Тестов:** 117 (все проходят ✅)
- **Новых тестов:** 3
- **Коммитов:** 1
- **Ветка:** `cursor/google-calendar-oauth-e6f0`

## ⚠️ Breaking Changes

1. **Переменная окружения:**
   - `GOOGLE_TOKENS_PATH` → `GOOGLE_TOKENS_DB_PATH`

2. **Формат хранения:**
   - `data/google_tokens.json` → `data/google_tokens.db`
   - Требуется миграция данных (скрипт в DEPLOYMENT.md)

3. **OAuth endpoints:**
   - `/oauth/google/*` → `/oauth2/*`
   - Нужно обновить redirect URI в Google Console

4. **OAuth redirect path:**
   - По умолчанию: `/oauth/google/callback` → `/oauth2/callback`

## 📚 Дополнительные документы

- `DEPLOYMENT.md` - полная инструкция по деплою
- `systemd/README.md` - настройка systemd сервиса
- `nginx/README.md` - настройка nginx reverse proxy
- `.env.example` - пример конфигурации

## ✨ Заключение

Реализация полностью соответствует требованиям:
- ✅ Публичный HTTPS + Google Calendar OAuth
- ✅ Все тесты зелёные (117/117)
- ✅ Готово к деплою без ручной правки кода на сервере
- ✅ Автоматическая инициализация БД при старте
- ✅ Безопасное хранение токенов в SQLite
- ✅ OAuth сервер слушает только на 127.0.0.1
- ✅ Конфигурация через переменные окружения

Все изменения закоммичены и запушены в ветку `cursor/google-calendar-oauth-e6f0`.
