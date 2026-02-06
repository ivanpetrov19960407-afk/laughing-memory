# Deployment Guide - Google Calendar OAuth

Этот документ описывает изменения в реализации Google Calendar OAuth и требования для production deploy.

## Что было изменено

### 1. Эндпоинты OAuth
- **Старые:** `/oauth/google/start?user_id=<id>`, `/oauth/google/callback`
- **Новые:** `/oauth2/start?state=<id>`, `/oauth2/callback`
- **Добавлен:** `/health` → возвращает `200 ok`

### 2. Хранилище токенов
- **Было:** JSON файл (`data/google_tokens.json`)
- **Стало:** SQLite БД (`data/google_tokens.db`)
- **Таблица:** `google_tokens` с полями:
  - `user_id` TEXT PRIMARY KEY
  - `access_token` TEXT NOT NULL
  - `refresh_token` TEXT NOT NULL
  - `expires_at` REAL
  - `token_type` TEXT
  - `scope` TEXT
  - `created_at` TEXT NOT NULL
  - `updated_at` TEXT NOT NULL

### 3. OAuth Server
- **Host:** `127.0.0.1` (только локальный, не наружу)
- **Port:** `8000` (по умолчанию, конфигурируется через ENV)
- Запускается автоматически вместе с Telegram ботом

### 4. Тесты
- ✅ Все 117 тестов проходят
- Добавлены тесты для:
  - Генерации authorization URL с правильным state
  - SQLite хранилища (CRUD операции)
  - Новых эндпоинтов OAuth

## Требования для Production

### Обязательные переменные окружения

```bash
# Telegram Bot
BOT_TOKEN="your-telegram-bot-token"
ALLOWED_USER_IDS="123,456"

# Google OAuth
GOOGLE_OAUTH_CLIENT_ID="your-google-client-id"
GOOGLE_OAUTH_CLIENT_SECRET="your-google-client-secret"
PUBLIC_BASE_URL="https://vanekpetrov1997.fvds.ru"
GOOGLE_OAUTH_REDIRECT_PATH="/oauth2/callback"

# OAuth Server (опционально, значения по умолчанию)
OAUTH_SERVER_HOST="127.0.0.1"
OAUTH_SERVER_PORT="8000"

# Database path (опционально, значение по умолчанию)
GOOGLE_TOKENS_DB_PATH="data/google_tokens.db"
```

### Google Cloud Console Setup

1. Перейдите в [Google Cloud Console](https://console.cloud.google.com/)
2. Создайте или выберите проект
3. Включите Google Calendar API
4. Создайте OAuth 2.0 Client ID (тип: Web application)
5. В "Authorized redirect URIs" добавьте:
   ```
   https://vanekpetrov1997.fvds.ru/oauth2/callback
   ```
6. Скопируйте Client ID и Client Secret в `.env`

### Nginx Configuration

1. Скопируйте конфиг:
   ```bash
   sudo cp nginx/telegram-bot-oauth.conf /etc/nginx/sites-available/
   ```

2. Отредактируйте `server_name`:
   ```nginx
   server_name vanekpetrov1997.fvds.ru;
   ```

3. Настройте SSL (рекомендуется):
   ```bash
   sudo certbot --nginx -d vanekpetrov1997.fvds.ru
   ```

4. Включите сайт:
   ```bash
   sudo ln -s /etc/nginx/sites-available/telegram-bot-oauth.conf /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl reload nginx
   ```

### Systemd Service

1. Скопируйте service файл:
   ```bash
   sudo cp systemd/telegram-bot.service /etc/systemd/system/
   ```

2. Отредактируйте пути в service файле (если нужно):
   ```ini
   User=ubuntu
   WorkingDirectory=/home/ubuntu/laughing-memory
   EnvironmentFile=/home/ubuntu/laughing-memory/.env
   ExecStart=/usr/bin/python3 /home/ubuntu/laughing-memory/bot.py
   ```

3. Запустите сервис:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable telegram-bot.service
   sudo systemctl start telegram-bot.service
   ```

4. Проверьте статус:
   ```bash
   sudo systemctl status telegram-bot.service
   sudo journalctl -u telegram-bot.service -f
   ```

### Проверка работоспособности

1. **Health check:**
   ```bash
   curl https://vanekpetrov1997.fvds.ru/health
   # Должен вернуть: ok
   ```

2. **OAuth flow:**
   - Откройте Telegram бот
   - Перейдите в Menu → Settings → Подключить Google Calendar
   - Получите ссылку типа: `https://vanekpetrov1997.fvds.ru/oauth2/start?state=<telegram_user_id>`
   - Пройдите авторизацию в Google
   - После успешной авторизации должно появиться сообщение: "✅ Календарь подключён. Можно вернуться в Telegram."

3. **Проверка БД:**
   ```bash
   sqlite3 data/google_tokens.db "SELECT user_id, created_at, updated_at FROM google_tokens;"
   ```

### Безопасность

1. **Права на БД:**
   ```bash
   chmod 600 data/google_tokens.db
   chown ubuntu:ubuntu data/google_tokens.db
   ```

2. **Права на .env:**
   ```bash
   chmod 600 .env
   chown ubuntu:ubuntu .env
   ```

3. **Firewall:**
   - Убедитесь, что порт 8000 НЕ открыт извне
   - Должны быть открыты только 80 и 443 для nginx:
     ```bash
     sudo ufw status
     sudo ufw allow 80/tcp
     sudo ufw allow 443/tcp
     ```

## Миграция с старой версии

Если у вас уже есть `data/google_tokens.json`, нужно мигрировать данные:

```python
#!/usr/bin/env python3
import json
import sqlite3
import time
from pathlib import Path

# Читаем старый JSON
old_path = Path("data/google_tokens.json")
if old_path.exists():
    with open(old_path) as f:
        data = json.load(f)
    
    # Создаем новую БД
    db_path = Path("data/google_tokens.db")
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS google_tokens (
            user_id TEXT PRIMARY KEY,
            access_token TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            expires_at REAL,
            token_type TEXT,
            scope TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    
    # Мигрируем данные
    now_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    for user_id, tokens in data.get("tokens", {}).items():
        conn.execute(
            """
            INSERT OR REPLACE INTO google_tokens 
            (user_id, access_token, refresh_token, expires_at, token_type, scope, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                tokens.get("access_token"),
                tokens.get("refresh_token"),
                tokens.get("expires_at"),
                tokens.get("token_type"),
                tokens.get("scope"),
                now_iso,
                tokens.get("updated_at", now_iso),
            )
        )
    
    conn.commit()
    conn.close()
    print(f"Migrated {len(data.get('tokens', {}))} tokens to SQLite")
    
    # Бэкап старого файла
    old_path.rename("data/google_tokens.json.backup")
```

## Troubleshooting

### OAuth сервер не запускается

**Ошибка:** `Failed to start Google OAuth server on 127.0.0.1:8000`

**Решение:**
```bash
# Проверьте, не занят ли порт
sudo lsof -i :8000
# Измените порт в .env
echo "OAUTH_SERVER_PORT=8001" >> .env
```

### Nginx возвращает 502 Bad Gateway

**Причина:** OAuth сервер не запущен или слушает не на том порту

**Решение:**
```bash
# Проверьте логи бота
sudo journalctl -u telegram-bot.service -n 50

# Проверьте, что OAuth сервер слушает на правильном порту
sudo netstat -tlnp | grep 8000
```

### Google возвращает redirect_uri_mismatch

**Причина:** Несовпадение redirect URI в Google Console и в коде

**Решение:**
1. Проверьте переменную `PUBLIC_BASE_URL` в `.env`
2. Убедитесь, что в Google Console добавлен точный URI: `https://vanekpetrov1997.fvds.ru/oauth2/callback`
3. Обратите внимание на https/http и trailing slash

## Тесты

Все тесты проходят успешно:
```bash
pytest -q
# 117 passed in 0.43s
```

Запуск только OAuth тестов:
```bash
pytest -xvs tests/test_google_oauth.py
# 3 passed
```
