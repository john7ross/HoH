from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from .config import ModelPriceConfig
from .coordination import CoordinationConfig, FileLease, state_lease
from .durable_io import atomic_append_jsonl, read_jsonl
from .domain import ModelInvocationEvidence


METRICS_VERSION = "1.0"


@dataclass(frozen=True)
class UsageContext:
    role: str
    agent: str
    provider: str
    model: str
    driver: str
    task_id: str | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class UsageEvent:
    event_id: str
    created_at_utc: str
    event_kind: str
    role: str
    agent: str
    provider: str
    model: str
    driver: str
    task_id: str | None
    run_id: str | None
    invocation_id: str | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    context_used_tokens: int | None
    context_size_tokens: int | None
    cost_amount: float | None
    cost_currency: str | None
    cost_source: str | None
    quality_score: float | None
    outcome: str
    metrics_version: str = METRICS_VERSION


class UsageLedger:
    def __init__(
        self,
        state_root: Path,
        lease: FileLease | None = None,
        coordination: CoordinationConfig = CoordinationConfig(),
    ) -> None:
        self.state_root = state_root
        self.path = state_root / "usage-metrics.jsonl"
        self.lease = lease or state_lease(state_root, coordination)

    def record(self, event: UsageEvent) -> UsageEvent:
        with self.lease.hold("metrics.record"):
            atomic_append_jsonl(self.path, usage_event_payload(event))
        return event

    def events(self) -> tuple[UsageEvent, ...]:
        return tuple(usage_event_from_payload(item) for item in read_jsonl(self.path))

    def record_quality(self, context: UsageContext, score: float, outcome: str) -> UsageEvent:
        if not 0.0 <= score <= 1.0:
            raise ValueError("quality score must be between 0 and 1.")
        return self.record(
            UsageEvent(
                event_id=str(uuid4()),
                created_at_utc=datetime.now(UTC).isoformat(),
                event_kind="quality",
                role=context.role,
                agent=context.agent,
                provider=context.provider,
                model=context.model,
                driver=context.driver,
                task_id=context.task_id,
                run_id=context.run_id,
                invocation_id=None,
                duration_ms=None,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                context_used_tokens=None,
                context_size_tokens=None,
                cost_amount=None,
                cost_currency=None,
                cost_source=None,
                quality_score=score,
                outcome=outcome,
            )
        )

    def record_model_evidence(
        self,
        context: UsageContext,
        evidence: ModelInvocationEvidence,
        prices: Sequence[ModelPriceConfig] = (),
        *,
        outcome: str = "success",
    ) -> UsageEvent:
        calculated = _calculated_cost(
            prices,
            evidence.provider,
            evidence.model,
            evidence.input_tokens,
            evidence.output_tokens,
        )
        return self.record(
            UsageEvent(
                event_id=str(uuid4()),
                created_at_utc=datetime.now(UTC).isoformat(),
                event_kind="invocation",
                role=context.role,
                agent=context.agent,
                provider=evidence.provider,
                model=evidence.model,
                driver=context.driver,
                task_id=context.task_id,
                run_id=context.run_id,
                invocation_id=evidence.request_id,
                duration_ms=evidence.latency_ms,
                input_tokens=evidence.input_tokens,
                output_tokens=evidence.output_tokens,
                total_tokens=evidence.total_tokens,
                context_used_tokens=None,
                context_size_tokens=None,
                cost_amount=calculated[0] if calculated else None,
                cost_currency=calculated[1] if calculated else None,
                cost_source="configured_price" if calculated else None,
                quality_score=None,
                outcome=outcome,
            )
        )

    def summary(self) -> dict[str, Any]:
        groups: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        for event in self.events():
            key = (event.role, event.agent, event.provider, event.model, event.driver)
            group = groups.setdefault(
                key,
                {
                    "role": event.role,
                    "agent": event.agent,
                    "provider": event.provider,
                    "model": event.model,
                    "driver": event.driver,
                    "invocations": 0,
                    "successful_invocations": 0,
                    "duration_ms": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "unknown_token_invocations": 0,
                    "costs": {},
                    "quality_samples": 0,
                    "quality_total": 0.0,
                },
            )
            if event.event_kind == "invocation":
                group["invocations"] += 1
                group["successful_invocations"] += int(event.outcome == "success")
                group["duration_ms"] += event.duration_ms or 0
                if event.total_tokens is None and event.input_tokens is None and event.output_tokens is None:
                    group["unknown_token_invocations"] += 1
                group["input_tokens"] += event.input_tokens or 0
                group["output_tokens"] += event.output_tokens or 0
                group["total_tokens"] += event.total_tokens or (
                    (event.input_tokens or 0) + (event.output_tokens or 0)
                )
                if event.cost_amount is not None and event.cost_currency:
                    costs = group["costs"]
                    costs[event.cost_currency] = round(costs.get(event.cost_currency, 0.0) + event.cost_amount, 8)
            if event.quality_score is not None:
                group["quality_samples"] += 1
                group["quality_total"] += event.quality_score
        rows = []
        for group in groups.values():
            samples = group.pop("quality_samples")
            total = group.pop("quality_total")
            group["quality_score"] = round(total / samples, 4) if samples else None
            group["quality_samples"] = samples
            rows.append(group)
        rows.sort(key=lambda item: (item["role"], item["agent"], item["model"], item["driver"]))
        return {"metrics_version": METRICS_VERSION, "groups": rows}


class AdapterMetricsSink:
    """Combines cumulative adapter signals into one immutable invocation event."""

    def __init__(
        self,
        ledger: UsageLedger,
        context: UsageContext,
        prices: Sequence[ModelPriceConfig] = (),
        forward: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.ledger = ledger
        self.context = context
        self.prices = tuple(prices)
        self.forward = forward
        self._started: float | None = None
        self._usage: dict[str, Any] = {}
        self._recorded = False

    def __call__(self, direction: str, payload: Mapping[str, Any]) -> None:
        if self.forward is not None:
            self.forward(direction, payload)
        try:
            self._observe(direction, payload)
        except Exception:
            # Metrics must never alter an agent protocol result.
            return

    def _observe(self, direction: str, payload: Mapping[str, Any]) -> None:
        if direction == "outbound":
            if self._started is None:
                self._started = time.monotonic()
            return
        if direction != "inbound" or self._recorded:
            return
        self._merge(_extract_usage(payload))
        if not _is_terminal_adapter_payload(payload):
            return
        elapsed = max(0, int((time.monotonic() - (self._started or time.monotonic())) * 1000))
        duration_ms = _integer(self._usage.get("duration_ms")) or elapsed
        input_tokens = _integer(self._usage.get("input_tokens"))
        output_tokens = _integer(self._usage.get("output_tokens"))
        total_tokens = _integer(self._usage.get("total_tokens"))
        reported_cost = _number(self._usage.get("cost_amount"))
        currency = str(self._usage.get("cost_currency") or "USD").upper() if reported_cost is not None else None
        cost_source = "provider" if reported_cost is not None else None
        if reported_cost is None:
            calculated = _calculated_cost(
                self.prices,
                str(self._usage.get("provider") or self.context.provider),
                str(self._usage.get("model") or self.context.model),
                input_tokens,
                output_tokens,
            )
            if calculated is not None:
                reported_cost, currency = calculated
                cost_source = "configured_price"
        outcome = "failure" if _payload_failed(payload) else "success"
        self.ledger.record(
            UsageEvent(
                event_id=str(uuid4()),
                created_at_utc=datetime.now(UTC).isoformat(),
                event_kind="invocation",
                role=self.context.role,
                agent=self.context.agent,
                provider=str(self._usage.get("provider") or self.context.provider),
                model=str(self._usage.get("model") or self.context.model),
                driver=self.context.driver,
                task_id=self.context.task_id,
                run_id=self.context.run_id,
                invocation_id=_text(self._usage.get("invocation_id")),
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                context_used_tokens=_integer(self._usage.get("context_used_tokens")),
                context_size_tokens=_integer(self._usage.get("context_size_tokens")),
                cost_amount=reported_cost,
                cost_currency=currency,
                cost_source=cost_source,
                quality_score=None,
                outcome=outcome,
            )
        )
        self._recorded = True

    def _merge(self, incoming: Mapping[str, Any]) -> None:
        for key, value in incoming.items():
            if value is None:
                continue
            if key in {
                "input_tokens", "output_tokens", "total_tokens", "context_used_tokens",
                "context_size_tokens", "duration_ms", "cost_amount",
            }:
                current = _number(self._usage.get(key))
                candidate = _number(value)
                if candidate is not None and (current is None or candidate >= current):
                    self._usage[key] = value
            elif key == "invocation_id":
                current = _text(self._usage.get(key))
                candidate = _text(value)
                if candidate is not None and (
                    current is None or (current.isdigit() and not candidate.isdigit())
                ):
                    self._usage[key] = candidate
            else:
                self._usage[key] = value


def usage_event_payload(event: UsageEvent) -> dict[str, Any]:
    return dict(event.__dict__)


def usage_event_from_payload(payload: Mapping[str, Any]) -> UsageEvent:
    return UsageEvent(
        metrics_version=str(payload.get("metrics_version", METRICS_VERSION)),
        event_id=str(payload["event_id"]),
        created_at_utc=str(payload["created_at_utc"]),
        event_kind=str(payload["event_kind"]),
        role=str(payload["role"]),
        agent=str(payload["agent"]),
        provider=str(payload["provider"]),
        model=str(payload["model"]),
        driver=str(payload["driver"]),
        task_id=_text(payload.get("task_id")),
        run_id=_text(payload.get("run_id")),
        invocation_id=_text(payload.get("invocation_id")),
        duration_ms=_integer(payload.get("duration_ms")),
        input_tokens=_integer(payload.get("input_tokens")),
        output_tokens=_integer(payload.get("output_tokens")),
        total_tokens=_integer(payload.get("total_tokens")),
        context_used_tokens=_integer(payload.get("context_used_tokens")),
        context_size_tokens=_integer(payload.get("context_size_tokens")),
        cost_amount=_number(payload.get("cost_amount")),
        cost_currency=_text(payload.get("cost_currency")),
        cost_source=_text(payload.get("cost_source")),
        quality_score=_number(payload.get("quality_score")),
        outcome=str(payload.get("outcome", "unknown")),
    )


def _extract_usage(payload: Mapping[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = {}

    def visit(value: object) -> None:
        if not isinstance(value, Mapping):
            if isinstance(value, list):
                for item in value:
                    visit(item)
            return
        aliases = {
            "input_tokens": ("input_tokens", "inputTokens", "prompt_tokens"),
            "output_tokens": ("output_tokens", "outputTokens", "completion_tokens"),
            "total_tokens": ("total_tokens", "totalTokens"),
            "context_used_tokens": ("used",),
            "context_size_tokens": ("size",),
            "duration_ms": ("duration_ms", "durationMs", "latency_ms"),
            "cost_amount": ("total_cost_usd",),
            "invocation_id": ("request_id", "session_id", "sessionId", "id"),
            "provider": ("provider",),
            "model": ("model",),
        }
        for target, names in aliases.items():
            for name in names:
                if name in value and value[name] is not None:
                    candidate = value[name]
                    current = found.get(target)
                    if target in {"provider", "model", "invocation_id"}:
                        found[target] = candidate
                    elif _number(candidate) is not None and (
                        _number(current) is None or _number(candidate) >= _number(current)
                    ):
                        found[target] = candidate
        cost = value.get("cost")
        if isinstance(cost, Mapping):
            if _number(cost.get("amount")) is not None:
                found["cost_amount"] = cost["amount"]
                found["cost_currency"] = str(cost.get("currency") or "USD").upper()
        for nested in value.values():
            visit(nested)

    visit(payload)
    if "cost_amount" in found and "cost_currency" not in found:
        found["cost_currency"] = "USD"
    return found


def _is_terminal_adapter_payload(payload: Mapping[str, Any]) -> bool:
    if "method" in payload:
        return False
    result = payload.get("result")
    if isinstance(result, Mapping) and ("stopReason" in result or "stop_reason" in result):
        return True
    if "error" in payload and "id" in payload:
        return True
    terminal_keys = {
        "return_code", "request_id", "patch_sha256", "status", "ok", "is_error",
        "total_cost_usd", "duration_ms", "durationMs", "latency_ms",
    }
    return any(key in payload for key in terminal_keys)


def _payload_failed(payload: Mapping[str, Any]) -> bool:
    if payload.get("ok") is False or payload.get("is_error") is True or "error" in payload:
        return True
    return_code = _integer(payload.get("return_code"))
    if return_code is not None and return_code != 0:
        return True
    return str(payload.get("status", "")).casefold() in {"error", "failed", "rejected", "canceled"}


def _calculated_cost(
    prices: Sequence[ModelPriceConfig],
    provider: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> tuple[float, str] | None:
    if input_tokens is None and output_tokens is None:
        return None
    folded_provider = provider.casefold()
    folded_model = model.casefold()
    price = next(
        (
            item for item in prices
            if item.provider == folded_provider and item.model.casefold() == folded_model
        ),
        None,
    ) or next(
        (item for item in prices if item.provider == folded_provider and item.model == "*"),
        None,
    )
    if price is None:
        return None
    amount = ((input_tokens or 0) * price.input_per_million + (output_tokens or 0) * price.output_per_million) / 1_000_000
    return round(amount, 8), price.currency


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _text(value: object) -> str | None:
    return str(value) if isinstance(value, (str, int)) and str(value) else None
