from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .config import HarnessConfig
from .critic_adapters import CriticAdapterError, create_critic_adapter
from .durable_io import atomic_write_text
from .review_protocol import (
    ReviewBundleExport,
    ReviewProtocolError,
    import_verifier_decision,
    load_review_bundle,
)
from .state import HohStateStore, ReviewDecisionRecord
from .metrics import UsageContext


@dataclass(frozen=True)
class CriticReviewResult:
    decision: ReviewDecisionRecord
    decision_path: Path
    task_status: str


def run_critic_review(
    repository: Path,
    store: HohStateStore,
    config: HarnessConfig,
    bundle: ReviewBundleExport | None = None,
    bundle_id: str | None = None,
) -> CriticReviewResult:
    if not config.three_head.required:
        raise CriticAdapterError("Automatic Critic requires three_head.mode = 'required'.")
    if not config.critic.automatic:
        raise CriticAdapterError("Critic transport is manual; no automatic adapter is configured.")
    if bundle is None and bundle_id is None:
        raise CriticAdapterError("A review bundle or bundle_id is required.")

    selected_bundle_id = bundle.record.bundle_id if bundle is not None else str(bundle_id)
    payload = bundle.payload if bundle is not None else load_review_bundle(store, selected_bundle_id)
    work_item_id = _pending_work_item_id(store, selected_bundle_id)
    adapter = create_critic_adapter(
        config.critic,
        config.three_head.critic,
        config.verifier_model,
        event_sink=store.journal.adapter_sink(
            config.three_head.critic.identity,
            work_item_id,
            metric_context=UsageContext(
                role="critic",
                agent=config.three_head.critic.identity,
                provider=config.three_head.critic.provider,
                model=config.three_head.critic.model,
                driver=config.critic.driver,
                task_id=work_item_id,
            ),
            prices=config.metrics.prices,
        ),
    )
    try:
        decision_payload = adapter.review(repository.resolve(), payload)
        decision_path = _write_decision_payload(store, decision_payload)
        decision = import_verifier_decision(repository, store, decision_path)
    except (CriticAdapterError, ReviewProtocolError, OSError, ValueError) as exc:
        store.record_review_failure(work_item_id, str(exc))
        if isinstance(exc, CriticAdapterError):
            raise
        raise CriticAdapterError(f"Critic decision could not be imported: {exc}") from exc

    task = next(item for item in store.list_tasks() if item.work_item.id == work_item_id)
    return CriticReviewResult(
        decision=decision,
        decision_path=decision_path,
        task_status=task.status,
    )


def _pending_work_item_id(store: HohStateStore, bundle_id: str) -> str:
    matches = [
        task
        for task in store.list_tasks()
        if task.status == "review_pending" and task.pending_review_bundle_id == bundle_id
    ]
    if len(matches) != 1:
        raise CriticAdapterError(
            f"Expected exactly one review-pending task for bundle {bundle_id}; found {len(matches)}."
        )
    return matches[0].work_item.id


def _write_decision_payload(store: HohStateStore, payload: dict) -> Path:
    directory = store.root / "review-decision-payloads"
    destination = directory / f"{payload['decision_id']}.json"
    atomic_write_text(
        destination,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    return destination
