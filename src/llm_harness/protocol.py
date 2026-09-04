from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import TYPE_CHECKING, Any, Mapping

from .audit import ProjectAuditReport
from .domain import (
    CommandResult,
    HarnessRunResult,
    SemanticVerificationReport,
    VerificationReport,
    WorkItem,
)
from .state import (
    AuditRecord,
    OperatorEvent,
    QueueSchedule,
    QueueTask,
    RollbackRecord,
    ReviewDecisionRecord,
    RunRecord,
    TaskReadiness,
)

if TYPE_CHECKING:
    from .supervisor_protocol import SupervisorStatusReport


PROTOCOL_VERSION = "1.0"
PROTOCOL_NAMESPACE = "hoh.protocol"


def protocol_envelope(
    message_type: str,
    *,
    ok: bool,
    data: Mapping[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build the stable vendor-neutral envelope used by machine-facing CLI output."""
    payload: dict[str, Any] = {
        "protocol": PROTOCOL_NAMESPACE,
        "protocol_version": PROTOCOL_VERSION,
        "message_type": message_type,
        "ok": ok,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "data": dict(data or {}),
    }
    if error is not None:
        payload["error"] = error
    return payload


def dump_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def work_item_payload(work_item: WorkItem) -> dict[str, Any]:
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
    }


def command_result_payload(result: CommandResult) -> dict[str, Any]:
    return {
        "command": result.command,
        "return_code": result.return_code,
        "ok": result.ok,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def verification_report_payload(report: VerificationReport) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "findings": list(report.findings),
        "metrics": dict(report.metrics),
    }


def harness_run_result_payload(result: HarnessRunResult) -> dict[str, Any]:
    return {
        "repository": str(result.repository),
        "work_item_id": result.work_item_id,
        "ok": result.ok,
        "commit": result.commit,
        "pre_apply": verification_report_payload(result.pre_apply),
        "post_apply": verification_report_payload(result.post_apply),
        "command_results": [command_result_payload(item) for item in result.command_results],
        "semantic_verification": semantic_verification_payload(result.semantic_verification),
    }


def queue_task_payload(task: QueueTask) -> dict[str, Any]:
    return {
        "work_item": work_item_payload(task.work_item),
        "status": task.status,
        "source": task.source,
        "added_at_utc": task.added_at_utc,
        "updated_at_utc": task.updated_at_utc,
        "attempts": task.attempts,
        "last_commit": task.last_commit,
        "last_error": task.last_error,
        "pending_review_bundle_id": task.pending_review_bundle_id,
        "last_review_decision_id": task.last_review_decision_id,
        "correction_context": {
            "decision_id": task.work_item.correction_decision_id,
            "instructions": list(task.work_item.correction_instructions),
        },
    }


def task_readiness_payload(readiness: TaskReadiness) -> dict[str, Any]:
    return {
        "task_id": readiness.task.work_item.id,
        "ready": readiness.ready,
        "priority": readiness.task.work_item.priority,
        "depends_on": list(readiness.task.work_item.depends_on),
        "blockers": [
            {"task_id": blocker.task_id, "status": blocker.status}
            for blocker in readiness.blockers
        ],
    }


def queue_schedule_payload(schedule: QueueSchedule) -> dict[str, Any]:
    return {
        "selected_task_id": schedule.selected.work_item.id if schedule.selected else None,
        "ready": [task_readiness_payload(item) for item in schedule.ready],
        "waiting": [task_readiness_payload(item) for item in schedule.waiting],
    }


def run_record_payload(record: RunRecord) -> dict[str, Any]:
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
        "command_results": [command_result_payload(item) for item in record.command_results],
        "semantic_verification": semantic_verification_payload(record.semantic_verification),
    }


def semantic_verification_payload(
    report: SemanticVerificationReport | None,
) -> dict[str, Any] | None:
    if report is None:
        return None
    evidence = report.evidence
    return {
        "decision": report.decision,
        "ok": report.ok,
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


def rollback_record_payload(record: RollbackRecord) -> dict[str, Any]:
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
        "command_results": [command_result_payload(item) for item in record.command_results],
        "error": record.error,
    }


def audit_record_payload(record: AuditRecord) -> dict[str, Any]:
    return {
        "audit_id": record.audit_id,
        "created_at_utc": record.created_at_utc,
        "ok": record.ok,
        "report_path": str(record.report_path),
        "findings_count": record.findings_count,
        "command_results_count": record.command_results_count,
    }


def operator_event_payload(event: OperatorEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "created_at_utc": event.created_at_utc,
        "command": event.command,
        "ok": event.ok,
        "message": event.message,
        "raw_text": event.raw_text,
        "task_id": event.task_id,
    }


def review_decision_record_payload(record: ReviewDecisionRecord) -> dict[str, Any]:
    return {
        "decision_id": record.decision_id,
        "bundle_id": record.bundle_id,
        "bundle_sha256": record.bundle_sha256,
        "run_id": record.run_id,
        "work_item_id": record.work_item_id,
        "commit": record.commit,
        "reviewer": {"id": record.reviewer_id, "kind": record.reviewer_kind},
        "decision": record.decision,
        "summary": record.summary,
        "findings": [
            {
                "code": item.code,
                "severity": item.severity,
                "message": item.message,
                "path": item.path,
            }
            for item in record.findings
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


def supervisor_status_payload(report: SupervisorStatusReport) -> dict[str, Any]:
    return {
        "repository": str(report.repository),
        "state_root": str(report.state_root),
        "queue": {
            "queued": report.queued,
            "ready": report.ready,
            "waiting": report.waiting,
            "running": report.running,
            "done": report.done,
            "failed": report.failed,
            "rolled_back": report.rolled_back,
            "review_pending": report.review_pending,
            "rework_required": report.rework_required,
            "escalated": report.escalated,
            "stale": report.stale,
            "stale_task_ids": list(report.stale_task_ids),
        },
        "latest_run": run_record_payload(report.latest_run) if report.latest_run else None,
        "latest_audit": audit_record_payload(report.latest_audit) if report.latest_audit else None,
        "latest_operator_event": (
            operator_event_payload(report.latest_operator_event) if report.latest_operator_event else None
        ),
        "reviews": {
            "bundles": report.review_bundles,
            "pending": report.pending_reviews,
            "approved": report.approved_reviews,
            "rejected": report.rejected_reviews,
            "latest": review_decision_record_payload(report.latest_review) if report.latest_review else None,
        },
        "rollbacks": {
            "count": report.rollback_records,
            "latest": rollback_record_payload(report.latest_rollback) if report.latest_rollback else None,
        },
        "reconciliation": {
            "pending": report.reconciliation_pending,
            "operations": [
                {
                    "operation_id": item.operation_id,
                    "action": item.action,
                    "classification": item.classification,
                    "safe_action": item.safe_action,
                    "detail": item.detail,
                    "terminal": item.terminal,
                }
                for item in report.reconciliation_items
            ],
        },
    }


def project_audit_payload(report: ProjectAuditReport) -> dict[str, Any]:
    return {
        "project_root": str(report.project_root),
        "ready": report.ok,
        "metrics": dict(report.metrics),
        "findings": [
            {"code": finding.code, "message": finding.message, "path": finding.path}
            for finding in report.findings
        ],
        "language_analyzers": [
            {
                "analyzer": evidence.analyzer,
                "language": evidence.language,
                "status": evidence.status,
                "files_analyzed": evidence.files_analyzed,
                "findings_count": len(evidence.findings),
                "findings": [
                    {"code": item.code, "message": item.message, "path": item.path}
                    for item in evidence.findings
                ],
                "detail": evidence.detail,
            }
            for evidence in report.analyzer_evidence
        ],
        "command_results": [command_result_payload(item) for item in report.command_results],
    }
