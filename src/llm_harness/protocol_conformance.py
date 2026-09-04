from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import sys

from .domain import WorkItem
from .driver_registry import DriverManifest
from .journal import journal_event_from_payload, validate_journal_ingress
from .lifecycle import load_project_spec
from .protocol import PROTOCOL_NAMESPACE, PROTOCOL_VERSION, dump_json
from .review_protocol import (
    REVIEW_DECISION_MESSAGE,
    export_review_bundle,
    import_verifier_decision,
    validate_critic_decision,
    validate_verifier_decision,
)
from .state import HohStateStore
from .supervisor import Supervisor
from .supervisor_protocol import build_supervisor_status
from .verifier import PolicyVerifier
from .workers import StubPatchWorker


REQUIRED_PROTOCOL_ASSETS = (
    "schemas/protocol-envelope-v1.schema.json",
    "schemas/review-bundle-v1.schema.json",
    "schemas/verifier-decision-v1.schema.json",
    "schemas/critic-review-bundle-v2.schema.json",
    "schemas/critic-decision-v2.schema.json",
    "schemas/project-spec-v2.schema.json",
    "schemas/role-profile-v1.schema.json",
    "schemas/role-profile-v2.schema.json",
    "schemas/driver-manifest-v1.schema.json",
    "schemas/journal-event-v1.schema.json",
    "schemas/journal-ingress-v1.schema.json",
    "examples/protocol/supervisor-status-v1.json",
    "examples/protocol/verifier-decision-v1.json",
    "examples/protocol/critic-approve-v2.json",
    "examples/protocol/critic-reject-v2.json",
    "examples/protocol/critic-escalate-v2.json",
    "examples/project-spec-v2.json",
    "examples/protocol/journal-event-v1.json",
    "examples/protocol/journal-ingress-v1.json",
    "examples/protocol/command-worker-driver-v1.json",
)

PROTOCOL_SCHEMA_ASSETS = tuple(item for item in REQUIRED_PROTOCOL_ASSETS if item.startswith("schemas/"))


class ProtocolConformanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProtocolConformanceReport:
    ok: bool
    distribution_root: Path
    repository: Path
    state_root: Path
    assets_checked: int
    run_id: str
    bundle_id: str
    bundle_sha256: str
    decision_id: str
    commit: str
    canonical_git_clean: bool


def run_protocol_conformance(
    distribution_root: Path,
    repository: Path,
) -> ProtocolConformanceReport:
    assets_checked = validate_protocol_assets(distribution_root)
    repository.mkdir(parents=True, exist_ok=True)
    _git(repository, "init")
    _git(repository, "config", "user.email", "protocol@example.local")
    _git(repository, "config", "user.name", "HoH Protocol Conformance")
    (repository / ".gitignore").write_text(".tmp\n", encoding="utf-8")
    _git(repository, "add", ".gitignore")
    _git(repository, "commit", "-m", "Initial commit")

    work_item = WorkItem(
        id="protocol-conformance",
        title="External verifier protocol conformance",
        objective="Create a deterministic artifact and hand exact evidence to an independent verifier.",
        acceptance_criteria=("HARNESS_DEMO.md exists and is independently approved.",),
        verification_commands=(
            f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'HARNESS_DEMO.md\').exists()"',
        ),
        allowed_paths=("HARNESS_DEMO.md",),
        non_goals=("Worker and verifier must not commit or merge.",),
    )
    state_root = repository.parent / "protocol-state"
    store = HohStateStore(state_root)
    store.enqueue(work_item, source="protocol-conformance")
    store.mark_running(work_item.id)
    started_at = datetime.now(UTC).isoformat()
    result = Supervisor(PolicyVerifier()).execute_work_item(repository, work_item, StubPatchWorker())
    run = store.record_result(work_item.id, started_at, result)
    if not run.ok or run.commit is None:
        raise ProtocolConformanceError("Deterministic supervisor run did not produce an accepted commit.")

    exported = export_review_bundle(repository, store, run.run_id)
    pending_status = build_supervisor_status(repository, state_root)
    if pending_status.ok or pending_status.pending_reviews != 1:
        raise ProtocolConformanceError("Exported review bundle did not create a pending supervisor gate.")

    decision_payload = _approval_payload(exported.payload)
    validate_verifier_decision(decision_payload)
    decision_path = state_root / "conformance-decision.json"
    decision_path.write_text(dump_json(decision_payload), encoding="utf-8")
    decision = import_verifier_decision(repository, store, decision_path)
    approved_status = build_supervisor_status(repository, state_root)
    clean = not _git(repository, "status", "--porcelain").strip()
    ok = (
        decision.decision == "approve"
        and approved_status.ok
        and approved_status.approved_reviews == 1
        and approved_status.pending_reviews == 0
        and approved_status.rejected_reviews == 0
        and clean
    )
    return ProtocolConformanceReport(
        ok=ok,
        distribution_root=distribution_root.resolve(),
        repository=repository.resolve(),
        state_root=state_root.resolve(),
        assets_checked=assets_checked,
        run_id=run.run_id,
        bundle_id=exported.record.bundle_id,
        bundle_sha256=exported.record.bundle_sha256,
        decision_id=decision.decision_id,
        commit=exported.record.commit,
        canonical_git_clean=clean,
    )


def validate_protocol_assets(distribution_root: Path) -> int:
    root = distribution_root.resolve()
    decoded: dict[str, dict] = {}
    for relative in REQUIRED_PROTOCOL_ASSETS:
        path = root / relative
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ProtocolConformanceError(f"Protocol asset cannot be read: {relative}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProtocolConformanceError(f"Protocol asset is invalid JSON: {relative}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ProtocolConformanceError(f"Protocol asset must contain a JSON object: {relative}")
        decoded[relative] = payload

    for relative in PROTOCOL_SCHEMA_ASSETS:
        schema = decoded[relative]
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise ProtocolConformanceError(f"Protocol schema does not declare JSON Schema 2020-12: {relative}")
        if not isinstance(schema.get("$id"), str) or schema.get("additionalProperties") is not False:
            raise ProtocolConformanceError(f"Protocol schema must have an id and a closed top-level object: {relative}")

    status = decoded["examples/protocol/supervisor-status-v1.json"]
    required_envelope = {"protocol", "protocol_version", "message_type", "ok", "generated_at_utc", "data"}
    if set(status) != required_envelope:
        raise ProtocolConformanceError("Supervisor status example does not match the closed v1 envelope.")
    if status["protocol"] != PROTOCOL_NAMESPACE or status["protocol_version"] != PROTOCOL_VERSION:
        raise ProtocolConformanceError("Supervisor status example uses an unsupported protocol version.")

    validate_verifier_decision(decoded["examples/protocol/verifier-decision-v1.json"])
    validate_critic_decision(decoded["examples/protocol/critic-approve-v2.json"])
    validate_critic_decision(decoded["examples/protocol/critic-reject-v2.json"])
    validate_critic_decision(decoded["examples/protocol/critic-escalate-v2.json"])
    load_project_spec(root / "examples/project-spec-v2.json")
    journal_event_from_payload(decoded["examples/protocol/journal-event-v1.json"])
    validate_journal_ingress(decoded["examples/protocol/journal-ingress-v1.json"])
    DriverManifest(**decoded["examples/protocol/command-worker-driver-v1.json"])
    return len(REQUIRED_PROTOCOL_ASSETS)


def _approval_payload(bundle: dict) -> dict:
    return {
        "protocol": PROTOCOL_NAMESPACE,
        "protocol_version": PROTOCOL_VERSION,
        "message_type": REVIEW_DECISION_MESSAGE,
        "decision_id": "protocol-conformance-approval",
        "bundle_id": bundle["bundle_id"],
        "bundle_sha256": bundle["integrity"]["payload_sha256"],
        "reviewer": {"id": "conformance-verifier", "kind": "agent"},
        "decision": "approve",
        "summary": "Exact task, scope, commit, patch digest, and deterministic checks are accepted.",
        "findings": [],
        "created_at_utc": datetime.now(UTC).isoformat(),
        "attestation": {
            "reviewed_commit": bundle["artifact"]["commit"],
            "reviewed_patch_sha256": bundle["artifact"]["patch_sha256"],
        },
    }


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ProtocolConformanceError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout
