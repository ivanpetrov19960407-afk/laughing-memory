"""Orchestrator result contract: OrchestratorResult and helpers.

All handlers and tools return OrchestratorResult (text, status, mode, intent,
sources, attachments, actions, debug). ensure_valid() normalizes raw results;
ensure_safe_text_strict() enforces facts-only mode (sources + citations).
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal


ResultStatus = Literal["ok", "refused", "error", "ratelimited"]
ResultMode = Literal["local", "llm", "tool"]

LOGGER = logging.getLogger(__name__)


STRICT_REFUSAL_TEXT = "Не могу приводить источники/ссылки без поиска. Открой /menu → Поиск."
STRICT_NO_SOURCES_TEXT = "Не могу ответить без источников. Открой /menu → Поиск."
TEXT_LENGTH_LIMIT = 4000

_PSEUDO_SOURCE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("bracket_citation", re.compile(r"\[\s*\d+\s*\]")),
    ("paren_citation", re.compile(r"\(\s*\d+\s*\)")),
    (
        "sources_keywords",
        re.compile(r"(?i)\b(источник|источники|ссылки|sources|references|bibliography)\b"),
    ),
    ("attribution_phrases", re.compile(r"(?i)\b(согласно|по данным|according to|as reported by)\b")),
    ("url", re.compile(r"https?://\S+", re.IGNORECASE)),
    ("domain", re.compile(r"\b\w+\.(?:com|ru|net|org|io|dev|app|ai)\b", re.IGNORECASE)),
]


@dataclass(frozen=True)
class Source:
    """A single reference (e.g. search result): title, URL, optional snippet."""

    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class Attachment:
    """File or URL attachment: type, name, and path/bytes/url for delivery."""

    type: str
    name: str
    path: str | None = None
    bytes: bytes | None = None
    url: str | None = None


@dataclass(frozen=True)
class Action:
    """Inline button: id (intent/op), label, payload for callback."""

    id: str
    label: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class OrchestratorResult:
    """Unified response from orchestrator/tools: text, status, mode, intent, sources, actions, attachments, debug."""

    text: str
    status: ResultStatus
    mode: ResultMode
    intent: str | None
    request_id: str = ""
    sources: list[Source] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "status": self.status,
            "mode": self.mode,
            "intent": self.intent,
            "request_id": self.request_id,
            "sources": [_source_to_dict(source) for source in self.sources],
            "attachments": [_attachment_to_dict(attachment) for attachment in self.attachments],
            "actions": [_action_to_dict(action) for action in self.actions],
        }

    def to_log_dict(self) -> dict[str, Any]:
        payload = self.to_public_dict()
        payload["debug"] = self.debug
        return payload

    def to_public_json(self) -> str:
        return _json_dumps(self.to_public_dict())

    def to_log_json(self) -> str:
        return _json_dumps(self.to_log_dict())

    def validate(self) -> None:
        errors = []
        if not isinstance(self.text, str):
            errors.append("text must be str")
        if self.status not in {"ok", "refused", "error", "ratelimited"}:
            errors.append("status must be ok/refused/error/ratelimited")
        if self.mode not in {"local", "llm", "tool"}:
            errors.append("mode must be local/llm/tool")
        if self.intent is not None:
            if not isinstance(self.intent, str) or not self.intent.strip():
                errors.append("intent must be non-empty str or None")
            elif "." not in self.intent:
                errors.append("intent must include namespace.action")
        if not isinstance(self.request_id, str):
            errors.append("request_id must be str")
        if not isinstance(self.sources, list) or any(not _is_valid_source(item) for item in self.sources):
            errors.append("sources must be list of valid Source entries")
        if not isinstance(self.attachments, list) or any(
            not _is_valid_attachment(item) for item in self.attachments
        ):
            errors.append("attachments must be list of valid Attachment entries")
        if not isinstance(self.actions, list) or any(not _is_valid_action(item) for item in self.actions):
            errors.append("actions must be list of valid Action entries")
        if not isinstance(self.debug, dict):
            errors.append("debug must be dict")
        if "actions" in self.debug:
            errors.append("debug must not include actions data")
        if any(_action_payload_contains_debug(item) for item in self.actions):
            errors.append("actions must not include debug data")
        if not _is_json_safe(self.debug):
            errors.append("debug must be JSON-serializable")
        if any(not _is_json_safe(_action_payload(item)) for item in self.actions):
            errors.append("action payloads must be JSON-serializable")
        if errors:
            raise ValueError("; ".join(errors))


def ok(
    text: str,
    intent: str,
    *,
    mode: ResultMode = "local",
    sources: list[Source] | None = None,
    actions: list[Action] | None = None,
    attachments: list[Attachment] | None = None,
    debug: dict[str, Any] | None = None,
) -> OrchestratorResult:
    """Build OrchestratorResult with status='ok'."""
    return OrchestratorResult(
        text=text,
        status="ok",
        mode=mode,
        intent=intent,
        sources=sources or [],
        actions=actions or [],
        attachments=attachments or [],
        debug=debug or {},
    )


def refused(
    text: str,
    intent: str,
    *,
    mode: ResultMode = "local",
    sources: list[Source] | None = None,
    actions: list[Action] | None = None,
    attachments: list[Attachment] | None = None,
    debug: dict[str, Any] | None = None,
) -> OrchestratorResult:
    """Build OrchestratorResult with status='refused' (policy/safety)."""
    return OrchestratorResult(
        text=text,
        status="refused",
        mode=mode,
        intent=intent,
        sources=sources or [],
        actions=actions or [],
        attachments=attachments or [],
        debug=debug or {},
    )


def error(
    text: str,
    intent: str,
    *,
    mode: ResultMode = "local",
    sources: list[Source] | None = None,
    actions: list[Action] | None = None,
    attachments: list[Attachment] | None = None,
    debug: dict[str, Any] | None = None,
) -> OrchestratorResult:
    """Build OrchestratorResult with status='error'."""
    return OrchestratorResult(
        text=text,
        status="error",
        mode=mode,
        intent=intent,
        sources=sources or [],
        actions=actions or [],
        attachments=attachments or [],
        debug=debug or {},
    )


def ratelimited(
    text: str,
    intent: str,
    *,
    mode: ResultMode = "local",
    sources: list[Source] | None = None,
    actions: list[Action] | None = None,
    attachments: list[Attachment] | None = None,
    debug: dict[str, Any] | None = None,
) -> OrchestratorResult:
    """Build OrchestratorResult with status='ratelimited'."""
    return OrchestratorResult(
        text=text,
        status="ratelimited",
        mode=mode,
        intent=intent,
        sources=sources or [],
        actions=actions or [],
        attachments=attachments or [],
        debug=debug or {},
    )


def normalize_to_orchestrator_result(
    result: OrchestratorResult | dict[str, Any] | str | None,
    *,
    logger: logging.Logger | None = None,
    fallback_intent: str | None = None,
) -> OrchestratorResult:
    """Alias for ensure_valid(); normalizes raw result to valid OrchestratorResult."""
    return ensure_valid(result, logger=logger, fallback_intent=fallback_intent)


def ensure_valid(
    result: OrchestratorResult | dict[str, Any] | str | None,
    *,
    logger: logging.Logger | None = None,
    fallback_intent: str | None = None,
) -> OrchestratorResult:
    """Normalize result (OrchestratorResult, dict, str, None) to a valid OrchestratorResult."""
    logger = logger or LOGGER
    if result is None:
        return OrchestratorResult(
            text="Internal error",
            status="error",
            mode="local",
            intent="internal.error",
        )
    if isinstance(result, OrchestratorResult):
        payload: dict[str, Any] = {
            "text": result.text,
            "status": result.status,
            "mode": result.mode,
            "intent": result.intent,
            "request_id": result.request_id,
            "sources": result.sources,
            "actions": result.actions,
            "attachments": result.attachments,
            "debug": result.debug,
        }
    elif isinstance(result, str):
        payload = {"text": result}
    elif isinstance(result, dict):
        payload = result
    else:
        logger.warning("Result validation: unexpected payload type %s", type(result).__name__)
        payload = {}

    status = payload.get("status")
    if status not in {"ok", "refused", "error", "ratelimited"}:
        status = None
    text = payload.get("text")
    if status is None:
        status = "ok" if isinstance(text, str) and text.strip() else "error"

    if text is None:
        text = ""
    elif not isinstance(text, str):
        text = str(text)
    text = text.strip("\n")

    intent_value = _normalize_intent(payload.get("intent"), fallback_intent=fallback_intent)

    mode_value = payload.get("mode")
    if mode_value not in {"local", "llm", "tool"}:
        mode_value = "local"

    request_id_value = payload.get("request_id")
    if not isinstance(request_id_value, str):
        request_id_value = ""

    known_keys = {"text", "status", "mode", "intent", "request_id", "sources", "actions", "attachments", "debug"}
    extra_fields = {key: value for key, value in payload.items() if key not in known_keys}

    raw_sources = payload.get("sources")
    sources: list[Source] = []
    invalid_sources: list[Any] = []
    if isinstance(raw_sources, list):
        for item in raw_sources:
            if isinstance(item, Source):
                if item.url.strip():
                    snippet_value = item.snippet or ""
                    sources.append(
                        Source(
                            title=item.title or item.url,
                            url=item.url.strip(),
                            snippet=snippet_value,
                        )
                    )
                continue
            if isinstance(item, dict):
                title = item.get("title")
                url = item.get("url")
                snippet = item.get("snippet")
                if isinstance(url, str) and url.strip():
                    sources.append(
                        Source(
                            title=title.strip() if isinstance(title, str) and title.strip() else url.strip(),
                            url=url.strip(),
                            snippet=snippet.strip() if isinstance(snippet, str) else "",
                        )
                    )
                else:
                    invalid_sources.append(item)
            else:
                invalid_sources.append(item)
    elif raw_sources is not None:
        invalid_sources.append(raw_sources)

    raw_actions = payload.get("actions")
    actions: list[Action] = []
    invalid_actions: list[Any] = []
    action_debug: list[dict[str, Any]] = []
    if isinstance(raw_actions, list):
        for item in raw_actions:
            if isinstance(item, Action):
                payload_copy = dict(item.payload)
                if "debug" in payload_copy:
                    action_debug.append({"id": item.id, "debug": payload_copy.pop("debug")})
                if item.id.strip() and item.label.strip():
                    actions.append(Action(id=item.id, label=item.label, payload=payload_copy))
                else:
                    invalid_actions.append(item)
                continue
            if isinstance(item, dict):
                action_id = item.get("id")
                label = item.get("label")
                action_payload = item.get("payload")
                if (
                    isinstance(action_id, str)
                    and action_id.strip()
                    and isinstance(label, str)
                    and label.strip()
                    and isinstance(action_payload, dict)
                ):
                    payload_copy = dict(action_payload)
                    if "debug" in payload_copy:
                        action_debug.append({"id": action_id, "debug": payload_copy.pop("debug")})
                    actions.append(Action(id=action_id, label=label, payload=payload_copy))
                else:
                    invalid_actions.append(item)
            else:
                invalid_actions.append(item)
    elif raw_actions is not None:
        invalid_actions.append(raw_actions)

    raw_attachments = payload.get("attachments")
    attachments: list[Attachment] = []
    invalid_attachments: list[Any] = []
    if isinstance(raw_attachments, list):
        for item in raw_attachments:
            if isinstance(item, Attachment):
                attachments.append(item)
                continue
            if isinstance(item, dict):
                attachment_type = item.get("type")
                name = item.get("name")
                path = item.get("path")
                payload_bytes = item.get("bytes")
                url = item.get("url")
                if _validate_attachment_fields(attachment_type, name, path, payload_bytes, url):
                    attachments.append(
                        Attachment(
                            type=attachment_type,
                            name=name,
                            path=path,
                            bytes=payload_bytes,
                            url=url,
                        )
                    )
                else:
                    invalid_attachments.append(item)
            else:
                invalid_attachments.append(item)
    elif raw_attachments is not None:
        invalid_attachments.append(raw_attachments)

    debug = payload.get("debug")
    if not isinstance(debug, dict):
        if debug is not None:
            extra_fields["debug"] = debug
        debug = {}
    else:
        debug = dict(debug)

    if "actions" in debug:
        extra_fields["actions_from_debug"] = debug.pop("actions")

    if extra_fields:
        debug.setdefault("extra_fields", {}).update(extra_fields)
    if invalid_sources:
        debug.setdefault("invalid_sources", []).extend(invalid_sources)
    if invalid_actions:
        debug.setdefault("invalid_actions", []).extend(invalid_actions)
    if invalid_attachments:
        debug.setdefault("invalid_attachments", []).extend(invalid_attachments)
    if action_debug:
        debug.setdefault("action_debug", []).extend(action_debug)

    if not sources:
        text = _strip_pseudo_sources(text)

    if text and len(text) > TEXT_LENGTH_LIMIT:
        original_length = len(text)
        text = text[: max(0, TEXT_LENGTH_LIMIT - 1)].rstrip() + "…"
        debug.setdefault("text_truncated", True)
        debug.setdefault("text_original_length", original_length)

    if not text.strip():
        text = "Не могу выполнить запрос. Открой /menu."
        if status == "ok":
            status = "refused"

    return OrchestratorResult(
        text=text,
        status=status,
        mode=mode_value,
        intent=intent_value,
        request_id=request_id_value,
        sources=sources,
        actions=actions,
        attachments=attachments,
        debug=debug,
    )


def ensure_safe_text_strict(
    result: OrchestratorResult,
    facts_enabled: bool,
    *,
    allow_sources_in_text: bool = False,
) -> OrchestratorResult:
    """Enforce facts-only: require sources and [N] citations when facts_enabled; strip pseudo-sources."""
    if facts_enabled and not result.sources:
        return OrchestratorResult(
            text=STRICT_NO_SOURCES_TEXT,
            status="refused",
            mode=result.mode,
            intent=result.intent,
            request_id=result.request_id,
            sources=[],
            attachments=[],
            actions=result.actions,
            debug=result.debug,
        )
    if facts_enabled and result.sources:
        # Цитаты [N] обязательны в теле ответа, а не только в блоке «Источники:»
        body_only = re.sub(
            r"\n*\s*Источники\s*:\s*[\s\S]*$",
            "",
            result.text or "",
            flags=re.IGNORECASE,
        ).strip()
        numbers = _extract_citation_numbers(body_only)
        if not numbers:
            return OrchestratorResult(
                text=STRICT_NO_SOURCES_TEXT,
                status="refused",
                mode=result.mode,
                intent=result.intent,
                request_id=result.request_id,
                sources=[],
                attachments=[],
                actions=result.actions,
                debug={**result.debug, "reason": "facts_no_citations"},
            )
        invalid = [num for num in numbers if num < 1 or num > len(result.sources)]
        if invalid:
            return OrchestratorResult(
                text=STRICT_NO_SOURCES_TEXT,
                status="refused",
                mode=result.mode,
                intent=result.intent,
                request_id=result.request_id,
                sources=[],
                attachments=[],
                actions=result.actions,
                debug={**result.debug, "reason": "facts_invalid_citations", "invalid": invalid},
            )
    if allow_sources_in_text or bool(result.sources):
        return result
    text = result.text or ""
    if not text.strip():
        return result
    matched_patterns = [label for label, pattern in _PSEUDO_SOURCE_PATTERNS if pattern.search(text)]
    if not matched_patterns:
        return result
    LOGGER.warning(
        "Text safety (strict): blocked pseudo-sources patterns=%s intent=%s request_id=%s facts_enabled=%s",
        matched_patterns,
        result.intent,
        result.request_id or "-",
        facts_enabled,
    )
    if not facts_enabled:
        cleaned = _strip_pseudo_sources(text)
        if cleaned:
            return OrchestratorResult(
                text=cleaned,
                status=result.status,
                mode=result.mode,
                intent=result.intent,
                request_id=result.request_id,
                sources=[],
                attachments=result.attachments,
                actions=result.actions,
                debug=result.debug,
            )
    return OrchestratorResult(
        text=STRICT_REFUSAL_TEXT,
        status="refused",
        mode=result.mode,
        intent=result.intent,
        request_id=result.request_id,
        sources=[],
        attachments=[],
        actions=result.actions,
        debug=result.debug,
    )


def _normalize_intent(value: object, *, fallback_intent: str | None = None) -> str:
    fallback = fallback_intent or "unknown.unknown"
    if not isinstance(value, str):
        return fallback
    cleaned = value.strip()
    if not cleaned or "." not in cleaned:
        return fallback
    return cleaned


def _extract_citation_numbers(text: str) -> list[int]:
    numbers: list[int] = []
    for match in re.findall(r"\[\s*(\d+)\s*\]", text or ""):
        try:
            numbers.append(int(match))
        except ValueError:
            continue
    return numbers


def _strip_pseudo_sources(text: str) -> str:
    cleaned = re.sub(r"\n*\s*Источники\s*:\s*[\s\S]*$", "", text, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\[\s*\d+\s*\]", "", cleaned)
    cleaned = re.sub(r"\(\s*\d+\s*\)", "", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(согласно|по данным|according to|as reported by)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _action_payload_contains_debug(action: Action | dict[str, Any]) -> bool:
    if isinstance(action, Action):
        return "debug" in action.payload
    if isinstance(action, dict):
        payload = action.get("payload")
        return isinstance(payload, dict) and "debug" in payload
    return False


def _action_payload(action: Action | dict[str, Any]) -> Any:
    if isinstance(action, Action):
        return action.payload
    if isinstance(action, dict):
        return action.get("payload")
    return None


def _is_json_safe(payload: Any) -> bool:
    if payload is None:
        return True
    if isinstance(payload, (str, int, float, bool)):
        return True
    if isinstance(payload, (bytes, bytearray)):
        return True
    if isinstance(payload, dict) or isinstance(payload, list):
        try:
            _json_dumps(payload)
        except Exception:
            return False
        return True
    try:
        _json_dumps(payload)
    except Exception:
        return False
    return True


def _is_valid_source(item: Source | dict[str, Any]) -> bool:
    if isinstance(item, Source):
        return (
            all(isinstance(value, str) for value in (item.title, item.url, item.snippet))
            and item.url.strip() != ""
        )
    if isinstance(item, dict):
        url = item.get("url")
        return (
            all(isinstance(item.get(key), str) for key in ("title", "url", "snippet"))
            and isinstance(url, str)
            and url.strip() != ""
        )
    return False


def _is_valid_attachment(item: Attachment | dict[str, Any]) -> bool:
    if isinstance(item, Attachment):
        return _validate_attachment_fields(item.type, item.name, item.path, item.bytes, item.url)
    if isinstance(item, dict):
        return _validate_attachment_fields(
            item.get("type"),
            item.get("name"),
            item.get("path"),
            item.get("bytes"),
            item.get("url"),
        )
    return False


def _validate_attachment_fields(
    attachment_type: Any,
    name: Any,
    path: Any,
    payload_bytes: Any,
    url: Any,
) -> bool:
    if not isinstance(attachment_type, str) or not isinstance(name, str):
        return False
    if payload_bytes is not None and not isinstance(payload_bytes, (bytes, bytearray)):
        return False
    if path is not None and not isinstance(path, str):
        return False
    if url is not None and not isinstance(url, str):
        return False
    return any(value is not None for value in (path, payload_bytes, url))


def _is_valid_action(item: Action | dict[str, Any]) -> bool:
    if isinstance(item, Action):
        return isinstance(item.id, str) and isinstance(item.label, str) and isinstance(item.payload, dict)
    if isinstance(item, dict):
        return (
            isinstance(item.get("id"), str)
            and isinstance(item.get("label"), str)
            and isinstance(item.get("payload"), dict)
        )
    return False


def _attachment_to_dict(attachment: Attachment | dict[str, Any]) -> dict[str, Any]:
    if isinstance(attachment, dict):
        payload_bytes = attachment.get("bytes")
        if isinstance(payload_bytes, (bytes, bytearray)):
            payload_bytes = base64.b64encode(payload_bytes).decode("utf-8")
        return {
            "type": attachment.get("type"),
            "name": attachment.get("name"),
            "path": attachment.get("path"),
            "bytes": payload_bytes,
            "url": attachment.get("url"),
        }
    return {
        "type": attachment.type,
        "name": attachment.name,
        "path": attachment.path,
        "bytes": base64.b64encode(attachment.bytes).decode("utf-8") if attachment.bytes else None,
        "url": attachment.url,
    }


def _source_to_dict(source: Source | dict[str, Any]) -> dict[str, Any]:
    if isinstance(source, dict):
        return {
            "title": source.get("title"),
            "url": source.get("url"),
            "snippet": source.get("snippet"),
        }
    return {"title": source.title, "url": source.url, "snippet": source.snippet}


def _action_to_dict(action: Action | dict[str, Any]) -> dict[str, Any]:
    if isinstance(action, dict):
        return {
            "id": action.get("id"),
            "label": action.get("label"),
            "payload": action.get("payload"),
        }
    return {"id": action.id, "label": action.label, "payload": action.payload}


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, default=str)
