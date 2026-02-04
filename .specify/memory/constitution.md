# Secretary Bot — Constitution (UX-first)

## Mission
Build a “secretary” Telegram bot that is usable by non-technical people: clear menu, guided scenarios (wizard), fast actions, and predictable behavior.

## Non-negotiables
- No silence: every user input must produce a response (text and/or actions).
- No dead ends: every response must provide a next step (at least “🏠 Menu”).
- Actions over instructions: prefer buttons (actions) to “type command …”.
- Refusal is a UX state: refused must be polite, short, and offer alternatives.
- Confirmation before side effects: calendar writes, reminders create/update/delete must require explicit confirmation.
- Consistent tone: short, direct, human, no internal jargon (intent, TTL, tool, orchestrator, etc.) in user text.

## UX Contract (Response)
- text: human-readable, 1–4 short lines; no debug; no citations markers like [1].
- actions: present whenever it makes sense (menu navigation, retry, confirm/cancel, back).
- attachments: only when explicitly requested (image generation etc.).
- status:
  - ok: successful result
  - refused: cannot/should not do it; provide next actions
  - error: unexpected failure; provide retry + menu

## Wizard (Scenarios)
- Wizard state is per (user_id, chat_id).
- Must support: cancel, timeout, resume entry point, and “Back”.
- Timeout must reset state and return to Menu.

## Menus
- Main menu is the default home screen.
- Sections: Chat, Search, Images, Calculator, Calendar, Reminders, Settings.
- Every section entry point must explain what it does in one sentence and offer the top 3 actions.

## Safety / Data Integrity
- Never fabricate sources or links.
- Never perform side-effect operations without confirmation.
- Logs may contain technical details; user-facing text must not.

## Definition of Done for Stage 4
- All primary flows reachable from main menu.
- Unknown commands/messages get a friendly fallback + menu action.
- Every flow has: confirm/cancel, back, timeout handling.
- Manual UX walkthrough passes: user always knows what to do next.
