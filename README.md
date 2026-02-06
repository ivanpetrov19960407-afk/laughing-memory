# laughing-memory

Telegram-бот на архитектуре **Orchestrator v2** — интеллектуальный секретарь с LLM, поиском, календарём, напоминаниями и inline-меню.

## Архитектура

- **Тонкий фронт** (`app/bot/handlers.py`) — принимает Update, отправляет `OrchestratorResult` в Telegram.
- **Мозг-оркестратор** (`app/core/orchestrator.py`) — маршрутизация, decisions, LLM-вызовы, search-pipeline.
- **Tool-слой** — калькулятор, календарь, напоминания, LLM-инструменты (check/rewrite/explain), web-поиск.
- **ActionStore** (`app/bot/actions.py`) — хранение inline-кнопок с TTL.
- **Wizard Framework** (`app/bot/wizard.py`, `app/storage/wizard_store.py`) — state-machine для сценариев.
- **Единый Result Contract** (`app/core/result.py`) — `OrchestratorResult` для всех ответов.

## Что поддерживается

- Единый контракт ответа `OrchestratorResult` для всех обработчиков и инструментов.
- Маршрутизация: команды, smalltalk, summary, обычные вопросы.
- Локальные задачи: `echo`, `upper`, `json_pretty`.
- Inline-меню (`/menu`) с разделами: Чат, Поиск, Картинки, Калькулятор, Календарь, Напоминания, Настройки.
- Wizard-сценарии: добавление события в календарь, создание напоминания, перенос напоминания.
- Напоминания: список, snooze, перенос, отключение, рекурренция.
- Режим фактов (`/facts_on`, `/facts_off`) — ответ только с источниками.
- Контекст диалога (`/context_on`, `/context_off`, `/context_clear`).
- Веб-поиск (`/search <запрос>`) с ответом по источникам и списком источников внизу.
- Генерация изображений (`/image <описание>`).
- Проверка/переписывание текста (`/check`, `/rewrite`, `/explain`).
- Анти-псевдоцитаты: ссылки `[1]` и блок `Источники:` запрещены, если `sources[]` пустой.
- Whitelist-доступ, rate-limiting, admin-команды.
- Fallback на неизвестные команды с подсказкой `/menu`.

## Команды

| Команда | Описание |
|---|---|
| `/start` | Приветствие |
| `/help` | Помощь |
| `/menu` | Открыть inline-меню |
| `/ping` | Проверка связи |
| `/tasks` | Список задач |
| `/task <name> <payload>` | Выполнить задачу |
| `/ask <текст>` | Вопрос к LLM |
| `/search <запрос>` | Веб-поиск с источниками |
| `/summary <текст>` | Краткое резюме |
| `/image <описание>` | Генерация изображения |
| `/calc <выражение>` | Калькулятор |
| `/calendar add/list/today/week/del` | Управление событиями |
| `/reminders [N]` | Ближайшие напоминания |
| `/check <текст>` | Проверка текста |
| `/rewrite <mode> <текст>` | Переписывание (simple/hard/short) |
| `/explain <текст>` | Объяснение текста |
| `/facts_on` / `/facts_off` | Режим фактов |
| `/context_on` / `/context_off` | Контекст диалога |
| `/context_clear` | Очистка контекста |
| `/context_status` | Статус контекста |
| `/cancel` | Отмена активного сценария |
| `/selfcheck` | Диагностика |
| `/health` | Статус бота |
| `/allow <user_id>` | Добавить в whitelist (admin) |
| `/deny <user_id>` | Удалить из whitelist (admin) |
| `/allowlist` | Показать whitelist (admin) |

Обычный текст маршрутизируется оркестратором (smalltalk / LLM / task).

## Result Contract

Поля `OrchestratorResult`:
- `text` — текст ответа (всегда non-empty)
- `status` — `ok` / `refused` / `error` / `ratelimited`
- `mode` — `local` / `llm` / `tool`
- `intent` — идентификатор намерения
- `request_id` — ID запроса
- `sources` — список `Source(title, url, snippet)`
- `attachments` — список вложений
- `actions` — список `Action` для inline-кнопок
- `debug` — отладочная информация (не показывается пользователю)

Правила:
- Любой handler/tool возвращает `OrchestratorResult`.
- Перед отправкой в UI применяется `ensure_valid`.
- `ensure_valid` гарантирует non-empty `text`, нормализует status/mode/intent.
- Если `sources` пусты, citation markers (`[1]`, `(1)`) удаляются из текста.
- `debug` никогда не отправляется пользователю.
- `actions` отрисовываются как inline-кнопки в Telegram.

## Режимы

- **Режим фактов**: бот отвечает только при наличии реальных `sources[]`. Без источников — `refused`.
- **Контекст диалога**: бот учитывает предыдущие сообщения при генерации ответа.
- **Strict pseudo-source guard**: ссылки/цитаты без реальных sources блокируются.

## Поиск и строгий facts-mode

- `/search` без аргументов → отказ с подсказкой.
- `/search <запрос>` → веб-поиск → ответ LLM по snippets → текст + `Источники:` внизу.
- В режиме фактов ответ допустим только при реальных `sources[]`.
- Если источники не найдены → `refused`, бот не придумывает ответ.

## Запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Заполнить .env (BOT_TOKEN, API ключи и т.д.)
python bot.py
```

## Переменные окружения

См. `.env.example`. Ключевые:

| Переменная | Описание | По умолчанию |
|---|---|---|
| `BOT_TOKEN` | Токен Telegram бота | (обязательно) |
| `BOT_TIMEZONE` | Таймзона для календаря/напоминаний | `Europe/Vilnius` |
| `ALLOWED_USER_IDS` | Разрешённые user_id (через запятую) | — |
| `ADMIN_USER_IDS` | Admin user_id | — |
| `OPENAI_API_KEY` | Ключ OpenAI | — |
| `PERPLEXITY_API_KEY` | Ключ Perplexity | — |
| `ENABLE_MENU` | Включить inline-меню | `true` |
| `ENABLE_WIZARDS` | Включить сценарии | `true` |
| `STRICT_NO_PSEUDO_SOURCES` | Строгий режим анти-псевдоцитат | `true` |
| `FEATURE_WEB_SEARCH` | Включить веб-поиск | `true` |
| `REMINDERS_ENABLED` | Включить напоминания | `true` |
| `FACTS_ONLY_DEFAULT` | Режим фактов по умолчанию | `false` |

## Деплой (systemd)

```ini
[Unit]
Description=Secretary Bot
After=network.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/opt/bot
ExecStart=/opt/bot/.venv/bin/python bot.py
Restart=always
RestartSec=5
EnvironmentFile=/opt/bot/.env

[Install]
WantedBy=multi-user.target
```

## Тесты

```bash
pytest
```

Все тесты проверяют:
- Контракт `OrchestratorResult` (обязательные поля, валидация)
- `ensure_valid` (нормализация, fallback, citation stripping)
- Actions/debug separation
- Wizard flows (календарь, напоминания, cancel, timeout)
- Menu sections
- Strict facts mode
- Search pipeline
- Timezone awareness
- Unknown command fallback
