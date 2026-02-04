# Implementation Plan — Stage 4 UX (Menu, Flows, Fallbacks)

## Summary
Implement UX polish layer: a human main menu, consistent section entry points, action-first navigation, and mandatory fallbacks (unknown command, refused, error, wizard timeout/cancel/back).

## Technical Context
- Platform: Telegram bot (python-telegram-bot), deployed on Ubuntu via systemd.
- Existing: OrchestratorResult contract (text/status/mode/intent/sources/actions/attachments/debug), wizard framework, TTL/stale handling, intent logging.
- Constraint: No new tools/integrations in Stage 4; only UX behavior and scenario glue.

## Constitution Check
- No silence: every handler returns a visible reply.
- No dead ends: every reply provides “🏠 Menu” action (except trivial acks).
- Actions over instructions: prefer buttons.
- Refused is a UX state (short + alternatives).
- Confirm before side effects (calendar/reminders writes).

## Project Structure Touchpoints
- app/bot/handlers.py: menu routing, fallback handlers, wizard callbacks.
- app/core/orchestrator.py (or equivalent): ensure_valid enforcement if needed for UX contract.
- config/orchestrator.json (if used): menu config / defaults (only if already part of project patterns).

## Work Breakdown

### 1) Main Menu (Home)
- Add a single entry point that renders Home text + actions:
  💬 Чат, 🔍 Поиск, 🖼 Картинки, 🧮 Калькулятор, 📅 Календарь, ⏰ Напоминания, ⚙️ Настройки
- Ensure “🏠 Menu” action is universally available.

### 2) Section Entry Points
For each section implement:
- 1-sentence description
- top 3 actions + “🏠 Menu”
- route intents: menu.<section>

### 3) Fallbacks (Hard Requirements)
- Unknown command: status=refused, text “Неизвестная команда.”, action “🏠 Menu”
- Generic refused: short reason + alternatives
- Error: status=error, “Ошибка. Попробовать ещё раз?”, actions Retry + Menu
- Wizard mismatch input: clarify expected input + actions Back/Cancel/Menu
- Wizard timeout: reset state + Menu

### 4) Action Wiring
- Ensure handler rendering displays actions consistently (inline keyboard).
- Ensure every action callback routes to correct handler / wizard step.

### 5) Manual Walkthrough Checklist
- From Home reach all sections.
- Trigger each fallback case deliberately.
- Confirm: never silence, never dead end.

## Acceptance Criteria (DoD)
- Unknown commands never produce silence.
- Every section is reachable from Home.
- Every reply provides a next step (at least Menu).
- Wizard supports cancel/back/timeout in UX.
- Manual walkthrough passes end-to-end.
