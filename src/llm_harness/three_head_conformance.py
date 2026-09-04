from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys

from .config import RoleIdentityConfig, ThreeHeadConfig
from .domain import WorkItem, WorkerPatch
from .protocol import PROTOCOL_NAMESPACE, dump_json
from .review_protocol import export_review_bundle, import_verifier_decision, validate_critic_decision
from .state import HohStateStore
from .supervisor import Supervisor
from .verifier import PolicyVerifier


class ThreeHeadConformanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ThreeHeadConformanceReport:
    ok: bool
    root: Path
    approve: bool
    reject: bool
    rework: bool
    escalate: bool
    git_clean: bool
    critic_disabled: bool


class CorrectionAwareWorker:
    name = "three-head-conformance-worker"

    def produce_patch(self, repository: Path, work_item: WorkItem) -> WorkerPatch:
        if work_item.correction_decision_id:
            patch = (
                "diff --git a/RESULT.txt b/RESULT.txt\n"
                "index 5e2e48e..2d0a00e 100644\n"
                "--- a/RESULT.txt\n"
                "+++ b/RESULT.txt\n"
                "@@ -1 +1 @@\n"
                "-candidate\n"
                "+corrected\n"
            )
        else:
            patch = (
                "diff --git a/RESULT.txt b/RESULT.txt\n"
                "new file mode 100644\n"
                "index 0000000..5e2e48e\n"
                "--- /dev/null\n"
                "+++ b/RESULT.txt\n"
                "@@ -0,0 +1 @@\n"
                "+candidate\n"
            )
        return WorkerPatch(self.name, work_item.id, patch, "Deterministic candidate or correction.")


def run_three_head_conformance(root: Path) -> ThreeHeadConformanceReport:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    approve, approve_clean = _run_terminal_scenario(root / "approve", "approve")
    escalate, escalate_clean = _run_terminal_scenario(root / "escalate", "escalate")
    reject, rework, rework_clean = _run_rework_scenario(root / "rework")
    critic_disabled, disabled_clean = _run_disabled_scenario(root / "critic-disabled")
    clean = approve_clean and escalate_clean and rework_clean and disabled_clean
    return ThreeHeadConformanceReport(
        ok=approve and reject and rework and escalate and critic_disabled and clean,
        root=root,
        approve=approve,
        reject=reject,
        rework=rework,
        escalate=escalate,
        git_clean=clean,
        critic_disabled=critic_disabled,
    )


def _run_disabled_scenario(repository: Path) -> tuple[bool, bool]:
    _init_repository(repository)
    task = _work_item("critic-disabled")
    store = HohStateStore(repository.parent / "critic-disabled-state")
    store.enqueue(task, source="critic-disabled-conformance")
    store.mark_running(task.id)
    started = datetime.now(UTC).isoformat()
    result = Supervisor(PolicyVerifier()).execute_work_item(repository, task, CorrectionAwareWorker())
    store.record_result(task.id, started, result, review_required=False)
    lifecycle = store.list_tasks()[0]
    return (
        result.ok and lifecycle.status == "done" and not store.review_bundles(),
        _git(repository, "status", "--porcelain").strip() == "",
    )


def _run_terminal_scenario(repository: Path, decision: str) -> tuple[bool, bool]:
    store, task, bundle = _candidate(repository, f"three-head-{decision}")
    _import_decision(repository, store, bundle.payload, decision, f"decision-{decision}")
    lifecycle = store.list_tasks()[0]
    expected = "done" if decision == "approve" else "escalated"
    return lifecycle.status == expected, _git(repository, "status", "--porcelain").strip() == ""


def _run_rework_scenario(repository: Path) -> tuple[bool, bool, bool]:
    store, task, first_bundle = _candidate(repository, "three-head-rework")
    rejected = _import_decision(repository, store, first_bundle.payload, "reject", "decision-reject")
    rejected_task = store.list_tasks()[0]
    if rejected.correction_brief is None:
        raise ThreeHeadConformanceError("Reject decision did not persist a correction brief.")
    queued = store.confirm_rework(
        task.id,
        rejected.decision_id,
        rejected.correction_brief.instructions,
        max_attempts=3,
    )
    store.mark_running(task.id)
    started = datetime.now(UTC).isoformat()
    result = Supervisor(PolicyVerifier()).execute_work_item(repository, queued.work_item, CorrectionAwareWorker())
    run = store.record_result(task.id, started, result, review_required=True)
    if not run.ok:
        raise ThreeHeadConformanceError("Correction attempt did not produce a committed candidate.")
    second_bundle = export_review_bundle(repository, store, run.run_id, three_head=_config())
    store.attach_review_bundle(task.id, second_bundle.record.bundle_id)
    _import_decision(repository, store, second_bundle.payload, "approve", "decision-rework-approve")
    final = store.list_tasks()[0]
    content = (repository / "RESULT.txt").read_text(encoding="utf-8").strip()
    return (
        rejected_task.status == "rework_required",
        final.status == "done" and final.attempts == 2 and content == "corrected",
        _git(repository, "status", "--porcelain").strip() == "",
    )


def _candidate(repository: Path, task_id: str):
    _init_repository(repository)
    task = _work_item(task_id)
    store = HohStateStore(repository.parent / f"{repository.name}-state")
    store.enqueue(task, source="three-head-conformance")
    store.mark_running(task.id)
    started = datetime.now(UTC).isoformat()
    result = Supervisor(PolicyVerifier()).execute_work_item(repository, task, CorrectionAwareWorker())
    run = store.record_result(task.id, started, result, review_required=True)
    if not run.ok:
        raise ThreeHeadConformanceError("Initial attempt did not produce a committed candidate.")
    bundle = export_review_bundle(repository, store, run.run_id, three_head=_config())
    store.attach_review_bundle(task.id, bundle.record.bundle_id)
    return store, task, bundle


def _work_item(task_id: str) -> WorkItem:
    return WorkItem(
        id=task_id,
        title="Three-head conformance candidate",
        objective="Produce a candidate that only the critic may close.",
        acceptance_criteria=("RESULT.txt exists.",),
        verification_commands=(
            f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'RESULT.txt\').exists()"',
        ),
        allowed_paths=("RESULT.txt",),
        non_goals=("Worker and critic must not commit or merge.",),
    )


def _import_decision(repository: Path, store: HohStateStore, bundle: dict, decision: str, decision_id: str):
    payload = _decision_payload(bundle, decision, decision_id)
    validate_critic_decision(payload)
    path = store.root / f"{decision_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_json(payload), encoding="utf-8")
    return import_verifier_decision(repository, store, path)


def _decision_payload(bundle: dict, decision: str, decision_id: str) -> dict:
    findings = [] if decision == "approve" else [
        {"code": "CONFORMANCE", "severity": "blocker" if decision == "escalate" else "error", "message": "Deterministic critic finding."}
    ]
    return {
        "protocol": PROTOCOL_NAMESPACE,
        "protocol_version": "2.0",
        "message_type": "critic.decision",
        "decision_id": decision_id,
        "bundle_id": bundle["bundle_id"],
        "bundle_sha256": bundle["integrity"]["payload_sha256"],
        "critic": {"identity": "conformance-critic", "kind": "agent"},
        "decision": decision,
        "summary": f"Deterministic {decision} decision.",
        "findings": findings,
        "correction_brief": {
            "rationale": "The first candidate requires the recorded correction.",
            "instructions": ["Replace candidate with corrected without changing task scope."],
            "validation_focus": ["Verify RESULT.txt contains corrected."],
        } if decision == "reject" else None,
        "escalation": {
            "reason": "A simulated business choice is required.",
            "question": "Choose the authoritative behavior.",
            "options": ["реши сам", "stop", "свой"],
        } if decision == "escalate" else None,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "attestation": {
            "reviewed_commit": bundle["artifact"]["commit"],
            "reviewed_patch_sha256": bundle["artifact"]["patch_sha256"],
        },
    }


def _config() -> ThreeHeadConfig:
    return ThreeHeadConfig(
        mode="required",
        max_attempts=3,
        logic=RoleIdentityConfig("conformance-logic", "cloud", "logic-model"),
        worker=RoleIdentityConfig("conformance-worker", "local", "worker-model"),
        critic=RoleIdentityConfig("conformance-critic", "cloud", "critic-model"),
    )


def _init_repository(repository: Path) -> None:
    repository.mkdir(parents=True, exist_ok=True)
    _git(repository, "init")
    _git(repository, "config", "user.email", "three-head@example.local")
    _git(repository, "config", "user.name", "HoH Three Head Conformance")
    (repository / ".gitignore").write_text(".tmp\n", encoding="utf-8")
    _git(repository, "add", ".gitignore")
    _git(repository, "commit", "-m", "Initial commit")


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repository, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise ThreeHeadConformanceError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout
