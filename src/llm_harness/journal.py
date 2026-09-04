from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any, Literal, Mapping, Sequence
from uuid import uuid4

from .coordination import CoordinationConfig, FileLease, state_lease
from .durable_io import atomic_append_jsonl, read_jsonl
from .config import ModelPriceConfig
from .metrics import AdapterMetricsSink, UsageContext, UsageLedger

JournalEventType = Literal["interaction", "tool_call", "tool_result", "state_transition", "artifact", "decision"]
JOURNAL_VERSION = "1.0"
JOURNAL_INGRESS_VERSION = "1.0"
SECRET_KEY_RE = re.compile(r"(token|secret|password|api[_-]?key|authorization|cookie)", re.IGNORECASE)
BOT_TOKEN_RE = re.compile(r"\b\d{6,14}:[A-Za-z0-9_-]{20,}\b")
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
ENV_SECRET_RE = re.compile(
    r"(?i)([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY)[A-Z0-9_]*\s*=\s*)([^\s;]+)"
)

# A key printed by a command has no recognizable name beside it, so the rules above
# never see it. These match the shape of the credential itself.
CREDENTIAL_SHAPE_RES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"), "<redacted-anthropic-key>"),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"), "<redacted-openai-key>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "<redacted-github-token>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"), "<redacted-github-token>"),
    (re.compile(r"\bxox[baprse]-[A-Za-z0-9-]{10,}"), "<redacted-slack-token>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<redacted-aws-key-id>"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{30,}"), "<redacted-google-key>"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        "<redacted-jwt>",
    ),
    (
        re.compile(
            r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----"
        ),
        "<redacted-private-key>",
    ),
)


@dataclass(frozen=True)
class JournalEvent:
    event_id: str
    created_at_utc: str
    event_type: JournalEventType
    actor: str
    recipient: str | None
    action: str
    content: Any
    metadata: Mapping[str, Any]
    task_id: str | None = None
    run_id: str | None = None
    correlation_id: str | None = None
    journal_version: str = JOURNAL_VERSION


class InteractionJournal:
    def __init__(
        self,
        state_root: Path,
        lease: FileLease | None = None,
        coordination: CoordinationConfig = CoordinationConfig(),
    ) -> None:
        self.state_root = state_root
        self.path = state_root / "interaction-journal.jsonl"
        self.lease = lease or state_lease(state_root, coordination)
        self.metrics = UsageLedger(state_root, lease=self.lease, coordination=coordination)

    def record(
        self,
        event_type: JournalEventType,
        actor: str,
        action: str,
        *,
        recipient: str | None = None,
        content: Any = None,
        metadata: Mapping[str, Any] | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> JournalEvent:
        event = JournalEvent(
            event_id=str(uuid4()),
            created_at_utc=datetime.now(UTC).isoformat(),
            event_type=event_type,
            actor=actor,
            recipient=recipient,
            action=action,
            content=redact(content),
            metadata=redact(dict(metadata or {})),
            task_id=task_id,
            run_id=run_id,
            correlation_id=correlation_id,
        )
        with self.lease.hold("journal.record"):
            atomic_append_jsonl(self.path, journal_event_payload(event))
        return event

    def events(self) -> tuple[JournalEvent, ...]:
        return tuple(journal_event_from_payload(data) for data in read_jsonl(self.path))

    def record_payload(self, payload: Mapping[str, Any]) -> JournalEvent:
        normalized = validate_journal_ingress(payload)
        return self.record(**normalized)

    def adapter_sink(
        self,
        actor: str,
        task_id: str | None = None,
        *,
        metric_context: UsageContext | None = None,
        prices: Sequence[ModelPriceConfig] = (),
    ):
        def sink(direction: str, payload: Mapping[str, Any]) -> None:
            outbound = direction == "outbound"
            self.record(
                "tool_call" if outbound else "tool_result",
                actor="supervisor" if outbound else actor,
                recipient=actor if outbound else "supervisor",
                action=f"adapter.{direction}",
                content=dict(payload),
                task_id=task_id,
            )

        if metric_context is None:
            return sink
        return AdapterMetricsSink(self.metrics, metric_context, prices, forward=sink)


def validate_journal_ingress(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "event_version", "event_type", "actor", "recipient", "action", "content",
        "metadata", "task_id", "run_id", "correlation_id",
    }
    unknown = set(payload) - required
    missing = required - set(payload)
    if missing or unknown:
        raise ValueError(
            "Journal ingress must use the closed v1 fields; "
            f"missing={','.join(sorted(missing)) or 'none'} unknown={','.join(sorted(unknown)) or 'none'}"
        )
    if payload["event_version"] != JOURNAL_INGRESS_VERSION:
        raise ValueError("Unsupported journal ingress version.")
    event_type = payload["event_type"]
    if event_type not in {"interaction", "tool_call", "tool_result", "state_transition", "artifact", "decision"}:
        raise ValueError("Unsupported journal event_type.")
    actor = payload["actor"]
    action = payload["action"]
    if not isinstance(actor, str) or not actor.strip() or not isinstance(action, str) or not action.strip():
        raise ValueError("Journal actor and action must be non-empty strings.")
    recipient = payload["recipient"]
    metadata = payload["metadata"]
    if recipient is not None and not isinstance(recipient, str):
        raise ValueError("Journal recipient must be a string or null.")
    if not isinstance(metadata, dict):
        raise ValueError("Journal metadata must be an object.")
    return {
        "event_type": event_type,
        "actor": actor.strip(),
        "action": action.strip(),
        "recipient": recipient,
        "content": payload["content"],
        "metadata": metadata,
        "task_id": _optional_text(payload["task_id"], "task_id"),
        "run_id": _optional_text(payload["run_id"], "run_id"),
        "correlation_id": _optional_text(payload["correlation_id"], "correlation_id"),
    }


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "<redacted>" if SECRET_KEY_RE.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text = BOT_TOKEN_RE.sub("<redacted-bot-token>", value)
        text = BEARER_RE.sub("Bearer <redacted>", text)
        text = ENV_SECRET_RE.sub(r"\1<redacted>", text)
        for pattern, replacement in CREDENTIAL_SHAPE_RES:
            text = pattern.sub(replacement, text)
        return text
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(str(value))


def journal_event_payload(event: JournalEvent) -> dict[str, Any]:
    return {
        "journal_version": event.journal_version,
        "event_id": event.event_id,
        "created_at_utc": event.created_at_utc,
        "event_type": event.event_type,
        "actor": event.actor,
        "recipient": event.recipient,
        "action": event.action,
        "content": event.content,
        "metadata": dict(event.metadata),
        "task_id": event.task_id,
        "run_id": event.run_id,
        "correlation_id": event.correlation_id,
    }


def render_journal_markdown(events: tuple[JournalEvent, ...]) -> str:
    lines = ["# HoH interaction journal", ""]
    if not events:
        return "\n".join(lines + ["- No events.", ""])
    for event in events:
        route = f"{event.actor} → {event.recipient}" if event.recipient else event.actor
        lines.extend(
            [
                f"## {event.created_at_utc} — {event.action}",
                "",
                f"- Type: `{event.event_type}`",
                f"- Route: {route}",
                f"- Task: `{event.task_id or ''}`",
                f"- Run: `{event.run_id or ''}`",
                "",
                "```json",
                json.dumps({"content": event.content, "metadata": event.metadata}, indent=2, ensure_ascii=False),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def journal_event_from_payload(data: Mapping[str, Any]) -> JournalEvent:
    return JournalEvent(
        journal_version=str(data.get("journal_version", JOURNAL_VERSION)),
        event_id=str(data["event_id"]),
        created_at_utc=str(data["created_at_utc"]),
        event_type=str(data["event_type"]),  # type: ignore[arg-type]
        actor=str(data["actor"]),
        recipient=str(data["recipient"]) if data.get("recipient") is not None else None,
        action=str(data["action"]),
        content=data.get("content"),
        metadata=dict(data.get("metadata") or {}),
        task_id=str(data["task_id"]) if data.get("task_id") is not None else None,
        run_id=str(data["run_id"]) if data.get("run_id") is not None else None,
        correlation_id=str(data["correlation_id"]) if data.get("correlation_id") is not None else None,
    )


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Journal {label} must be a non-empty string or null.")
    return value.strip()
