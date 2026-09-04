from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import wraps
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, TypeVar
from uuid import uuid4

from .coordination import CoordinationConfig, CoordinationError, FileLease, state_lease
from .durable_io import DurableIOError, atomic_append_jsonl, atomic_write_text, read_json, read_jsonl
from .domain import (
    CommandResult,
    HarnessRunResult,
    ModelInvocationEvidence,
    SemanticVerificationReport,
    WorkItem,
)
from .journal import InteractionJournal
from .metrics import UsageLedger


TaskStatus = Literal[
    "queued",
    "running",
    "review_pending",
    "rework_required",
    "escalated",
    "done",
    "failed",
    "rolled_back",
]
ReviewDecision = Literal["approve", "reject", "escalate"]


class StateStoreError(RuntimeError):
    pass


_Result = TypeVar("_Result")


def _state_mutation(action: str) -> Callable[[Callable[..., _Result]], Callable[..., _Result]]:
    def decorate(method: Callable[..., _Result]) -> Callable[..., _Result]:
        @wraps(method)
        def locked(self: "HohStateStore", *args: Any, **kwargs: Any) -> _Result:
            try:
                with self.lease.hold(action):
                    return method(self, *args, **kwargs)
            except (CoordinationError, DurableIOError) as exc:
                raise StateStoreError(str(exc)) from exc

        return locked

    return decorate


@dataclass(frozen=True)
class QueueTask:
    work_item: WorkItem
    status: TaskStatus
    source: str
    added_at_utc: str
    updated_at_utc: str
    attempts: int = 0
    last_commit: str | None = None
    last_error: str | None = None
    pending_review_bundle_id: str | None = None
    last_review_decision_id: str | None = None


@dataclass(frozen=True)
class DependencyBlocker:
    task_id: str
    status: str


@dataclass(frozen=True)
class TaskReadiness:
    task: QueueTask
    ready: bool
    blockers: tuple[DependencyBlocker, ...] = ()


@dataclass(frozen=True)
class QueueSchedule:
    selected: QueueTask | None
    ready: tuple[TaskReadiness, ...]
    waiting: tuple[TaskReadiness, ...]


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    work_item_id: str
    started_at_utc: str
    finished_at_utc: str
    ok: bool
    commit: str | None
    error: str | None
    pre_apply_findings: tuple[str, ...]
    post_apply_findings: tuple[str, ...]
    command_results: tuple[CommandResult, ...]
    semantic_verification: SemanticVerificationReport | None = None


@dataclass(frozen=True)
class RollbackRecord:
    rollback_id: str
    created_at_utc: str
    work_item_id: str
    run_id: str
    target_commit: str
    rollback_commit: str | None
    reason: str
    ok: bool
    changed_files: tuple[str, ...]
    command_results: tuple[CommandResult, ...]
    error: str | None = None


@dataclass(frozen=True)
class OperatorEvent:
    event_id: str
    created_at_utc: str
    command: str
    ok: bool
    message: str
    raw_text: str
    task_id: str | None = None


@dataclass(frozen=True)
class AuditRecord:
    audit_id: str
    created_at_utc: str
    ok: bool
    report_path: Path
    findings_count: int
    command_results_count: int


@dataclass(frozen=True)
class ReviewBundleRecord:
    bundle_id: str
    created_at_utc: str
    run_id: str
    work_item_id: str
    commit: str
    bundle_sha256: str
    bundle_path: Path


@dataclass(frozen=True)
class ReviewFinding:
    code: str
    severity: str
    message: str
    path: str | None = None


@dataclass(frozen=True)
class CorrectionBrief:
    rationale: str
    instructions: tuple[str, ...]
    validation_focus: tuple[str, ...] = ()


@dataclass(frozen=True)
class EscalationRequest:
    reason: str
    question: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class ReviewDecisionRecord:
    decision_id: str
    bundle_id: str
    bundle_sha256: str
    run_id: str
    work_item_id: str
    commit: str
    reviewer_id: str
    reviewer_kind: str
    decision: ReviewDecision
    summary: str
    findings: tuple[ReviewFinding, ...]
    created_at_utc: str
    imported_at_utc: str
    protocol_version: str = "1.0"
    correction_brief: CorrectionBrief | None = None
    escalation: EscalationRequest | None = None


class HohStateStore:
    def __init__(
        self,
        root: Path,
        coordination: CoordinationConfig = CoordinationConfig(),
    ) -> None:
        self.root = root
        self.coordination = coordination
        self.lease: FileLease = state_lease(root, coordination)
        self.queue_path = root / "queue.json"
        self.history_path = root / "history.jsonl"
        self.rollback_records_path = root / "rollback-records.jsonl"
        self.operator_events_path = root / "operator-events.jsonl"
        self.telegram_offset_path = root / "telegram-offset.json"
        self.audit_reports_path = root / "audit-reports.jsonl"
        self.audit_reports_dir = root / "audit-reports"
        self.review_bundles_path = root / "review-bundles.jsonl"
        self.review_bundles_dir = root / "review-bundles"
        self.review_decisions_path = root / "review-decisions.jsonl"
        self.journal = InteractionJournal(root, lease=self.lease, coordination=coordination)
        self.metrics = UsageLedger(root, lease=self.lease, coordination=coordination)

    @contextmanager
    def transaction(self, action: str) -> Iterator[None]:
        try:
            with self.lease.hold(action):
                yield
        except (CoordinationError, DurableIOError) as exc:
            raise StateStoreError(str(exc)) from exc

    @_state_mutation("queue.enqueue")
    def enqueue(self, work_item: WorkItem, source: str = "") -> QueueTask:
        return self.enqueue_many(((work_item, source),))[0]

    @_state_mutation("queue.enqueue_many")
    def enqueue_many(
        self,
        items: tuple[tuple[WorkItem, str], ...],
    ) -> tuple[QueueTask, ...]:
        if not items:
            return ()
        tasks = list(self.list_tasks())
        work_items = tuple(work_item for work_item, _ in items)
        incoming_ids = [work_item.id for work_item in work_items]
        if len(set(incoming_ids)) != len(incoming_ids):
            raise StateStoreError("Enqueue batch must contain unique task ids.")
        active = {"queued", "running", "review_pending", "rework_required", "escalated"}
        for work_item in work_items:
            if any(task.work_item.id == work_item.id and task.status in active for task in tasks):
                raise StateStoreError(f"Task already has an active or unresolved lifecycle: {work_item.id}")
        _validate_enqueue_graph(tasks, work_items)

        now = _utc_now()
        added = tuple(
            QueueTask(
                work_item=work_item,
                status="queued",
                source=source,
                added_at_utc=now,
                updated_at_utc=now,
            )
            for work_item, source in items
        )
        tasks.extend(added)
        self._write_tasks(tasks)
        for task in added:
            self.journal.record(
                "state_transition", "supervisor", "task.enqueued",
                recipient="worker",
                content={
                    "from": None,
                    "to": "queued",
                    "source": task.source,
                    "priority": task.work_item.priority,
                    "depends_on": list(task.work_item.depends_on),
                },
                task_id=task.work_item.id,
            )
        return added

    def list_tasks(self) -> tuple[QueueTask, ...]:
        try:
            data = read_json(self.queue_path, default=[])
        except DurableIOError as exc:
            raise StateStoreError(str(exc)) from exc
        if not isinstance(data, list):
            raise StateStoreError("Queue file must contain a JSON list.")
        return tuple(_task_from_json(item) for item in data)

    def next_queued(self) -> QueueTask | None:
        return self.schedule().selected

    def schedule_batch(self, limit: int) -> tuple[QueueTask, ...]:
        if limit < 1:
            raise StateStoreError("Scheduler batch limit must be at least 1.")
        selected: list[QueueTask] = []
        for readiness in self.schedule().ready:
            candidate = readiness.task
            if any(
                _resource_scopes_conflict(
                    candidate.work_item.allowed_paths,
                    current.work_item.allowed_paths,
                )
                for current in selected
            ):
                continue
            selected.append(candidate)
            if len(selected) == limit:
                break
        return tuple(selected)

    def schedule(self) -> QueueSchedule:
        tasks = self.list_tasks()
        latest = _latest_tasks_by_id(tasks)
        indexed = [
            (index, task)
            for index, task in enumerate(tasks)
            if task.status == "queued"
        ]
        readiness = [
            TaskReadiness(
                task=task,
                ready=not (blockers := _dependency_blockers(task, latest)),
                blockers=blockers,
            )
            for _, task in indexed
        ]
        ready_by_id = {
            item.task.work_item.id: item
            for item in readiness
            if item.ready
        }
        ready = tuple(
            ready_by_id[task.work_item.id]
            for _, task in sorted(
                (
                    (index, task)
                    for index, task in indexed
                    if task.work_item.id in ready_by_id
                ),
                key=lambda item: (-item[1].work_item.priority, item[0]),
            )
        )
        waiting = tuple(item for item in readiness if not item.ready)
        return QueueSchedule(
            selected=ready[0].task if ready else None,
            ready=ready,
            waiting=waiting,
        )

    def stale_running_tasks(
        self,
        max_age_minutes: float,
        now: datetime | None = None,
    ) -> tuple[QueueTask, ...]:
        if max_age_minutes <= 0:
            return ()
        current = now or datetime.now(UTC)
        cutoff = current.astimezone(UTC) - timedelta(minutes=max_age_minutes)
        stale: list[QueueTask] = []
        for task in self.list_tasks():
            if task.status != "running":
                continue
            if _parse_utc_timestamp(task.updated_at_utc) <= cutoff:
                stale.append(task)
        return tuple(stale)

    @_state_mutation("queue.claim")
    def claim_next(self, max_attempts: int | None = None) -> QueueTask | None:
        selected = self.schedule().selected
        if selected is None:
            return None
        if max_attempts is not None and selected.attempts >= max_attempts:
            escalated = self.escalate_attempt_limit(selected.work_item.id, max_attempts)
            raise StateStoreError(escalated.last_error or "Worker attempt limit reached.")
        return self.mark_running(selected.work_item.id)

    @_state_mutation("queue.claim_batch")
    def claim_batch(self, limit: int) -> tuple[QueueTask, ...]:
        return tuple(self.mark_running(task.work_item.id) for task in self.schedule_batch(limit))

    @_state_mutation("queue.mark_running")
    def mark_running(self, work_item_id: str) -> QueueTask:
        task = self._get_task(work_item_id)
        if task.status != "queued":
            raise StateStoreError(f"Only queued tasks can start running: {work_item_id}")
        readiness = next(
            (item for item in self.schedule().ready if item.task.work_item.id == work_item_id),
            None,
        )
        if readiness is None:
            waiting = next(
                item for item in self.schedule().waiting if item.task.work_item.id == work_item_id
            )
            details = ", ".join(
                f"{blocker.task_id}={blocker.status}"
                for blocker in waiting.blockers
            )
            raise StateStoreError(f"Task dependencies are not ready: {work_item_id} ({details}).")
        updated = self._update_task(work_item_id, status="running", attempts_delta=1, last_error=None)
        self._transition_event(updated, task.status, "running", "task.started")
        return updated

    @_state_mutation("queue.requeue_failed")
    def requeue_failed(self, work_item_id: str) -> QueueTask:
        task = self._get_task(work_item_id)
        if task.status != "failed":
            raise StateStoreError(f"Only failed tasks can be retried: {work_item_id}")
        updated = self._update_task(work_item_id, status="queued", last_commit=task.last_commit, last_error=None)
        self._transition_event(updated, task.status, "queued", "task.retry_confirmed")
        return updated

    @_state_mutation("queue.recover_running")
    def recover_running(self, work_item_id: str, reason: str) -> QueueTask:
        task = self._get_task(work_item_id)
        if task.status != "running":
            raise StateStoreError(f"Only running tasks can be recovered: {work_item_id}")
        updated = self._update_task(work_item_id, status="queued", last_commit=task.last_commit, last_error=reason)
        self._transition_event(updated, task.status, "queued", "task.recovered", {"reason": reason})
        return updated

    @_state_mutation("queue.record_result")
    def record_result(
        self,
        work_item_id: str,
        started_at_utc: str,
        result: HarnessRunResult,
        error: str | None = None,
        review_required: bool = False,
        run_id: str | None = None,
    ) -> RunRecord:
        finished_at = _utc_now()
        status: TaskStatus = "review_pending" if result.ok and review_required else ("done" if result.ok else "failed")
        selected_run_id = run_id or str(uuid4())
        existing = next((item for item in self.history() if item.run_id == selected_run_id), None)
        if existing is not None:
            if (
                existing.work_item_id == work_item_id
                and existing.commit == result.commit
                and existing.ok == result.ok
            ):
                return existing
            raise StateStoreError(f"Run id already exists with different evidence: {selected_run_id}")
        current = self._get_task(work_item_id)
        expected_error = error or _result_error(result)
        if (
            current.status == status
            and current.last_commit == result.commit
            and current.last_error == expected_error
        ):
            task = current
        else:
            if current.status != "running":
                raise StateStoreError(
                    f"Only a running task can record a new result: {work_item_id}={current.status}"
                )
            task = self._update_task(
                work_item_id,
                status=status,
                last_commit=result.commit,
                last_error=expected_error,
            )
        record = RunRecord(
            run_id=selected_run_id,
            work_item_id=task.work_item.id,
            started_at_utc=started_at_utc,
            finished_at_utc=finished_at,
            ok=result.ok,
            commit=result.commit,
            error=task.last_error,
            pre_apply_findings=result.pre_apply.findings,
            post_apply_findings=result.post_apply.findings,
            command_results=result.command_results,
            semantic_verification=result.semantic_verification,
        )
        self._append_history(record)
        if current.status == "running":
            self._transition_event(task, "running", status, "task.run_recorded", {"ok": result.ok}, run_id=record.run_id)
        return record

    @_state_mutation("review.attach_bundle")
    def attach_review_bundle(self, work_item_id: str, bundle_id: str) -> QueueTask:
        task = self._get_task(work_item_id)
        if task.status != "review_pending":
            raise StateStoreError(f"Only review-pending tasks can receive a review bundle: {work_item_id}")
        updated = self._replace_task(
            task,
            pending_review_bundle_id=bundle_id,
            last_error=None,
        )
        self.journal.record(
            "artifact", "supervisor", "review.bundle_attached", recipient="critic",
            content={"bundle_id": bundle_id}, task_id=work_item_id,
        )
        return updated

    @_state_mutation("review.fail_setup")
    def fail_review_setup(self, work_item_id: str, error: str) -> QueueTask:
        task = self._get_task(work_item_id)
        if task.status != "review_pending":
            raise StateStoreError(f"Task is not waiting for review setup: {work_item_id}")
        updated = self._replace_task(task, status="failed", last_error=error)
        self._transition_event(updated, task.status, "failed", "review.setup_failed", {"error": error})
        return updated

    @_state_mutation("review.record_failure")
    def record_review_failure(self, work_item_id: str, error: str) -> QueueTask:
        task = self._get_task(work_item_id)
        if task.status != "review_pending" or not task.pending_review_bundle_id:
            raise StateStoreError(f"Task is not waiting for critic review: {work_item_id}")
        updated = self._replace_task(task, last_error=error)
        self.journal.record(
            "tool_result",
            "critic",
            "critic.review_failed",
            recipient="supervisor",
            content={"error": error, "bundle_id": task.pending_review_bundle_id},
            task_id=work_item_id,
            correlation_id=task.pending_review_bundle_id,
        )
        return updated

    @_state_mutation("review.resolve")
    def resolve_review(
        self,
        work_item_id: str,
        bundle_id: str,
        decision_id: str,
        decision: str,
        summary: str,
    ) -> QueueTask:
        task = self._get_task(work_item_id)
        if task.status != "review_pending":
            raise StateStoreError(f"Task is not waiting for critic review: {work_item_id}")
        if task.pending_review_bundle_id != bundle_id:
            raise StateStoreError(f"Critic decision does not match the pending bundle for task: {work_item_id}")
        if decision == "approve":
            status: TaskStatus = "done"
            error = None
        elif decision == "reject":
            status = "rework_required"
            error = summary
        elif decision == "escalate":
            status = "escalated"
            error = summary
        else:
            raise StateStoreError(f"Unsupported critic decision: {decision}")
        updated = self._replace_task(
            task,
            status=status,
            pending_review_bundle_id=None,
            last_review_decision_id=decision_id,
            last_error=error,
        )
        self._transition_event(
            updated, task.status, status, "critic.decision_applied",
            {"decision": decision, "decision_id": decision_id, "summary": summary},
        )
        return updated

    @_state_mutation("review.confirm_rework")
    def confirm_rework(
        self,
        work_item_id: str,
        decision_id: str,
        instructions: tuple[str, ...],
        max_attempts: int,
    ) -> QueueTask:
        task = self._get_task(work_item_id)
        if task.status != "rework_required":
            raise StateStoreError(f"Task is not awaiting supervisor-confirmed rework: {work_item_id}")
        if task.last_review_decision_id != decision_id:
            raise StateStoreError(f"Correction brief does not match the latest critic decision: {decision_id}")
        if not instructions or any(not instruction.strip() for instruction in instructions):
            raise StateStoreError("Supervisor-confirmed correction instructions must be non-empty.")
        if max_attempts < 1:
            raise StateStoreError("Three-head max_attempts must be at least 1.")
        if task.attempts >= max_attempts:
            updated = self._replace_task(
                task,
                status="escalated",
                last_error=f"Rework attempt limit reached ({task.attempts}/{max_attempts}).",
            )
            self._transition_event(updated, task.status, "escalated", "rework.attempt_limit")
            return updated
        work_item = replace(
            task.work_item,
            correction_instructions=instructions,
            correction_decision_id=decision_id,
        )
        updated = self._replace_task(
            task,
            status="queued",
            work_item=work_item,
            pending_review_bundle_id=None,
            last_error=None,
        )
        self._transition_event(
            updated, task.status, "queued", "rework.supervisor_confirmed",
            {"decision_id": decision_id, "instructions": instructions},
        )
        return updated

    @_state_mutation("queue.escalate_attempt_limit")
    def escalate_attempt_limit(self, work_item_id: str, max_attempts: int) -> QueueTask:
        task = self._get_task(work_item_id)
        if task.status != "queued" or task.attempts < max_attempts:
            return task
        updated = self._replace_task(
            task,
            status="escalated",
            last_error=f"Worker attempt limit reached ({task.attempts}/{max_attempts}).",
        )
        self._transition_event(updated, task.status, "escalated", "worker.attempt_limit")
        return updated

    @_state_mutation("queue.resolve_escalation")
    def resolve_escalation(self, work_item_id: str, resolution: str, reason: str) -> QueueTask:
        """Operator exit from `escalated`: back to the queue with a fresh attempt budget, or closed as failed."""
        task = self._get_task(work_item_id)
        if task.status != "escalated":
            raise StateStoreError(f"Only escalated tasks can be resolved: {work_item_id}")
        if not reason.strip():
            raise StateStoreError("Escalation resolution requires an operator reason.")
        if resolution == "requeue":
            status: TaskStatus = "queued"
            error = None
            attempts = 0
        elif resolution == "fail":
            status = "failed"
            error = reason
            attempts = task.attempts
        else:
            raise StateStoreError(f"Unsupported escalation resolution: {resolution}")
        updated = self._replace_task(
            task,
            status=status,
            attempts=attempts,
            pending_review_bundle_id=None,
            last_error=error,
        )
        self._transition_event(
            updated, task.status, status, "escalation.resolved",
            {"resolution": resolution, "reason": reason},
        )
        return updated

    @_state_mutation("queue.record_failure")
    def record_failure(self, work_item_id: str, started_at_utc: str, error: str) -> RunRecord:
        task = self._update_task(work_item_id, status="failed", last_commit=None, last_error=error)
        now = _utc_now()
        record = RunRecord(
            run_id=str(uuid4()),
            work_item_id=task.work_item.id,
            started_at_utc=started_at_utc,
            finished_at_utc=now,
            ok=False,
            commit=None,
            error=error,
            pre_apply_findings=(),
            post_apply_findings=(),
            command_results=(),
        )
        self._append_history(record)
        self._transition_event(task, "running", "failed", "task.failed", {"error": error}, run_id=record.run_id)
        return record

    def history(self) -> tuple[RunRecord, ...]:
        try:
            return tuple(_run_from_json(item) for item in read_jsonl(self.history_path))
        except DurableIOError as exc:
            raise StateStoreError(str(exc)) from exc

    def latest_task(self, work_item_id: str) -> QueueTask:
        return self._get_task(work_item_id)

    def downstream_tasks(self, work_item_id: str) -> tuple[QueueTask, ...]:
        latest = _latest_tasks_by_id(self.list_tasks())
        result: list[QueueTask] = []
        pending = [work_item_id]
        seen = {work_item_id}
        while pending:
            dependency_id = pending.pop(0)
            for task_id, task in latest.items():
                if task_id in seen or dependency_id not in task.work_item.depends_on:
                    continue
                seen.add(task_id)
                result.append(task)
                pending.append(task_id)
        return tuple(result)

    @_state_mutation("rollback.mark_rolled_back")
    def mark_rolled_back(
        self,
        work_item_id: str,
        target_commit: str,
        rollback_commit: str,
    ) -> QueueTask:
        task = self._get_task(work_item_id)
        if (
            task.status == "rolled_back"
            and task.last_error == f"Rolled back by {rollback_commit}; target commit was {target_commit}."
        ):
            return task
        if task.status != "done":
            raise StateStoreError(f"Only done tasks can be marked rolled back: {work_item_id}")
        updated = self._replace_task(
            task,
            status="rolled_back",
            last_error=f"Rolled back by {rollback_commit}; target commit was {target_commit}.",
        )
        self._transition_event(
            updated,
            task.status,
            "rolled_back",
            "rollback.applied",
            {"target_commit": target_commit, "rollback_commit": rollback_commit},
        )
        return updated

    @_state_mutation("rollback.complete")
    def complete_rollback(self, record: RollbackRecord) -> RollbackRecord:
        if not record.ok or record.rollback_commit is None:
            raise StateStoreError("Only a successful rollback with a commit can complete state.")
        self.mark_rolled_back(
            record.work_item_id,
            record.target_commit,
            record.rollback_commit,
        )
        return self.record_rollback(record)

    @_state_mutation("rollback.record")
    def record_rollback(self, record: RollbackRecord) -> RollbackRecord:
        existing = next(
            (item for item in self.rollback_records() if item.rollback_id == record.rollback_id),
            None,
        )
        if existing is not None:
            if existing == record:
                return existing
            raise StateStoreError(f"Rollback id already exists with different evidence: {record.rollback_id}")
        atomic_append_jsonl(self.rollback_records_path, _rollback_record_to_json(record))
        self.journal.record(
            "decision",
            "supervisor",
            "rollback.recorded",
            content=_rollback_record_to_json(record),
            task_id=record.work_item_id,
            run_id=record.run_id,
            correlation_id=record.rollback_id,
        )
        return record

    def rollback_records(self) -> tuple[RollbackRecord, ...]:
        try:
            return tuple(_rollback_record_from_json(item) for item in read_jsonl(self.rollback_records_path))
        except DurableIOError as exc:
            raise StateStoreError(str(exc)) from exc

    @_state_mutation("operator.record")
    def record_operator_event(
        self,
        command: str,
        ok: bool,
        message: str,
        raw_text: str,
        task_id: str | None = None,
    ) -> OperatorEvent:
        event = OperatorEvent(
            event_id=str(uuid4()),
            created_at_utc=_utc_now(),
            command=command,
            ok=ok,
            message=message,
            raw_text=raw_text,
            task_id=task_id,
        )
        atomic_append_jsonl(self.operator_events_path, _operator_event_to_json(event))
        self.journal.record(
            "interaction", "customer", "operator.command", recipient="supervisor",
            content={"raw_text": raw_text, "message": message, "ok": ok}, task_id=task_id,
        )
        return event

    def operator_events(self) -> tuple[OperatorEvent, ...]:
        try:
            return tuple(_operator_event_from_json(item) for item in read_jsonl(self.operator_events_path))
        except DurableIOError as exc:
            raise StateStoreError(str(exc)) from exc

    @_state_mutation("audit.record")
    def record_audit_report(
        self,
        markdown: str,
        ok: bool,
        findings_count: int,
        command_results_count: int,
    ) -> AuditRecord:
        audit_id = str(uuid4())
        created_at = _utc_now()
        safe_timestamp = created_at.replace(":", "").replace("+", "Z")
        report_path = self.audit_reports_dir / f"{safe_timestamp}-{audit_id}.md"
        atomic_write_text(report_path, markdown)
        record = AuditRecord(
            audit_id=audit_id,
            created_at_utc=created_at,
            ok=ok,
            report_path=report_path,
            findings_count=findings_count,
            command_results_count=command_results_count,
        )
        atomic_append_jsonl(self.audit_reports_path, _audit_record_to_json(record, self.root))
        self.journal.record(
            "artifact", "supervisor", "audit.recorded",
            content={"audit_id": audit_id, "ok": ok, "report_path": str(report_path)},
        )
        return record

    def audit_reports(self) -> tuple[AuditRecord, ...]:
        try:
            return tuple(
                _audit_record_from_json(item, self.root)
                for item in read_jsonl(self.audit_reports_path)
            )
        except DurableIOError as exc:
            raise StateStoreError(str(exc)) from exc

    @_state_mutation("review.record_bundle")
    def record_review_bundle(self, record: ReviewBundleRecord) -> ReviewBundleRecord:
        existing = next((item for item in self.review_bundles() if item.bundle_id == record.bundle_id), None)
        if existing is not None:
            if existing == record:
                return existing
            raise StateStoreError(f"Review bundle id already exists with different evidence: {record.bundle_id}")
        atomic_append_jsonl(self.review_bundles_path, _review_bundle_record_to_json(record, self.root))
        self.journal.record(
            "interaction", "supervisor", "critic.review_bundle", recipient="critic",
            content={"bundle_id": record.bundle_id, "commit": record.commit, "sha256": record.bundle_sha256},
            task_id=record.work_item_id, run_id=record.run_id,
        )
        return record

    def review_bundles(self) -> tuple[ReviewBundleRecord, ...]:
        try:
            return tuple(
                _review_bundle_record_from_json(item, self.root)
                for item in read_jsonl(self.review_bundles_path)
            )
        except DurableIOError as exc:
            raise StateStoreError(str(exc)) from exc

    @_state_mutation("review.record_decision")
    def record_review_decision(self, record: ReviewDecisionRecord) -> ReviewDecisionRecord:
        for existing in self.review_decisions():
            if existing.decision_id == record.decision_id:
                if existing == record:
                    return existing
                raise StateStoreError(f"Review decision id already exists with different evidence: {record.decision_id}")
            if existing.bundle_id == record.bundle_id:
                raise StateStoreError(f"Review bundle already has an immutable decision: {record.bundle_id}")
        atomic_append_jsonl(self.review_decisions_path, _review_decision_record_to_json(record))
        self.journal.record(
            "decision", "critic", "critic.decision", recipient="supervisor",
            content=_review_decision_record_to_json(record), task_id=record.work_item_id, run_id=record.run_id,
        )
        return record

    def review_decisions(self) -> tuple[ReviewDecisionRecord, ...]:
        try:
            return tuple(
                _review_decision_record_from_json(item)
                for item in read_jsonl(self.review_decisions_path)
            )
        except DurableIOError as exc:
            raise StateStoreError(str(exc)) from exc

    def telegram_update_offset(self) -> int | None:
        try:
            data = read_json(self.telegram_offset_path, default=None)
        except DurableIOError as exc:
            raise StateStoreError(str(exc)) from exc
        if data is None:
            return None
        offset = data.get("offset") if isinstance(data, dict) else None
        if offset is None:
            return None
        return int(offset)

    @_state_mutation("telegram.advance_offset")
    def set_telegram_update_offset(self, offset: int) -> None:
        current = self.telegram_update_offset()
        if current is not None and offset < current:
            return
        atomic_write_text(
            self.telegram_offset_path,
            json.dumps({"offset": offset}, indent=2, ensure_ascii=False) + "\n",
        )

    def _update_task(
        self,
        work_item_id: str,
        status: TaskStatus,
        attempts_delta: int = 0,
        last_commit: str | None = None,
        last_error: str | None = None,
    ) -> QueueTask:
        tasks = list(self.list_tasks())
        for index in range(len(tasks) - 1, -1, -1):
            task = tasks[index]
            if task.work_item.id != work_item_id:
                continue
            updated = QueueTask(
                work_item=task.work_item,
                status=status,
                source=task.source,
                added_at_utc=task.added_at_utc,
                updated_at_utc=_utc_now(),
                attempts=task.attempts + attempts_delta,
                last_commit=last_commit,
                last_error=last_error,
                pending_review_bundle_id=task.pending_review_bundle_id,
                last_review_decision_id=task.last_review_decision_id,
            )
            tasks[index] = updated
            self._write_tasks(tasks)
            return updated
        raise StateStoreError(f"Unknown queued task: {work_item_id}")

    def _replace_task(self, current: QueueTask, **changes: Any) -> QueueTask:
        tasks = list(self.list_tasks())
        for index, task in enumerate(tasks):
            if task is current or task == current:
                updated = replace(task, updated_at_utc=_utc_now(), **changes)
                tasks[index] = updated
                self._write_tasks(tasks)
                return updated
        raise StateStoreError(f"Queued task changed before transition: {current.work_item.id}")

    def _get_task(self, work_item_id: str) -> QueueTask:
        for task in reversed(self.list_tasks()):
            if task.work_item.id == work_item_id:
                return task
        raise StateStoreError(f"Unknown queued task: {work_item_id}")

    def _write_tasks(self, tasks: list[QueueTask]) -> None:
        payload = [_task_to_json(task) for task in tasks]
        atomic_write_text(self.queue_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    def _append_history(self, record: RunRecord) -> None:
        atomic_append_jsonl(self.history_path, _run_to_json(record))

    def _transition_event(
        self,
        task: QueueTask,
        previous: str,
        current: str,
        action: str,
        details: Any = None,
        run_id: str | None = None,
    ) -> None:
        self.journal.record(
            "state_transition", "supervisor", action,
            content={"from": previous, "to": current, "details": details},
            task_id=task.work_item.id, run_id=run_id,
        )


def default_state_root(repository: Path) -> Path:
    resolved = repository.resolve()
    digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:8]
    return resolved.parent / ".hoh-state" / f"{resolved.name}-{digest}"


def _task_to_json(task: QueueTask) -> dict[str, Any]:
    return {
        "work_item": _work_item_to_json(task.work_item),
        "status": task.status,
        "source": task.source,
        "added_at_utc": task.added_at_utc,
        "updated_at_utc": task.updated_at_utc,
        "attempts": task.attempts,
        "last_commit": task.last_commit,
        "last_error": task.last_error,
        "pending_review_bundle_id": task.pending_review_bundle_id,
        "last_review_decision_id": task.last_review_decision_id,
    }


def _task_from_json(data: Any) -> QueueTask:
    if not isinstance(data, dict):
        raise StateStoreError("Queue task entry must be an object.")
    return QueueTask(
        work_item=_work_item_from_json(data.get("work_item")),
        status=_status_from_json(data.get("status")),
        source=str(data.get("source") or ""),
        added_at_utc=str(data.get("added_at_utc") or ""),
        updated_at_utc=str(data.get("updated_at_utc") or ""),
        attempts=int(data.get("attempts") or 0),
        last_commit=_optional_string(data.get("last_commit")),
        last_error=_optional_string(data.get("last_error")),
        pending_review_bundle_id=_optional_string(data.get("pending_review_bundle_id")),
        last_review_decision_id=_optional_string(data.get("last_review_decision_id")),
    )


def _work_item_to_json(work_item: WorkItem) -> dict[str, Any]:
    return {
        "id": work_item.id,
        "title": work_item.title,
        "objective": work_item.objective,
        "acceptance_criteria": list(work_item.acceptance_criteria),
        "verification_commands": list(work_item.verification_commands),
        "allowed_paths": list(work_item.allowed_paths),
        "non_goals": list(work_item.non_goals),
        "depends_on": list(work_item.depends_on),
        "priority": work_item.priority,
        "correction_instructions": list(work_item.correction_instructions),
        "correction_decision_id": work_item.correction_decision_id,
    }


def _work_item_from_json(data: Any) -> WorkItem:
    if not isinstance(data, dict):
        raise StateStoreError("Queue work_item must be an object.")
    return WorkItem(
        id=str(data["id"]),
        title=str(data["title"]),
        objective=str(data["objective"]),
        acceptance_criteria=tuple(str(item) for item in data["acceptance_criteria"]),
        verification_commands=tuple(str(item) for item in data["verification_commands"]),
        allowed_paths=tuple(str(item) for item in data.get("allowed_paths", ())),
        non_goals=tuple(str(item) for item in data.get("non_goals", ())),
        depends_on=tuple(str(item) for item in data.get("depends_on", ())),
        priority=_priority_from_json(data.get("priority", 0)),
        correction_instructions=tuple(str(item) for item in data.get("correction_instructions", ())),
        correction_decision_id=_optional_string(data.get("correction_decision_id")),
    )


def _priority_from_json(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StateStoreError("Queue work_item priority must be an integer.")
    return value


def _latest_tasks_by_id(tasks: tuple[QueueTask, ...]) -> dict[str, QueueTask]:
    latest: dict[str, QueueTask] = {}
    for task in tasks:
        latest[task.work_item.id] = task
    return latest


def _dependency_blockers(
    task: QueueTask,
    latest: dict[str, QueueTask],
) -> tuple[DependencyBlocker, ...]:
    blockers: list[DependencyBlocker] = []
    for dependency_id in task.work_item.depends_on:
        dependency = latest.get(dependency_id)
        status = dependency.status if dependency is not None else "missing"
        if status != "done":
            blockers.append(DependencyBlocker(dependency_id, status))
    return tuple(blockers)


def _resource_scopes_conflict(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if not left or not right:
        return True
    normalized_left = tuple(_normalize_resource_path(path) for path in left)
    normalized_right = tuple(_normalize_resource_path(path) for path in right)
    for first in normalized_left:
        for second in normalized_right:
            if first == "*" or second == "*":
                return True
            if first == second or first.startswith(second + "/") or second.startswith(first + "/"):
                return True
    return False


def _normalize_resource_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/").strip("/")
    return normalized.casefold() or "*"


def _validate_enqueue_graph(
    existing: list[QueueTask],
    incoming: tuple[WorkItem, ...],
) -> None:
    known_ids = {task.work_item.id for task in existing}
    known_ids.update(task.id for task in incoming)
    for work_item in incoming:
        if len(set(work_item.depends_on)) != len(work_item.depends_on):
            raise StateStoreError(f"Task {work_item.id} contains duplicate dependencies.")
        unknown = [
            dependency_id
            for dependency_id in work_item.depends_on
            if dependency_id not in known_ids
        ]
        if unknown:
            raise StateStoreError(
                f"Task {work_item.id} has unknown dependencies: {', '.join(unknown)}."
            )

    latest = _latest_tasks_by_id(tuple(existing))
    for work_item in incoming:
        latest[work_item.id] = QueueTask(
            work_item=work_item,
            status="queued",
            source="",
            added_at_utc="",
            updated_at_utc="",
        )
    unresolved = {
        task_id: task
        for task_id, task in latest.items()
        if task.status != "done"
    }
    graph = {
        task_id: tuple(
            dependency_id
            for dependency_id in task.work_item.depends_on
            if dependency_id in unresolved
        )
        for task_id, task in unresolved.items()
    }
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visited:
            return
        if task_id in visiting:
            cycle = visiting[visiting.index(task_id):] + [task_id]
            raise StateStoreError(f"Queue task dependency cycle: {' -> '.join(cycle)}.")
        visiting.append(task_id)
        for dependency_id in graph.get(task_id, ()):
            visit(dependency_id)
        visiting.pop()
        visited.add(task_id)

    for task_id in graph:
        visit(task_id)


def _run_to_json(record: RunRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "work_item_id": record.work_item_id,
        "started_at_utc": record.started_at_utc,
        "finished_at_utc": record.finished_at_utc,
        "ok": record.ok,
        "commit": record.commit,
        "error": record.error,
        "pre_apply_findings": list(record.pre_apply_findings),
        "post_apply_findings": list(record.post_apply_findings),
        "command_results": [
            {
                "command": result.command,
                "return_code": result.return_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
            for result in record.command_results
        ],
        "semantic_verification": _semantic_verification_to_json(record.semantic_verification),
    }


def _rollback_record_to_json(record: RollbackRecord) -> dict[str, Any]:
    return {
        "rollback_id": record.rollback_id,
        "created_at_utc": record.created_at_utc,
        "work_item_id": record.work_item_id,
        "run_id": record.run_id,
        "target_commit": record.target_commit,
        "rollback_commit": record.rollback_commit,
        "reason": record.reason,
        "ok": record.ok,
        "changed_files": list(record.changed_files),
        "command_results": [
            {
                "command": result.command,
                "return_code": result.return_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
            for result in record.command_results
        ],
        "error": record.error,
    }


def _operator_event_to_json(event: OperatorEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "created_at_utc": event.created_at_utc,
        "command": event.command,
        "ok": event.ok,
        "message": event.message,
        "raw_text": event.raw_text,
        "task_id": event.task_id,
    }


def _audit_record_to_json(record: AuditRecord, root: Path) -> dict[str, Any]:
    return {
        "audit_id": record.audit_id,
        "created_at_utc": record.created_at_utc,
        "ok": record.ok,
        "report_path": _relative_or_absolute(root, record.report_path),
        "findings_count": record.findings_count,
        "command_results_count": record.command_results_count,
    }


def _review_bundle_record_to_json(record: ReviewBundleRecord, root: Path) -> dict[str, Any]:
    return {
        "bundle_id": record.bundle_id,
        "created_at_utc": record.created_at_utc,
        "run_id": record.run_id,
        "work_item_id": record.work_item_id,
        "commit": record.commit,
        "bundle_sha256": record.bundle_sha256,
        "bundle_path": _relative_or_absolute(root, record.bundle_path),
    }


def _review_decision_record_to_json(record: ReviewDecisionRecord) -> dict[str, Any]:
    return {
        "decision_id": record.decision_id,
        "bundle_id": record.bundle_id,
        "bundle_sha256": record.bundle_sha256,
        "run_id": record.run_id,
        "work_item_id": record.work_item_id,
        "commit": record.commit,
        "reviewer_id": record.reviewer_id,
        "reviewer_kind": record.reviewer_kind,
        "decision": record.decision,
        "summary": record.summary,
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity,
                "message": finding.message,
                "path": finding.path,
            }
            for finding in record.findings
        ],
        "created_at_utc": record.created_at_utc,
        "imported_at_utc": record.imported_at_utc,
        "protocol_version": record.protocol_version,
        "correction_brief": (
            {
                "rationale": record.correction_brief.rationale,
                "instructions": list(record.correction_brief.instructions),
                "validation_focus": list(record.correction_brief.validation_focus),
            }
            if record.correction_brief
            else None
        ),
        "escalation": (
            {
                "reason": record.escalation.reason,
                "question": record.escalation.question,
                "options": list(record.escalation.options),
            }
            if record.escalation
            else None
        ),
    }


def _run_from_json(data: Any) -> RunRecord:
    if not isinstance(data, dict):
        raise StateStoreError("Run history entry must be an object.")
    return RunRecord(
        run_id=str(data["run_id"]),
        work_item_id=str(data["work_item_id"]),
        started_at_utc=str(data["started_at_utc"]),
        finished_at_utc=str(data["finished_at_utc"]),
        ok=bool(data["ok"]),
        commit=_optional_string(data.get("commit")),
        error=_optional_string(data.get("error")),
        pre_apply_findings=tuple(str(item) for item in data.get("pre_apply_findings", ())),
        post_apply_findings=tuple(str(item) for item in data.get("post_apply_findings", ())),
        command_results=tuple(_command_result_from_json(item) for item in data.get("command_results", ())),
        semantic_verification=_semantic_verification_from_json(data.get("semantic_verification")),
    )


def _semantic_verification_to_json(
    report: SemanticVerificationReport | None,
) -> dict[str, Any] | None:
    if report is None:
        return None
    evidence = report.evidence
    return {
        "decision": report.decision,
        "summary": report.summary,
        "findings": list(report.findings),
        "evidence": (
            {
                "provider": evidence.provider,
                "model": evidence.model,
                "endpoint": evidence.endpoint,
                "request_id": evidence.request_id,
                "attempts": evidence.attempts,
                "latency_ms": evidence.latency_ms,
                "input_tokens": evidence.input_tokens,
                "output_tokens": evidence.output_tokens,
                "total_tokens": evidence.total_tokens,
                "request_sha256": evidence.request_sha256,
                "response_sha256": evidence.response_sha256,
            }
            if evidence is not None
            else None
        ),
    }


def _semantic_verification_from_json(data: Any) -> SemanticVerificationReport | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise StateStoreError("Semantic verification history must be an object.")
    evidence_data = data.get("evidence")
    evidence = None
    if evidence_data is not None:
        if not isinstance(evidence_data, dict):
            raise StateStoreError("Semantic verification evidence must be an object.")
        evidence = ModelInvocationEvidence(
            provider=str(evidence_data["provider"]),
            model=str(evidence_data["model"]),
            endpoint=str(evidence_data["endpoint"]),
            request_id=_optional_string(evidence_data.get("request_id")),
            attempts=int(evidence_data["attempts"]),
            latency_ms=int(evidence_data["latency_ms"]),
            input_tokens=_optional_int(evidence_data.get("input_tokens")),
            output_tokens=_optional_int(evidence_data.get("output_tokens")),
            total_tokens=_optional_int(evidence_data.get("total_tokens")),
            request_sha256=str(evidence_data.get("request_sha256", "")),
            response_sha256=str(evidence_data.get("response_sha256", "")),
        )
    return SemanticVerificationReport(
        decision=str(data["decision"]),
        summary=str(data["summary"]),
        findings=tuple(str(item) for item in data.get("findings", ())),
        evidence=evidence,
    )


def _rollback_record_from_json(data: Any) -> RollbackRecord:
    if not isinstance(data, dict):
        raise StateStoreError("Rollback record entry must be an object.")
    return RollbackRecord(
        rollback_id=str(data["rollback_id"]),
        created_at_utc=str(data["created_at_utc"]),
        work_item_id=str(data["work_item_id"]),
        run_id=str(data["run_id"]),
        target_commit=str(data["target_commit"]),
        rollback_commit=_optional_string(data.get("rollback_commit")),
        reason=str(data["reason"]),
        ok=bool(data["ok"]),
        changed_files=tuple(str(item) for item in data.get("changed_files", ())),
        command_results=tuple(_command_result_from_json(item) for item in data.get("command_results", ())),
        error=_optional_string(data.get("error")),
    )


def _audit_record_from_json(data: Any, root: Path) -> AuditRecord:
    if not isinstance(data, dict):
        raise StateStoreError("Audit report entry must be an object.")
    report_path = Path(str(data["report_path"]))
    if not report_path.is_absolute():
        report_path = root / report_path
    return AuditRecord(
        audit_id=str(data["audit_id"]),
        created_at_utc=str(data["created_at_utc"]),
        ok=bool(data["ok"]),
        report_path=report_path,
        findings_count=int(data["findings_count"]),
        command_results_count=int(data["command_results_count"]),
    )


def _review_bundle_record_from_json(data: Any, root: Path) -> ReviewBundleRecord:
    if not isinstance(data, dict):
        raise StateStoreError("Review bundle record must be an object.")
    bundle_path = Path(str(data["bundle_path"]))
    if not bundle_path.is_absolute():
        bundle_path = root / bundle_path
    return ReviewBundleRecord(
        bundle_id=str(data["bundle_id"]),
        created_at_utc=str(data["created_at_utc"]),
        run_id=str(data["run_id"]),
        work_item_id=str(data["work_item_id"]),
        commit=str(data["commit"]),
        bundle_sha256=str(data["bundle_sha256"]),
        bundle_path=bundle_path,
    )


def _review_decision_record_from_json(data: Any) -> ReviewDecisionRecord:
    if not isinstance(data, dict):
        raise StateStoreError("Review decision record must be an object.")
    decision = data.get("decision")
    if decision not in {"approve", "reject", "escalate"}:
        raise StateStoreError(f"Invalid review decision: {decision}")
    raw_findings = data.get("findings", ())
    if not isinstance(raw_findings, list):
        raise StateStoreError("Review decision findings must be a list.")
    findings: list[ReviewFinding] = []
    for item in raw_findings:
        if not isinstance(item, dict):
            raise StateStoreError("Review finding must be an object.")
        findings.append(
            ReviewFinding(
                code=str(item["code"]),
                severity=str(item["severity"]),
                message=str(item["message"]),
                path=_optional_string(item.get("path")),
            )
        )
    raw_correction = data.get("correction_brief")
    correction = None
    if raw_correction is not None:
        if not isinstance(raw_correction, dict):
            raise StateStoreError("Review correction_brief must be an object or null.")
        correction = CorrectionBrief(
            rationale=str(raw_correction["rationale"]),
            instructions=tuple(str(item) for item in raw_correction.get("instructions", ())),
            validation_focus=tuple(str(item) for item in raw_correction.get("validation_focus", ())),
        )
    raw_escalation = data.get("escalation")
    escalation = None
    if raw_escalation is not None:
        if not isinstance(raw_escalation, dict):
            raise StateStoreError("Review escalation must be an object or null.")
        escalation = EscalationRequest(
            reason=str(raw_escalation["reason"]),
            question=str(raw_escalation["question"]),
            options=tuple(str(item) for item in raw_escalation.get("options", ())),
        )
    return ReviewDecisionRecord(
        decision_id=str(data["decision_id"]),
        bundle_id=str(data["bundle_id"]),
        bundle_sha256=str(data["bundle_sha256"]),
        run_id=str(data["run_id"]),
        work_item_id=str(data["work_item_id"]),
        commit=str(data["commit"]),
        reviewer_id=str(data["reviewer_id"]),
        reviewer_kind=str(data["reviewer_kind"]),
        decision=decision,
        summary=str(data["summary"]),
        findings=tuple(findings),
        created_at_utc=str(data["created_at_utc"]),
        imported_at_utc=str(data["imported_at_utc"]),
        protocol_version=str(data.get("protocol_version", "1.0")),
        correction_brief=correction,
        escalation=escalation,
    )


def _operator_event_from_json(data: Any) -> OperatorEvent:
    if not isinstance(data, dict):
        raise StateStoreError("Operator event entry must be an object.")
    return OperatorEvent(
        event_id=str(data["event_id"]),
        created_at_utc=str(data["created_at_utc"]),
        command=str(data["command"]),
        ok=bool(data["ok"]),
        message=str(data["message"]),
        raw_text=str(data["raw_text"]),
        task_id=_optional_string(data.get("task_id")),
    )


def _command_result_from_json(data: Any) -> CommandResult:
    if not isinstance(data, dict):
        raise StateStoreError("Command result entry must be an object.")
    return CommandResult(
        command=str(data["command"]),
        return_code=int(data["return_code"]),
        stdout=str(data.get("stdout") or ""),
        stderr=str(data.get("stderr") or ""),
    )


def _status_from_json(value: Any) -> TaskStatus:
    if value in {
        "queued",
        "running",
        "review_pending",
        "rework_required",
        "escalated",
        "done",
        "failed",
        "rolled_back",
    }:
        return value
    raise StateStoreError(f"Invalid task status: {value}")


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _relative_or_absolute(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _parse_utc_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise StateStoreError(f"Invalid UTC timestamp in queue state: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _result_error(result: HarnessRunResult) -> str | None:
    findings = result.pre_apply.findings + result.post_apply.findings
    if findings:
        return "; ".join(findings)
    failed_commands = [item for item in result.command_results if not item.ok]
    if failed_commands:
        return "; ".join(f"{item.command} exited {item.return_code}" for item in failed_commands)
    if result.commit is None:
        return "No commit was created."
    return None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
