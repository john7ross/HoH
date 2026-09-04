from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any

from .config import HarnessConfig
from .critic_adapters import CriticAdapterError, create_critic_adapter
from .model_providers import ModelProviderError
from .supervisor_adapters import SupervisorAdapterError, create_supervisor_adapter


@dataclass(frozen=True)
class RoleConformanceCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class RoleConformanceReport:
    repository: Path
    role: str
    driver: str
    adapter_name: str | None
    checks: tuple[RoleConformanceCheck, ...]
    evidence: dict[str, Any]

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(item.ok for item in self.checks)


def run_supervisor_conformance(repository: Path, config: HarnessConfig) -> RoleConformanceReport:
    _init_repository(repository, "Supervisor")
    checks: list[RoleConformanceCheck] = []
    try:
        adapter = create_supervisor_adapter(
            config.supervisor,
            config.supervisor_model,
            config.three_head.logic,
        )
        checks.append(RoleConformanceCheck("adapter_created", True, f"name={adapter.name}"))
        spec, evidence = adapter.plan(repository, _supervisor_requirements())
    except (OSError, ValueError, ModelProviderError, SupervisorAdapterError) as exc:
        checks.append(RoleConformanceCheck("supervisor_turn", False, str(exc)))
        return RoleConformanceReport(
            repository,
            "supervisor",
            config.supervisor.driver,
            locals().get("adapter").name if "adapter" in locals() else None,
            tuple(checks),
            {},
        )
    checks.extend(
        (
            RoleConformanceCheck(
                "validated_project_spec",
                bool(spec.tasks and spec.definition_of_done and spec.business_requirements),
                f"project_id={spec.id} tasks={len(spec.tasks)}",
            ),
            RoleConformanceCheck(
                "structured_evidence",
                len(evidence.request_sha256) == 64 and len(evidence.response_sha256) == 64,
                f"endpoint={evidence.endpoint} attempts={evidence.attempts}",
            ),
            _clean_check(repository),
        )
    )
    return RoleConformanceReport(
        repository,
        "supervisor",
        config.supervisor.driver,
        adapter.name,
        tuple(checks),
        {
            "project_id": spec.id,
            "task_count": len(spec.tasks),
            "provider": evidence.provider,
            "model": evidence.model,
            "endpoint": evidence.endpoint,
            "attempts": evidence.attempts,
            "request_sha256": evidence.request_sha256,
            "response_sha256": evidence.response_sha256,
        },
    )


def run_critic_conformance(repository: Path, config: HarnessConfig) -> RoleConformanceReport:
    _init_repository(repository, "Critic")
    checks: list[RoleConformanceCheck] = []
    if not config.critic.automatic:
        return RoleConformanceReport(
            repository,
            "critic",
            config.critic.driver or config.critic.type,
            None,
            (RoleConformanceCheck("automatic_transport", False, "manual Critic has no live transport"),),
            {},
        )
    try:
        adapter = create_critic_adapter(
            config.critic,
            config.three_head.critic,
            config.verifier_model,
        )
        checks.append(RoleConformanceCheck("adapter_created", True, f"name={adapter.name}"))
        decision = adapter.review(repository, _critic_bundle())
    except (OSError, ValueError, CriticAdapterError) as exc:
        checks.append(RoleConformanceCheck("critic_turn", False, str(exc)))
        return RoleConformanceReport(
            repository,
            "critic",
            config.critic.driver or config.critic.type,
            locals().get("adapter").name if "adapter" in locals() else None,
            tuple(checks),
            {},
        )
    checks.extend(
        (
            RoleConformanceCheck(
                "validated_decision",
                decision.get("decision") in {"approve", "reject", "escalate"},
                f"decision={decision.get('decision')} decision_id={decision.get('decision_id')}",
            ),
            RoleConformanceCheck(
                "canonical_attestation",
                decision.get("attestation", {}).get("reviewed_commit") == "b" * 40
                and decision.get("attestation", {}).get("reviewed_patch_sha256") == "c" * 64,
                "decision attests the supplied immutable commit and patch hashes",
            ),
            _clean_check(repository),
        )
    )
    return RoleConformanceReport(
        repository,
        "critic",
        config.critic.driver or config.critic.type,
        adapter.name,
        tuple(checks),
        {
            "decision": decision["decision"],
            "decision_id": decision["decision_id"],
            "bundle_id": decision["bundle_id"],
            "bundle_sha256": decision["bundle_sha256"],
            "reviewed_commit": decision["attestation"]["reviewed_commit"],
            "reviewed_patch_sha256": decision["attestation"]["reviewed_patch_sha256"],
        },
    )


def _supervisor_requirements() -> str:
    return (
        "Create a minimal, atomic plan for adding HARNESS_DEMO.md to an existing Git project. "
        "The plan must include business requirements, definition of done, documentation, one task, "
        "allowed path HARNESS_DEMO.md, a verification command, and no Git authority for the agent."
    )


def _critic_bundle() -> dict[str, Any]:
    return {
        "protocol": "hoh.protocol",
        "protocol_version": "2.0",
        "message_type": "critic.review_bundle",
        "bundle_id": "role-conformance-bundle",
        "task": {
            "id": "role-conformance",
            "objective": "Create HARNESS_DEMO.md.",
            "acceptance_criteria": ["HARNESS_DEMO.md exists."],
            "verification_commands": ["test HARNESS_DEMO.md"],
            "allowed_paths": ["HARNESS_DEMO.md"],
            "non_goals": ["Do not modify any other path."],
        },
        "artifact": {
            "commit": "b" * 40,
            "patch_sha256": "c" * 64,
            "changed_files": ["HARNESS_DEMO.md"],
            "diff": "diff --git a/HARNESS_DEMO.md b/HARNESS_DEMO.md\n+conformant\n",
        },
        "verification": {
            "policy_ok": True,
            "commands": [{"command": "test HARNESS_DEMO.md", "return_code": 0}],
        },
        "authority": {
            "critic": "read-only judgment only",
            "supervisor": "canonical Git and state owner",
        },
        "integrity": {"payload_sha256": "a" * 64},
    }


def _init_repository(repository: Path, role: str) -> None:
    repository.mkdir(parents=True, exist_ok=True)
    _git(repository, "init", "-q", check=True)
    _git(repository, "config", "user.email", "conformance@example.local", check=True)
    _git(repository, "config", "user.name", "HoH Conformance", check=True)
    (repository / "README.md").write_text(f"# HoH {role} conformance\n", encoding="utf-8")
    _git(repository, "add", "README.md", check=True)
    _git(repository, "commit", "-q", "-m", "Initial conformance repository", check=True)


def _clean_check(repository: Path) -> RoleConformanceCheck:
    status = _git(repository, "status", "--porcelain", check=True).stdout.strip()
    return RoleConformanceCheck(
        "canonical_worktree_clean",
        status == "",
        "git status --porcelain is empty" if not status else status,
    )


def _git(repository: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ("git", *args),
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed
