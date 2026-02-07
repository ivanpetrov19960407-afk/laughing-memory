from __future__ import annotations

from app.core.recurrence_scope import RecurrenceScope, parse_recurrence_scope


def test_parse_recurrence_scope_this() -> None:
    assert parse_recurrence_scope("только это") == RecurrenceScope.THIS
    assert parse_recurrence_scope("только сегодня") == RecurrenceScope.THIS
    assert parse_recurrence_scope("только в этот раз") == RecurrenceScope.THIS


def test_parse_recurrence_scope_all() -> None:
    assert parse_recurrence_scope("всю серию") == RecurrenceScope.ALL
    assert parse_recurrence_scope("все") == RecurrenceScope.ALL
    assert parse_recurrence_scope("всегда") == RecurrenceScope.ALL


def test_parse_recurrence_scope_future() -> None:
    assert parse_recurrence_scope("начиная с завтра") == RecurrenceScope.FUTURE
    assert parse_recurrence_scope("дальше") == RecurrenceScope.FUTURE
    assert parse_recurrence_scope("со следующего раза") == RecurrenceScope.FUTURE
