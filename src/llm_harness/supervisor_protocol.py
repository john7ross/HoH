from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .state import (
    AuditRecord,
    HohStateStore,
    OperatorEvent,
    QueueTask,
    ReviewDecisionRecord,
    RollbackRecord,
    RunRecord,
    default_state_root,
)
from .operations import OperationLedger, ReconciliationItem


@dataclass(frozen=True)
class SupervisorStatusReport:
    repository: Path
    state_root: Path
    queued: int
    ready: int
    waiting: int
    running: int
    done: int
    failed: int
    rolled_back: int
    review_pending: int
    rework_required: int
    escalated: int
    stale: int
    stale_task_ids: tuple[str, ...]
    latest_run: RunRecord | None
    latest_audit: AuditRecord | None
    latest_operator_event: OperatorEvent | None
    review_bundles: int
    pending_reviews: int
    approved_reviews: int
    rejected_reviews: int
    latest_review: ReviewDecisionRecord | None
    rollback_records: int
    latest_rollback: RollbackRecord | None
    reconciliation_pending: int
    reconciliation_items: tuple[ReconciliationItem, ...]

    @property
    def ok(self) -> bool:
        return (
            self.failed == 0
            and self.rolled_back == 0
            and self.stale == 0
            and self.review_pending == 0
            and self.rework_required == 0
            and self.escalated == 0
            and self.pending_reviews == 0
            and self.rejected_reviews == 0
            and self.reconciliation_pending == 0
        )


def build_supervisor_status(
    repository: Path,
    state_root: Path | None = None,
    stale_minutes: float = 60.0,
) -> SupervisorStatusReport:
    root = state_root or default_state_root(repository)
    store = HohStateStore(root)
    tasks = store.list_tasks()
    schedule = store.schedule()
    counts = _task_counts(_latest_tasks(tasks))
    stale_tasks = store.stale_running_tasks(stale_minutes)
    history = store.history()
    audits = store.audit_reports()
    events = store.operator_events()
    bundles = store.review_bundles()
    decisions = store.review_decisions()
    rollbacks = store.rollback_records()
    reconciliation = OperationLedger(repository).reconcile(auto_complete_state=False)
    latest_bundles_by_task = {bundle.work_item_id: bundle for bundle in bundles}
    decisions_by_bundle = {decision.bundle_id: decision for decision in decisions}
    current_decisions = [decisions_by_bundle.get(bundle.bundle_id) for bundle in latest_bundles_by_task.values()]
    return SupervisorStatusReport(
        repository=repository,
        state_root=root,
        queued=counts["queued"],
        ready=len(schedule.ready),
        waiting=len(schedule.waiting),
        running=counts["running"],
        done=counts["done"],
        failed=counts["failed"],
        rolled_back=counts["rolled_back"],
        review_pending=counts["review_pending"],
        rework_required=counts["rework_required"],
        escalated=counts["escalated"],
        stale=len(stale_tasks),
        stale_task_ids=tuple(task.work_item.id for task in stale_tasks),
        latest_run=history[-1] if history else None,
        latest_audit=audits[-1] if audits else None,
        latest_operator_event=events[-1] if events else None,
        review_bundles=len(bundles),
        pending_reviews=sum(decision is None for decision in current_decisions),
        approved_reviews=sum(decision is not None and decision.decision == "approve" for decision in current_decisions),
        rejected_reviews=sum(decision is not None and decision.decision == "reject" for decision in current_decisions),
        latest_review=decisions[-1] if decisions else None,
        rollback_records=len(rollbacks),
        latest_rollback=rollbacks[-1] if rollbacks else None,
        reconciliation_pending=len(reconciliation.blocking),
        reconciliation_items=reconciliation.items,
    )


def _task_counts(tasks: tuple[QueueTask, ...]) -> dict[str, int]:
    counts = {
        status: 0
        for status in (
            "queued",
            "running",
            "review_pending",
            "rework_required",
            "escalated",
            "done",
            "failed",
            "rolled_back",
        )
    }
    for task in tasks:
        counts[task.status] += 1
    return counts


def _latest_tasks(tasks: tuple[QueueTask, ...]) -> tuple[QueueTask, ...]:
    latest: dict[str, QueueTask] = {}
    order: list[str] = []
    for task in tasks:
        task_id = task.work_item.id
        if task_id not in latest:
            order.append(task_id)
        latest[task_id] = task
    return tuple(latest[task_id] for task_id in order)
