## Аудит (Orchestrator v2)

- Отчёт: `docs/audit_report.md`
- Сводка: `docs/audit_summary.md`

## Что сделано

- [ ] Единый Result Contract (`text/status/mode/intent/sources/actions/attachments/debug`) везде
- [ ] `ensure_valid()` нормализует поля, гарантирует non-empty `text`, чистит псевдо-источники при пустом `sources[]`
- [ ] Telegram UI всегда отправляет `result.text`, рендерит `actions`, обрабатывает `attachments`, не показывает `debug`
- [ ] Wizard/меню стабильны (timeout/cancel/re-entry/confirm)
- [ ] `web_search` возвращает стабильный `sources[] {title,url,snippet}`
- [ ] Strict facts mode: при пустом `sources[]` → `refused`, без “ответов по памяти”
- [ ] TZ: `Europe/Vilnius` (naive datetime запрещены)

## Тесты

- [ ] `python3 -m pytest` зелёный

## Что осталось (следующий этап)

- (заполнить)

