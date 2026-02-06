"""Extended tests for ensure_valid: ratelimited status, non-empty text, citation stripping."""
from __future__ import annotations

import pytest

from app.core.result import (
    Action,
    OrchestratorResult,
    Source,
    ensure_valid,
    ok,
    error,
    refused,
    ratelimited,
)


# ---- ratelimited status preserved ----

def test_ensure_valid_preserves_ratelimited_status() -> None:
    result = ratelimited("Too many requests", intent="rate_limit", mode="local")
    validated = ensure_valid(result)
    assert validated.status == "ratelimited"
    assert validated.text == "Too many requests"


def test_ensure_valid_dict_ratelimited() -> None:
    validated = ensure_valid({"status": "ratelimited", "text": "slow", "intent": "rate"})
    assert validated.status == "ratelimited"


# ---- non-empty text guarantee ----

def test_ensure_valid_empty_text_ok_gets_fallback() -> None:
    result = ok("", intent="test", mode="local")
    validated = ensure_valid(result)
    assert validated.text.strip() != ""
    assert "Готово" in validated.text


def test_ensure_valid_empty_text_error_gets_fallback() -> None:
    result = error("", intent="test", mode="local")
    validated = ensure_valid(result)
    assert validated.text.strip() != ""
    assert "ошибка" in validated.text.lower()


def test_ensure_valid_empty_text_refused_gets_fallback() -> None:
    result = refused("", intent="test", mode="local")
    validated = ensure_valid(result)
    assert validated.text.strip() != ""
    assert "отклон" in validated.text.lower()


def test_ensure_valid_whitespace_text_gets_fallback() -> None:
    validated = ensure_valid({"status": "ok", "text": "   ", "intent": "test"})
    assert validated.text.strip() != ""


def test_ensure_valid_none_text_gets_fallback() -> None:
    validated = ensure_valid({"status": "error", "text": None, "intent": "test"})
    assert validated.text.strip() != ""


# ---- citation markers stripped when sources empty ----

def test_ensure_valid_strips_bracket_citations_no_sources() -> None:
    result = ok("Answer [1] with fact [2].", intent="test", mode="llm")
    validated = ensure_valid(result)
    assert "[1]" not in validated.text
    assert "[2]" not in validated.text
    assert "Answer" in validated.text


def test_ensure_valid_strips_paren_citations_no_sources() -> None:
    result = ok("Answer (1) with fact (2).", intent="test", mode="llm")
    validated = ensure_valid(result)
    assert "(1)" not in validated.text
    assert "(2)" not in validated.text


def test_ensure_valid_keeps_citations_with_sources() -> None:
    sources = [Source(title="A", url="https://a.example", snippet="sa")]
    result = ok("Answer [1].", intent="test", mode="llm", sources=sources)
    validated = ensure_valid(result)
    assert "[1]" in validated.text
    assert len(validated.sources) == 1


# ---- actions and debug separation ----

def test_actions_not_leaked_to_debug() -> None:
    action = Action(id="test.action", label="Click", payload={"op": "do_thing"})
    result = ok("Hello", intent="test", mode="local", actions=[action], debug={"key": "val"})
    result.validate()
    assert "actions" not in result.debug
    public = result.to_public_dict()
    assert "debug" not in public


def test_debug_not_leaked_to_actions() -> None:
    bad_action = Action(id="test", label="Click", payload={"debug": "secret"})
    result = ok("Hello", intent="test", mode="local", actions=[bad_action])
    with pytest.raises(ValueError, match="actions must not include debug"):
        result.validate()


def test_debug_in_debug_field_raises() -> None:
    result = ok("Hello", intent="test", mode="local", debug={"actions": [1, 2]})
    with pytest.raises(ValueError, match="debug must not include actions"):
        result.validate()


# ---- all mandatory fields present ----

def test_all_mandatory_fields_present() -> None:
    result = ensure_valid(None)
    assert hasattr(result, "text")
    assert hasattr(result, "status")
    assert hasattr(result, "mode")
    assert hasattr(result, "intent")
    assert hasattr(result, "sources")
    assert hasattr(result, "actions")
    assert hasattr(result, "attachments")
    assert hasattr(result, "debug")
    assert isinstance(result.sources, list)
    assert isinstance(result.actions, list)
    assert isinstance(result.attachments, list)
    assert isinstance(result.debug, dict)


def test_ensure_valid_normalizes_bad_status() -> None:
    validated = ensure_valid({"status": "invalid_status", "text": "x", "intent": "test"})
    assert validated.status == "error"


def test_ensure_valid_normalizes_missing_mode() -> None:
    validated = ensure_valid({"status": "ok", "text": "x", "intent": "test"})
    assert validated.mode == "local"


def test_ensure_valid_normalizes_missing_intent() -> None:
    validated = ensure_valid({"status": "ok", "text": "x"})
    assert validated.intent == "unknown"


def test_ensure_valid_fallback_intent() -> None:
    validated = ensure_valid({"status": "ok", "text": "x"}, fallback_intent="custom")
    assert validated.intent == "custom"
