from __future__ import annotations

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
    bytes: str | None = None
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
            "sources": [
                {"title": source.title, "url": source.url, "snippet": source.snippet}
                for source in self.sources
            ],
            "attachments": [
                {
                    "type": attachment.type,
                    "name": attachment.name,
                    "path": attachment.path,
                    "bytes": attachment.bytes,
                    "url": attachment.url,
                }
                for attachment in self.attachments
            ],
            "actions": [
                {"id": action.id, "label": action.label, "payload": action.payload}
                for action in self.actions
            ],
        }

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
        if not isinstance(self.sources, list) or any(not isinstance(item, Source) for item in self.sources):
            errors.append("sources must be list[Source]")
        if not isinstance(self.attachments, list) or any(
            not isinstance(item, Attachment) for item in self.attachments
        ):
            errors.append("attachments must be list[Attachment]")
        if not isinstance(self.actions, list) or any(not isinstance(item, Action) for item in self.actions):
            errors.append("actions must be list[Action]")
        if not isinstance(self.debug, dict):
            errors.append("debug must be dict")
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
