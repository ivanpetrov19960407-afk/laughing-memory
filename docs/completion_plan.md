# План доработки бота

**Дата:** 2026-02-05  
**Ветка:** `cursor/orchestrator-8b16`

---

## 🔍 Найденные проблемы

### 🔴 КРИТИЧНО: Дублирование регистрации handlers

**Файл:** `app/main.py`  
**Проблема:** Handlers регистрируются дважды:

1. **Строки 27-41:** Функция `_register_handlers()`
   - Регистрирует все команды включая `facts_on`, `facts_off`

2. **Строки 177-189:** Повторная регистрация в `main()`
   - НЕТ `facts_on`, `facts_off` (потеряны)
   - Дублирует остальные handlers

**Последствия:**
- Конфликты при обработке команд
- Undefined behavior
- Потенциальные race conditions

**Решение:**
```python
# УДАЛИТЬ строки 178-189 (дублирующую регистрацию)
# ОСТАВИТЬ только вызов _register_handlers(application) на строке 177
```

---

### ⚠️ Недостающие команды

Есть handlers, но они не зарегистрированы в `_register_handlers()`:

**LLM инструменты:**
- ❌ `/image` - генерация изображений (handlers.image)
- ❌ `/check` - проверка текста (handlers.check)
- ❌ `/rewrite` - переписывание текста (handlers.rewrite)
- ❌ `/explain` - объяснение текста (handlers.explain)

**Утилиты:**
- ❌ `/calc` - калькулятор (handlers.calc)
- ❌ `/calendar` - календарь (handlers.calendar)
- ❌ `/ask` - прямой вопрос LLM (handlers.ask)
- ❌ `/summary` - summarization (handlers.summary)

**Контекст диалога:**
- ❌ `/context_on` - включить контекст (handlers.context_on)
- ❌ `/context_off` - выключить контекст (handlers.context_off)
- ❌ `/context_clear` - очистить контекст (handlers.context_clear)
- ❌ `/context_status` - статус контекста (handlers.context_status)

**Напоминания (дополнительные):**
- ❌ `/reminder_on` - включить напоминание (handlers.reminder_on)
- ❌ `/reminder_off` - выключить напоминание (handlers.reminder_off)

**Admin команды:**
- ❌ `/allow` - добавить в whitelist (handlers.allow)
- ❌ `/deny` - удалить из whitelist (handlers.deny)
- ❌ `/allowlist` - показать whitelist (handlers.allowlist)

**Служебные:**
- ❌ `/cancel` - отмена wizard (handlers.cancel_command)
- ❌ `/last` - последняя задача (handlers.last)
- ❌ `/selfcheck` - самопроверка (handlers.selfcheck)
- ❌ `/health` - health check (handlers.health)

---

### 📋 Открытые PR

**PR #63:**
- Тема: facts commands routing + search-safe citation sanitizer
- Статус: OPEN
- Проблема: Возможно конфликтует с текущей веткой

**PR #64:**
- Тема: apply strict pseudo-source guard
- Статус: OPEN
- Проблема: Возможно конфликтует с текущей веткой

**Решение:** Проверить конфликты, смержить нужные изменения

---

## ✅ План действий

### Шаг 1: Исправить дублирование handlers ✅ (в процессе)
```python
# app/main.py, строки 177-189
# УДАЛИТЬ:
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_command))
    # ... остальные дубликаты ...

# ОСТАВИТЬ только:
    _register_handlers(application)
    application.add_error_handler(handlers.error_handler)
```

### Шаг 2: Добавить все недостающие команды
```python
def _register_handlers(application: Application) -> None:
    # Основные команды (уже есть)
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_command))
    # ... и т.д.
    
    # LLM инструменты (ДОБАВИТЬ)
    application.add_handler(CommandHandler("image", handlers.image))
    application.add_handler(CommandHandler("check", handlers.check))
    application.add_handler(CommandHandler("rewrite", handlers.rewrite))
    application.add_handler(CommandHandler("explain", handlers.explain))
    
    # Утилиты (ДОБАВИТЬ)
    application.add_handler(CommandHandler("calc", handlers.calc))
    application.add_handler(CommandHandler("calendar", handlers.calendar))
    application.add_handler(CommandHandler("ask", handlers.ask))
    application.add_handler(CommandHandler("summary", handlers.summary))
    
    # Контекст (ДОБАВИТЬ)
    application.add_handler(CommandHandler("context_on", handlers.context_on))
    application.add_handler(CommandHandler("context_off", handlers.context_off))
    application.add_handler(CommandHandler("context_clear", handlers.context_clear))
    application.add_handler(CommandHandler("context_status", handlers.context_status))
    
    # Напоминания доп (ДОБАВИТЬ)
    application.add_handler(CommandHandler("reminder_on", handlers.reminder_on))
    application.add_handler(CommandHandler("reminder_off", handlers.reminder_off))
    
    # Admin (ДОБАВИТЬ)
    application.add_handler(CommandHandler("allow", handlers.allow))
    application.add_handler(CommandHandler("deny", handlers.deny))
    application.add_handler(CommandHandler("allowlist", handlers.allowlist))
    
    # Служебные (ДОБАВИТЬ)
    application.add_handler(CommandHandler("cancel", handlers.cancel_command))
    application.add_handler(CommandHandler("last", handlers.last))
    application.add_handler(CommandHandler("selfcheck", handlers.selfcheck))
    application.add_handler(CommandHandler("health", handlers.health))
    
    # Callbacks и fallback (уже есть)
    application.add_handler(CallbackQueryHandler(handlers.static_callback, pattern="^cb:"))
    application.add_handler(CallbackQueryHandler(handlers.action_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.chat))
    application.add_handler(MessageHandler(filters.COMMAND, handlers.unknown_command))
```

### Шаг 3: Обновить README.md
Добавить все команды в документацию

### Шаг 4: Тестирование
- ✅ Компиляция Python
- ✅ Запуск бота
- ✅ Проверка всех команд
- ✅ Проверка меню
- ✅ Проверка wizard

---

## 🎯 Ожидаемый результат

После исправлений:
- ✅ Нет дублирования handlers
- ✅ Все команды зарегистрированы
- ✅ facts_on/facts_off работают
- ✅ Все LLM инструменты доступны
- ✅ Все admin команды доступны
- ✅ Бот полностью функционален

---

## 📊 Статус: В ПРОЦЕССЕ

- [x] Анализ проблемы
- [ ] Исправление дублирования
- [ ] Добавление команд
- [ ] Обновление документации
- [ ] Тестирование
- [ ] Коммит и push
