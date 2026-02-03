from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Literal


ResultStatus = Literal["ok", "refused", "error"]
ResultMode = Literal["local", "llm", "tool"]

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class Attachment:
    type: str
    name: str
    path: str | None = None
    bytes: bytes | None = None
    url: str | None = None


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class OrchestratorResult:
    text: str
    status: ResultStatus
    mode: ResultMode
    intent: str
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
        if self.status not in {"ok", "refused", "error"}:
            errors.append("status must be ok/refused/error")
        if self.mode not in {"local", "llm", "tool"}:
            errors.append("mode must be local/llm/tool")
        if not isinstance(self.intent, str) or not self.intent.strip():
            errors.append("intent must be non-empty str")
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


def ensure_valid(
    result: OrchestratorResult,
    *,
    logger: logging.Logger | None = None,
    fallback_intent: str | None = None,
) -> OrchestratorResult:
    logger = logger or LOGGER
    try:
        result.validate()
        return result
    except Exception as exc:
        logger.exception("Result validation failed: %s", exc)
        return OrchestratorResult(
            text="Internal error",
            status="error",
            mode=result.mode if isinstance(result.mode, str) else "local",
            intent=fallback_intent or result.intent or "unknown",
            debug={"validation_error": str(exc)},
        )


def _action_payload_contains_debug(action: Action | dict[str, Any]) -> bool:
    if isinstance(action, Action):
        return "debug" in action.payload
    if isinstance(action, dict):
        payload = action.get("payload")
        return isinstance(payload, dict) and "debug" in payload
    return False


def _is_valid_source(item: Source | dict[str, Any]) -> bool:
    if isinstance(item, Source):
        return all(isinstance(value, str) for value in (item.title, item.url, item.snippet))
    if isinstance(item, dict):
        return all(isinstance(item.get(key), str) for key in ("title", "url", "snippet"))
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
