# Audit Report: Orchestrator v2

Дата: 2026-02-05

## 1. Архитектура
- Orchestrator v2 ведёт принятие решений и маршрутизацию (app/core/orchestrator.py).
- Telegram handlers в основном маршрутизируют события и вызывают send_result для отображения.
- Инструменты (tools_calendar, tools_llm) и задачи (tasks) возвращают OrchestratorResult.
- В handlers остаётся прикладная логика для UI-команд (calc/calendar/admin/menus) и
  сценариев (wizard). Это соответствует текущей архитектуре, но является зоной
  потенциального "утечки логики" из core. Осознанно не перерабатывалось, чтобы
  не менять архитектурный слой в рамках аудита.

## 2. Контракт OrchestratorResult
Проверены все места формирования результата (text/status/mode/intent/sources/actions/
attachments/debug).

Найдено и исправлено:
- ensure_valid приводил статус ratelimited к error, хотя контракт допускает ratelimited.
- ensure_valid не нормализовал неверный mode (оставлял произвольные строки).
- Несколько путей /search формировали refused() с двумя позиционными строками и
  одновременно intent=..., что приводило к TypeError.

## 3. Валидация
- send_result всегда вызывает ensure_valid перед отправкой пользователю.
- Orchestrator и инструменты также используют ensure_valid.
- Прямой bypass валидации не найден.
- UI-функция _send_reply_keyboard_remove отправляет техническое сообщение напрямую
  (без OrchestratorResult) только для удаления клавиатуры — это оставлено как есть.

## 4. Handlers (Telegram слой)
- Основной путь отображения результата проходит через send_result.
- Логика формирования текстов для служебных команд и меню остаётся в handlers;
  это текущий дизайн, изменений не вносилось.

## 5. Безопасность и стабильность
- Защита от пустых/слишком длинных входов есть в Orchestrator.
- send_result гарантирует непустой текст ответа ("Нет ответа.") и безопасную
  обработку пустых списков sources/actions/attachments.
- Статусы нормализуются к допустимым значениям, включая ratelimited.

## Исправления
- app/core/orchestrator.py: исправлены refused() для /search без payload (TypeError).
- app/core/result.py: ensure_valid сохраняет ratelimited и нормализует mode.
- app/bot/handlers.py: удалён мёртвый return в /search и добавлен импорт io для attachments.
- tests/test_result_contract.py: добавлены проверки ensure_valid для ratelimited и invalid mode.

## Осознанно не тронуто
- Возврат None из WizardManager для сценариев без активного состояния — это
  контракт для fallback в чат.
- Архитектурный перенос логики из handlers в core не выполнялся (вне scope аудита).
