# Stage 4 — UX polish & human scenarios

## Goal
Make the bot usable for non-technical users via a clear main menu, guided flows (wizard), predictable fallbacks, and action-first UX.

## Scope
In scope:
- Main menu and section entry points
- Human-readable texts (no internal jargon)
- “No dead ends” navigation
- Quick actions (buttons) for next steps
- Fallbacks: unknown commands, refused, error, wizard timeout/cancel/back

Out of scope:
- New tools/integrations
- Search improvements
- Memory features
- NLP parsing upgrades

## Main Menu (Home)
Home screen must always be reachable via action `🏠 Menu`.

### Home text (v1)
"Выбери раздел:"

### Home actions
- 💬 Чат
- 🔍 Поиск
- 🖼 Картинки
- 🧮 Калькулятор
- 📅 Календарь
- ⏰ Напоминания
- ⚙️ Настройки

## Section entry points
Each section entry point must:
- Explain in 1 sentence what the section does
- Offer top 3 actions + `🏠 Menu`

### Chat
Text: "Пиши сообщением — отвечу. Можно вернуться в меню."
Actions: `🏠 Menu`, `🧹 Очистить контекст`, `📌 Режим фактов`

### Search
Text: "Ищу в интернете и даю ссылки на источники."
Actions: `🔎 Новый поиск`, `🏠 Menu`, `📌 Режим фактов`

### Images
Text: "Опиши картинку — сгенерирую."
Actions: `🖼 Сгенерировать`, `🏠 Menu`, `ℹ️ Примеры`

### Calculator
Text: "Введи выражение (например: 12*(5+3))."
Actions: `🧮 Посчитать`, `🏠 Menu`, `ℹ️ Примеры`

### Calendar
Text: "Календарь: добавить/посмотреть/удалить события."
Actions: `➕ Добавить`, `📋 Список`, `🏠 Menu`

### Reminders
Text: "Напоминания: создать/список/удалить."
Actions: `➕ Создать`, `📋 Список`, `🏠 Menu`

### Settings
Text: "Настройки режимов и поведения."
Actions: `📌 Факты on/off`, `🧠 Контекст on/off`, `🏠 Menu`

## Fallbacks (must-have)

### Unknown command
When user sends an unknown `/command`:
- status: refused
- text: "Неизвестная команда."
- actions: `🏠 Menu`

### Unknown text in wizard
If wizard expects input but got something else:
- status: ok
- text: "Я жду: <что именно>. Можно отменить."
- actions: `↩ Назад` (if available), `✖ Отмена`, `🏠 Menu`

### Refused (general)
If tool returns refused:
- Show short reason (1–2 lines)
- Offer alternatives (at least Menu + one relevant action)

### Error (unexpected)
If exception or tool error:
- status: error
- text: "Ошибка. Попробовать ещё раз?"
- actions: `🔁 Повторить`, `🏠 Menu`

### Wizard timeout
If wizard step expires:
- reset state
- text: "Время ожидания истекло. Открыл меню."
- actions: `🏠 Menu`

## Acceptance Criteria (DoD)
- Every handler reply includes at least one navigation action (`🏠 Menu`) except trivial acknowledgements.
- Unknown commands never lead to silence.
- Every wizard has: confirm/cancel, back, timeout.
- Manual walkthrough from Home covers all sections without dead ends.

