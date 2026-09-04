from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from .config import RoleIdentityConfig, ThreeHeadConfig
from .coordination import named_state_operation_lease
from .durable_io import atomic_write_text, read_authored_text
from .git_ops import decode_process_output
from .protocol import (
    PROTOCOL_NAMESPACE,
    PROTOCOL_VERSION,
    canonical_json_bytes,
    command_result_payload,
    dump_json,
    run_record_payload,
    work_item_payload,
)
from .state import (
    CorrectionBrief,
    EscalationRequest,
    HohStateStore,
    ReviewBundleRecord,
    ReviewDecisionRecord,
    ReviewFinding,
    RunRecord,
    StateStoreError,
)
from .operations import crash_point


REVIEW_BUNDLE_MESSAGE = "verifier.review_bundle"
REVIEW_DECISION_MESSAGE = "verifier.decision"
THREE_HEAD_PROTOCOL_VERSION = "2.0"
THREE_HEAD_BUNDLE_MESSAGE = "critic.review_bundle"
THREE_HEAD_DECISION_MESSAGE = "critic.decision"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ReviewProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReviewBundleExport:
    record: ReviewBundleRecord
    payload: dict[str, Any]
    output_path: Path


def load_review_bundle(store: HohStateStore, bundle_id: str) -> dict[str, Any]:
    """Load and validate the canonical bundle recorded by the supervisor."""
    record = next((item for item in store.review_bundles() if item.bundle_id == bundle_id), None)
    if record is None:
        raise ReviewProtocolError(f"Unknown review bundle: {bundle_id}")
    payload = _load_json_object(record.bundle_path, "Review bundle")
    validate_review_bundle(payload)
    if payload["integrity"]["payload_sha256"] != record.bundle_sha256:
        raise ReviewProtocolError(f"Stored review bundle evidence does not match its index: {bundle_id}")
    return payload


def export_review_bundle(
    repository: Path,
    store: HohStateStore,
    run_id: str,
    output_path: Path | None = None,
    three_head: ThreeHeadConfig | None = None,
) -> ReviewBundleExport:
    lease = named_state_operation_lease(store.root, "review-export", store.coordination)
    with lease.hold(f"review.export:{run_id}"):
        return _export_review_bundle_locked(
            repository,
            store,
            run_id,
            output_path,
            three_head,
        )


def _export_review_bundle_locked(
    repository: Path,
    store: HohStateStore,
    run_id: str,
    output_path: Path | None,
    three_head: ThreeHeadConfig | None,
) -> ReviewBundleExport:
    root = repository.resolve()
    run = _find_run(store, run_id)
    if not run.ok or run.commit is None:
        raise ReviewProtocolError(f"Only a successful committed run can be exported for review: {run_id}")
    task = _find_task(store, run.work_item_id)
    commit = _resolve_commit(root, run.commit)
    strict = three_head is not None and three_head.required
    bundle_id = f"three-head-{run.run_id}" if strict else f"review-{run.run_id}"

    existing = next((item for item in store.review_bundles() if item.bundle_id == bundle_id), None)
    if existing is not None:
        payload = _load_json_object(existing.bundle_path, "Review bundle")
        validate_review_bundle(payload)
        digest = str(payload["integrity"]["payload_sha256"])
        if digest != existing.bundle_sha256 or existing.commit != commit:
            raise ReviewProtocolError(f"Stored review bundle evidence does not match its index: {bundle_id}")
        destination = output_path.resolve() if output_path else existing.bundle_path
        if destination != existing.bundle_path:
            atomic_write_text(destination, dump_json(payload))
        return ReviewBundleExport(existing, payload, destination)

    patch_text = _git(root, "show", "--format=", "--binary", "--no-ext-diff", commit)
    patch_sha256 = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
    changed_files = _changed_files(root, commit)
    parent_commit = _parent_commit(root, commit)
    created_at = _utc_now()
    payload: dict[str, Any] = {
        "protocol": PROTOCOL_NAMESPACE,
        "protocol_version": THREE_HEAD_PROTOCOL_VERSION if strict else PROTOCOL_VERSION,
        "message_type": THREE_HEAD_BUNDLE_MESSAGE if strict else REVIEW_BUNDLE_MESSAGE,
        "bundle_id": bundle_id,
        "created_at_utc": created_at,
        "repository": {
            "path": str(root),
            "commit": commit,
            "parent_commit": parent_commit,
        },
        "task": work_item_payload(task.work_item),
        "scope": {
            "allowed_paths": list(task.work_item.allowed_paths),
            "non_goals": list(task.work_item.non_goals),
            "changed_files": list(changed_files),
        },
        "artifact": {
            "contract": "git_commit_diff",
            "commit": commit,
            "patch_sha256": patch_sha256,
            "patch_text": patch_text,
        },
        "verification": {
            "run": run_record_payload(run),
            "pre_apply_ok": not run.pre_apply_findings,
            "post_apply_ok": not run.post_apply_findings,
            "commands_ok": all(item.ok for item in run.command_results),
            "command_results": [command_result_payload(item) for item in run.command_results],
        },
        "authority": {
            "worker": ["produce_candidate_patch"],
            "verifier": ["inspect_evidence", "approve", "reject", "escalate"] if strict else ["inspect_evidence", "approve", "reject"],
            "supervisor": ["apply_patch", "run_checks", "commit", "merge", "handoff"],
            "worker_commit_allowed": False,
            "worker_merge_allowed": False,
            "verifier_commit_allowed": False,
            "verifier_merge_allowed": False,
        },
    }
    if strict and three_head is not None:
        payload["three_head"] = {
            "mode": three_head.mode,
            "attempt": task.attempts,
            "max_attempts": three_head.max_attempts,
            "roles": {
                "logic": _role_payload(three_head.logic),
                "worker": _role_payload(three_head.worker),
                "critic": _role_payload(three_head.critic),
            },
            "correction_context": {
                "decision_id": task.work_item.correction_decision_id,
                "instructions": list(task.work_item.correction_instructions),
            },
            "closure_policy": {
                "critic_approval_required": True,
                "supervisor_owns_git": True,
                "supervisor_confirms_rework": True,
            },
        }
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    payload["integrity"] = {"algorithm": "sha256", "payload_sha256": digest}
    validate_review_bundle(payload)

    canonical_path = store.review_bundles_dir / f"{bundle_id}.json"
    atomic_write_text(canonical_path, dump_json(payload))
    crash_point("review.bundle_after_file")
    record = store.record_review_bundle(
        ReviewBundleRecord(
            bundle_id=bundle_id,
            created_at_utc=created_at,
            run_id=run.run_id,
            work_item_id=run.work_item_id,
            commit=commit,
            bundle_sha256=digest,
            bundle_path=canonical_path,
        )
    )
    crash_point("review.bundle_after_index")
    store.journal.record(
        "interaction",
        "supervisor",
        "critic.review_bundle_payload",
        recipient="critic",
        content=payload,
        task_id=run.work_item_id,
        run_id=run.run_id,
        correlation_id=bundle_id,
    )
    destination = output_path.resolve() if output_path else canonical_path
    if destination != canonical_path:
        atomic_write_text(destination, dump_json(payload))
    return ReviewBundleExport(record, payload, destination)


def import_verifier_decision(
    repository: Path,
    store: HohStateStore,
    decision_path: Path,
) -> ReviewDecisionRecord:
    with store.transaction("review.import_decision"):
        return _import_verifier_decision_locked(repository, store, decision_path)


def _import_verifier_decision_locked(
    repository: Path,
    store: HohStateStore,
    decision_path: Path,
) -> ReviewDecisionRecord:
    payload = _load_json_object(decision_path, "Verifier decision")
    strict_decision = payload.get("protocol_version") == THREE_HEAD_PROTOCOL_VERSION
    validated = validate_critic_decision(payload) if strict_decision else validate_verifier_decision(payload)
    bundle_id = validated["bundle_id"]
    bundle_record = next((item for item in store.review_bundles() if item.bundle_id == bundle_id), None)
    if bundle_record is None:
        raise ReviewProtocolError(f"Unknown review bundle: {bundle_id}")

    bundle = _load_json_object(bundle_record.bundle_path, "Review bundle")
    validate_review_bundle(bundle)
    strict_bundle = bundle.get("protocol_version") == THREE_HEAD_PROTOCOL_VERSION
    if strict_bundle != strict_decision:
        raise ReviewProtocolError("Three-head review bundles require a protocol v2 critic decision.")
    if strict_bundle:
        expected_critic = bundle["three_head"]["roles"]["critic"]["identity"]
        if validated["critic"]["identity"] != expected_critic:
            raise ReviewProtocolError("Critic decision identity does not match the review bundle role manifest.")
    bundle_digest = str(bundle["integrity"]["payload_sha256"])
    artifact = bundle["artifact"]
    attestation = validated["attestation"]
    if validated["bundle_sha256"] != bundle_digest or bundle_record.bundle_sha256 != bundle_digest:
        raise ReviewProtocolError("Verifier decision bundle_sha256 does not match canonical bundle evidence.")
    if attestation["reviewed_commit"] != artifact["commit"]:
        raise ReviewProtocolError("Verifier decision reviewed_commit does not match canonical bundle evidence.")
    if attestation["reviewed_patch_sha256"] != artifact["patch_sha256"]:
        raise ReviewProtocolError("Verifier decision reviewed_patch_sha256 does not match canonical bundle evidence.")
    resolved_commit = _resolve_commit(repository.resolve(), str(artifact["commit"]))
    if resolved_commit != bundle_record.commit:
        raise ReviewProtocolError("Canonical repository no longer resolves the reviewed commit evidence.")

    existing = next(
        (item for item in store.review_decisions() if item.decision_id == validated["decision_id"]),
        None,
    )
    if existing is not None:
        if _decision_matches(existing, validated, bundle_record):
            if strict_bundle:
                _apply_strict_decision_if_pending(store, existing)
            return existing
        raise ReviewProtocolError(f"Review decision id already exists with different evidence: {existing.decision_id}")

    findings = tuple(
        ReviewFinding(
            code=item["code"],
            severity=item["severity"],
            message=item["message"],
            path=item.get("path"),
        )
        for item in validated["findings"]
    )
    actor = validated["critic"] if strict_decision else validated["reviewer"]
    correction = None
    if validated.get("correction_brief") is not None:
        raw_correction = validated["correction_brief"]
        correction = CorrectionBrief(
            rationale=raw_correction["rationale"],
            instructions=tuple(raw_correction["instructions"]),
            validation_focus=tuple(raw_correction["validation_focus"]),
        )
    escalation = None
    if validated.get("escalation") is not None:
        raw_escalation = validated["escalation"]
        escalation = EscalationRequest(
            reason=raw_escalation["reason"],
            question=raw_escalation["question"],
            options=tuple(raw_escalation["options"]),
        )
    record = ReviewDecisionRecord(
        decision_id=validated["decision_id"],
        bundle_id=bundle_record.bundle_id,
        bundle_sha256=bundle_digest,
        run_id=bundle_record.run_id,
        work_item_id=bundle_record.work_item_id,
        commit=bundle_record.commit,
        reviewer_id=actor["identity"] if strict_decision else actor["id"],
        reviewer_kind=actor["kind"],
        decision=validated["decision"],
        summary=validated["summary"],
        findings=findings,
        created_at_utc=validated["created_at_utc"],
        imported_at_utc=_utc_now(),
        protocol_version=validated["protocol_version"],
        correction_brief=correction,
        escalation=escalation,
    )
    try:
        stored = store.record_review_decision(record)
        crash_point("review.decision_after_record")
        store.journal.record(
            "interaction",
            "critic" if strict_decision else "external-verifier",
            "review.decision_payload",
            recipient="supervisor",
            content=validated,
            task_id=record.work_item_id,
            run_id=record.run_id,
            correlation_id=record.bundle_id,
        )
        if strict_bundle:
            _apply_strict_decision_if_pending(store, stored)
        return stored
    except StateStoreError as exc:
        raise ReviewProtocolError(str(exc)) from exc


def validate_review_bundle(payload: Mapping[str, Any]) -> None:
    strict = payload.get("protocol_version") == THREE_HEAD_PROTOCOL_VERSION
    required = {
        "protocol",
        "protocol_version",
        "message_type",
        "bundle_id",
        "created_at_utc",
        "repository",
        "task",
        "scope",
        "artifact",
        "verification",
        "authority",
        "integrity",
    }
    if strict:
        required.add("three_head")
    _require_exact_keys(payload, required, "Review bundle")
    if strict:
        _require_protocol_version(payload, THREE_HEAD_PROTOCOL_VERSION, THREE_HEAD_BUNDLE_MESSAGE)
        _validate_three_head_manifest(payload["three_head"])
    else:
        _require_protocol(payload, REVIEW_BUNDLE_MESSAGE)
    _require_safe_id(payload["bundle_id"], "bundle_id")
    _require_timestamp(payload["created_at_utc"], "created_at_utc")
    integrity = _require_object(payload["integrity"], "integrity")
    _require_exact_keys(integrity, {"algorithm", "payload_sha256"}, "integrity")
    if integrity["algorithm"] != "sha256" or not _is_sha256(integrity["payload_sha256"]):
        raise ReviewProtocolError("Review bundle integrity must contain a lowercase SHA-256 digest.")
    content = dict(payload)
    content.pop("integrity")
    actual = hashlib.sha256(canonical_json_bytes(content)).hexdigest()
    if actual != integrity["payload_sha256"]:
        raise ReviewProtocolError("Review bundle integrity digest does not match its payload.")
    artifact = _require_object(payload["artifact"], "artifact")
    if not _is_sha256(artifact.get("patch_sha256")):
        raise ReviewProtocolError("Review bundle artifact.patch_sha256 must be a lowercase SHA-256 digest.")
    patch_text = artifact.get("patch_text")
    if not isinstance(patch_text, str):
        raise ReviewProtocolError("Review bundle artifact.patch_text must be a string.")
    if hashlib.sha256(patch_text.encode("utf-8")).hexdigest() != artifact["patch_sha256"]:
        raise ReviewProtocolError("Review bundle patch digest does not match patch_text.")
    authority = _require_object(payload["authority"], "authority")
    denied = (
        "worker_commit_allowed",
        "worker_merge_allowed",
        "verifier_commit_allowed",
        "verifier_merge_allowed",
    )
    if any(authority.get(field) is not False for field in denied):
        raise ReviewProtocolError("Review bundle must deny worker and verifier commit/merge authority.")


def validate_verifier_decision(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "protocol",
        "protocol_version",
        "message_type",
        "decision_id",
        "bundle_id",
        "bundle_sha256",
        "reviewer",
        "decision",
        "summary",
        "findings",
        "created_at_utc",
        "attestation",
    }
    _require_exact_keys(payload, required, "Verifier decision")
    _require_protocol(payload, REVIEW_DECISION_MESSAGE)
    _require_safe_id(payload["decision_id"], "decision_id")
    _require_safe_id(payload["bundle_id"], "bundle_id")
    if not _is_sha256(payload["bundle_sha256"]):
        raise ReviewProtocolError("Verifier decision bundle_sha256 must be a lowercase SHA-256 digest.")
    reviewer = _require_object(payload["reviewer"], "reviewer")
    _require_exact_keys(reviewer, {"id", "kind"}, "reviewer")
    _require_safe_id(reviewer["id"], "reviewer.id")
    if reviewer["kind"] not in {"agent", "human", "service"}:
        raise ReviewProtocolError("reviewer.kind must be agent, human, or service.")
    decision = payload["decision"]
    if decision not in {"approve", "reject"}:
        raise ReviewProtocolError("decision must be approve or reject.")
    if not isinstance(payload["summary"], str) or not payload["summary"].strip():
        raise ReviewProtocolError("summary must be a non-empty string.")
    _require_timestamp(payload["created_at_utc"], "created_at_utc")
    attestation = _require_object(payload["attestation"], "attestation")
    _require_exact_keys(attestation, {"reviewed_commit", "reviewed_patch_sha256"}, "attestation")
    if not isinstance(attestation["reviewed_commit"], str) or not attestation["reviewed_commit"].strip():
        raise ReviewProtocolError("attestation.reviewed_commit must be a non-empty string.")
    if not _is_sha256(attestation["reviewed_patch_sha256"]):
        raise ReviewProtocolError("attestation.reviewed_patch_sha256 must be a lowercase SHA-256 digest.")
    normalized_findings = _normalize_findings(payload["findings"], decision)
    result = dict(payload)
    result["reviewer"] = dict(reviewer)
    result["attestation"] = dict(attestation)
    result["findings"] = normalized_findings
    return result


def validate_critic_decision(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "protocol",
        "protocol_version",
        "message_type",
        "decision_id",
        "bundle_id",
        "bundle_sha256",
        "critic",
        "decision",
        "summary",
        "findings",
        "correction_brief",
        "escalation",
        "created_at_utc",
        "attestation",
    }
    _require_exact_keys(payload, required, "Critic decision")
    _require_protocol_version(payload, THREE_HEAD_PROTOCOL_VERSION, THREE_HEAD_DECISION_MESSAGE)
    _require_safe_id(payload["decision_id"], "decision_id")
    _require_safe_id(payload["bundle_id"], "bundle_id")
    if not _is_sha256(payload["bundle_sha256"]):
        raise ReviewProtocolError("Critic decision bundle_sha256 must be a lowercase SHA-256 digest.")
    critic = _require_object(payload["critic"], "critic")
    _require_exact_keys(critic, {"identity", "kind"}, "critic")
    _require_safe_id(critic["identity"], "critic.identity")
    if critic["kind"] not in {"agent", "human", "service"}:
        raise ReviewProtocolError("critic.kind must be agent, human, or service.")
    decision = payload["decision"]
    if decision not in {"approve", "reject", "escalate"}:
        raise ReviewProtocolError("decision must be approve, reject, or escalate.")
    if not isinstance(payload["summary"], str) or not payload["summary"].strip():
        raise ReviewProtocolError("summary must be a non-empty string.")
    _require_timestamp(payload["created_at_utc"], "created_at_utc")
    attestation = _require_object(payload["attestation"], "attestation")
    _require_exact_keys(attestation, {"reviewed_commit", "reviewed_patch_sha256"}, "attestation")
    if not isinstance(attestation["reviewed_commit"], str) or not attestation["reviewed_commit"].strip():
        raise ReviewProtocolError("attestation.reviewed_commit must be a non-empty string.")
    if not _is_sha256(attestation["reviewed_patch_sha256"]):
        raise ReviewProtocolError("attestation.reviewed_patch_sha256 must be a lowercase SHA-256 digest.")
    findings = _normalize_findings(payload["findings"], decision)
    correction = payload["correction_brief"]
    escalation = payload["escalation"]
    if decision == "approve":
        if correction is not None or escalation is not None:
            raise ReviewProtocolError("Approve decisions cannot contain correction_brief or escalation.")
    elif decision == "reject":
        if correction is None or escalation is not None:
            raise ReviewProtocolError("Reject decisions require correction_brief and cannot contain escalation.")
        correction = _validate_correction_brief(correction)
    else:
        if escalation is None or correction is not None:
            raise ReviewProtocolError("Escalate decisions require escalation and cannot contain correction_brief.")
        escalation = _validate_escalation(escalation)
    result = dict(payload)
    result["critic"] = dict(critic)
    result["attestation"] = dict(attestation)
    result["findings"] = findings
    result["correction_brief"] = correction
    result["escalation"] = escalation
    return result


def review_decision_payload(record: ReviewDecisionRecord) -> dict[str, Any]:
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


def _find_run(store: HohStateStore, run_id: str) -> RunRecord:
    for run in store.history():
        if run.run_id == run_id:
            return run
    raise ReviewProtocolError(f"Unknown run id: {run_id}")


def _find_task(store: HohStateStore, work_item_id: str):
    for task in reversed(store.list_tasks()):
        if task.work_item.id == work_item_id:
            return task
    raise ReviewProtocolError(f"Run references an unknown queued task: {work_item_id}")


def _resolve_commit(repository: Path, commit: str) -> str:
    return _git(repository, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()


def _parent_commit(repository: Path, commit: str) -> str | None:
    parts = _git(repository, "rev-list", "--parents", "-n", "1", commit).strip().split()
    return parts[1] if len(parts) > 1 else None


def _changed_files(repository: Path, commit: str) -> tuple[str, ...]:
    output = _git(repository, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit)
    return tuple(line for line in output.splitlines() if line.strip())


def _git(repository: Path, *args: str) -> str:
    """Binary read: text mode would strip the CR out of a CRLF patch body.

    The reviewed patch is hashed, so a rewritten newline is not merely cosmetic --
    it would make the bundle describe a patch that never existed.
    """
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ReviewProtocolError(
            decode_process_output(completed.stderr).strip() or f"git {' '.join(args)} failed"
        )
    return decode_process_output(completed.stdout)


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(read_authored_text(path))
    except OSError as exc:
        raise ReviewProtocolError(f"{label} cannot be read: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReviewProtocolError(f"{label} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReviewProtocolError(f"{label} must be a JSON object.")
    return raw


def _require_protocol(payload: Mapping[str, Any], message_type: str) -> None:
    if payload.get("protocol") != PROTOCOL_NAMESPACE:
        raise ReviewProtocolError(f"protocol must be {PROTOCOL_NAMESPACE}.")
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ReviewProtocolError(f"protocol_version must be {PROTOCOL_VERSION}.")
    if payload.get("message_type") != message_type:
        raise ReviewProtocolError(f"message_type must be {message_type}.")


def _require_protocol_version(payload: Mapping[str, Any], version: str, message_type: str) -> None:
    if payload.get("protocol") != PROTOCOL_NAMESPACE:
        raise ReviewProtocolError(f"protocol must be {PROTOCOL_NAMESPACE}.")
    if payload.get("protocol_version") != version:
        raise ReviewProtocolError(f"protocol_version must be {version}.")
    if payload.get("message_type") != message_type:
        raise ReviewProtocolError(f"message_type must be {message_type}.")


def _role_payload(role: RoleIdentityConfig) -> dict[str, str]:
    return {"identity": role.identity, "provider": role.provider, "model": role.model}


def _validate_three_head_manifest(value: Any) -> None:
    manifest = _require_object(value, "three_head")
    _require_exact_keys(
        manifest,
        {"mode", "attempt", "max_attempts", "roles", "correction_context", "closure_policy"},
        "three_head",
    )
    if manifest["mode"] != "required":
        raise ReviewProtocolError("Three-head bundle mode must be required.")
    if not isinstance(manifest["attempt"], int) or manifest["attempt"] < 1:
        raise ReviewProtocolError("Three-head attempt must be a positive integer.")
    if not isinstance(manifest["max_attempts"], int) or manifest["max_attempts"] < 1:
        raise ReviewProtocolError("Three-head max_attempts must be a positive integer.")
    roles = _require_object(manifest["roles"], "three_head.roles")
    _require_exact_keys(roles, {"logic", "worker", "critic"}, "three_head.roles")
    identities: list[str] = []
    fingerprints: list[tuple[str, str]] = []
    for name in ("logic", "worker", "critic"):
        role = _require_object(roles[name], f"three_head.roles.{name}")
        _require_exact_keys(role, {"identity", "provider", "model"}, f"three_head.roles.{name}")
        for field in ("identity", "provider", "model"):
            if not isinstance(role[field], str) or not role[field].strip():
                raise ReviewProtocolError(f"three_head.roles.{name}.{field} must be non-empty.")
        identities.append(role["identity"].strip().casefold())
        fingerprints.append((role["provider"].strip().casefold(), role["model"].strip().casefold()))
    if len(set(identities)) != 3 or len(set(fingerprints)) != 3:
        raise ReviewProtocolError("Three-head bundle must contain three distinct role identities and models.")
    correction = _require_object(manifest["correction_context"], "three_head.correction_context")
    _require_exact_keys(correction, {"decision_id", "instructions"}, "three_head.correction_context")
    if correction["decision_id"] is not None:
        _require_safe_id(correction["decision_id"], "three_head.correction_context.decision_id")
    if not isinstance(correction["instructions"], list) or any(
        not isinstance(item, str) or not item.strip() for item in correction["instructions"]
    ):
        raise ReviewProtocolError("three_head.correction_context.instructions must be non-empty strings.")
    closure = _require_object(manifest["closure_policy"], "three_head.closure_policy")
    _require_exact_keys(
        closure,
        {"critic_approval_required", "supervisor_owns_git", "supervisor_confirms_rework"},
        "three_head.closure_policy",
    )
    if any(closure.get(field) is not True for field in closure):
        raise ReviewProtocolError("Three-head closure policy invariants must all be true.")


def _normalize_findings(value: Any, decision: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ReviewProtocolError("findings must be a list.")
    if decision in {"reject", "escalate"} and not value:
        raise ReviewProtocolError(f"A {decision} decision must contain at least one finding.")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        finding = _require_object(raw, f"findings[{index}]")
        allowed = {"code", "severity", "message", "path"}
        if not {"code", "severity", "message"}.issubset(finding) or set(finding) - allowed:
            raise ReviewProtocolError(f"findings[{index}] has missing or unknown fields.")
        _require_safe_id(finding["code"], f"findings[{index}].code")
        if finding["severity"] not in {"info", "warning", "error", "blocker"}:
            raise ReviewProtocolError(f"findings[{index}].severity is invalid.")
        if decision == "approve" and finding["severity"] in {"error", "blocker"}:
            raise ReviewProtocolError("An approve decision cannot contain error or blocker findings.")
        if not isinstance(finding["message"], str) or not finding["message"].strip():
            raise ReviewProtocolError(f"findings[{index}].message must be a non-empty string.")
        path = finding.get("path")
        if path is not None and (not isinstance(path, str) or not path.strip()):
            raise ReviewProtocolError(f"findings[{index}].path must be null or a non-empty string.")
        normalized.append(dict(finding))
    return normalized


def _validate_correction_brief(value: Any) -> dict[str, Any]:
    brief = _require_object(value, "correction_brief")
    _require_exact_keys(brief, {"rationale", "instructions", "validation_focus"}, "correction_brief")
    if not isinstance(brief["rationale"], str) or not brief["rationale"].strip():
        raise ReviewProtocolError("correction_brief.rationale must be non-empty.")
    for field in ("instructions", "validation_focus"):
        items = brief[field]
        if not isinstance(items, list) or (field == "instructions" and not items):
            raise ReviewProtocolError(f"correction_brief.{field} must be a list of strings.")
        if any(not isinstance(item, str) or not item.strip() for item in items):
            raise ReviewProtocolError(f"correction_brief.{field} must contain non-empty strings.")
    return dict(brief)


def _validate_escalation(value: Any) -> dict[str, Any]:
    escalation = _require_object(value, "escalation")
    _require_exact_keys(escalation, {"reason", "question", "options"}, "escalation")
    for field in ("reason", "question"):
        if not isinstance(escalation[field], str) or not escalation[field].strip():
            raise ReviewProtocolError(f"escalation.{field} must be non-empty.")
    options = escalation["options"]
    if not isinstance(options, list) or any(not isinstance(item, str) or not item.strip() for item in options):
        raise ReviewProtocolError("escalation.options must contain non-empty strings.")
    if "реши сам" not in options or "свой" not in options:
        raise ReviewProtocolError("escalation.options must include 'реши сам' and 'свой'.")
    return dict(escalation)


def _require_exact_keys(payload: Mapping[str, Any], keys: set[str], label: str) -> None:
    missing = keys - set(payload)
    unknown = set(payload) - keys
    if missing:
        raise ReviewProtocolError(f"{label} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ReviewProtocolError(f"{label} contains unknown fields: {', '.join(sorted(unknown))}")


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewProtocolError(f"{label} must be an object.")
    return value


def _require_safe_id(value: Any, label: str) -> None:
    if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value):
        raise ReviewProtocolError(f"{label} must be a safe non-empty identifier up to 128 characters.")


def _require_timestamp(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise ReviewProtocolError(f"{label} must be an ISO-8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewProtocolError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ReviewProtocolError(f"{label} must include a timezone offset.")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _decision_matches(
    existing: ReviewDecisionRecord,
    payload: Mapping[str, Any],
    bundle: ReviewBundleRecord,
) -> bool:
    strict = payload.get("protocol_version") == THREE_HEAD_PROTOCOL_VERSION
    actor = payload["critic"] if strict else payload["reviewer"]
    actor_id = actor["identity"] if strict else actor["id"]
    findings = tuple(
        (item.code, item.severity, item.message, item.path)
        for item in existing.findings
    )
    incoming_findings = tuple(
        (item["code"], item["severity"], item["message"], item.get("path"))
        for item in payload["findings"]
    )
    return (
        existing.bundle_id == bundle.bundle_id
        and existing.bundle_sha256 == payload["bundle_sha256"]
        and existing.reviewer_id == actor_id
        and existing.reviewer_kind == actor["kind"]
        and existing.decision == payload["decision"]
        and existing.summary == payload["summary"]
        and existing.created_at_utc == payload["created_at_utc"]
        and existing.protocol_version == payload["protocol_version"]
        and findings == incoming_findings
        and _correction_matches(existing.correction_brief, payload.get("correction_brief"))
        and _escalation_matches(existing.escalation, payload.get("escalation"))
    )


def _correction_matches(existing: CorrectionBrief | None, incoming: Any) -> bool:
    if existing is None or incoming is None:
        return existing is None and incoming is None
    return (
        existing.rationale == incoming["rationale"]
        and existing.instructions == tuple(incoming["instructions"])
        and existing.validation_focus == tuple(incoming["validation_focus"])
    )


def _escalation_matches(existing: EscalationRequest | None, incoming: Any) -> bool:
    if existing is None or incoming is None:
        return existing is None and incoming is None
    return (
        existing.reason == incoming["reason"]
        and existing.question == incoming["question"]
        and existing.options == tuple(incoming["options"])
    )


def _apply_strict_decision_if_pending(store: HohStateStore, record: ReviewDecisionRecord) -> None:
    task = next((item for item in reversed(store.list_tasks()) if item.work_item.id == record.work_item_id), None)
    if task is None:
        raise ReviewProtocolError(f"Critic decision references an unknown task: {record.work_item_id}")
    expected_status = {
        "approve": "done",
        "reject": "rework_required",
        "escalate": "escalated",
    }[record.decision]
    if task.status == expected_status and task.last_review_decision_id == record.decision_id:
        return
    try:
        store.resolve_review(
            record.work_item_id,
            record.bundle_id,
            record.decision_id,
            record.decision,
            record.summary,
        )
    except StateStoreError as exc:
        raise ReviewProtocolError(str(exc)) from exc


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
