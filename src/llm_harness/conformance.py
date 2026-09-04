from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys

from .config import HarnessConfig
from .domain import HarnessRunResult, WorkItem
from .supervisor import Supervisor
from .trust import WorkerTrustPolicy
from .verifier import PolicyVerifier
from .worker_adapters import WorkerAdapterError, create_worker_adapter, with_timeout


SUPPORTED_ARTIFACT_CONTRACTS = {"worktree_diff", "direct_patch"}
SUPPORTED_TASK_TRANSPORTS = {"acp_stdio", "stdin_prompt", "profiled_process", "in_process"}


@dataclass(frozen=True)
class WorkerConformanceCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class WorkerConformanceReport:
    repository: Path
    worker_type: str
    adapter_name: str | None
    checks: tuple[WorkerConformanceCheck, ...]
    run_result: HarnessRunResult | None = None

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks) and self.run_result is not None and self.run_result.ok


def run_worker_conformance(
    repository: Path,
    config: HarnessConfig,
    timeout_seconds: float | None = None,
) -> WorkerConformanceReport:
    _init_repository(repository)

    worker_config = with_timeout(config.worker, timeout_seconds)
    checks: list[WorkerConformanceCheck] = []
    try:
        adapter = create_worker_adapter(worker_config)
    except WorkerAdapterError as exc:
        return WorkerConformanceReport(
            repository=repository,
            worker_type=worker_config.type,
            adapter_name=None,
            checks=(
                WorkerConformanceCheck(
                    name="adapter_created",
                    ok=False,
                    detail=str(exc),
                ),
            ),
        )

    checks.append(
        WorkerConformanceCheck(
            name="adapter_created",
            ok=True,
            detail=(
                f"name={adapter.name} provider={adapter.provider} "
                f"safety_profile={adapter.safety_profile}"
            ),
        )
    )
    checks.append(
        WorkerConformanceCheck(
            name="task_transport_supported",
            ok=adapter.task_transport in SUPPORTED_TASK_TRANSPORTS,
            detail=f"task_transport={adapter.task_transport}",
        )
    )
    checks.append(
        WorkerConformanceCheck(
            name="artifact_contract_supported",
            ok=adapter.artifact_contract in SUPPORTED_ARTIFACT_CONTRACTS,
            detail=f"artifact_contract={adapter.artifact_contract}",
        )
    )
    checks.append(
        WorkerConformanceCheck(
            name="isolation_matches_manifest",
            ok=worker_config.capabilities.requires_isolated_worktree == adapter.isolated_worktree,
            detail=(
                f"manifest_requires_isolated_worktree="
                f"{worker_config.capabilities.requires_isolated_worktree} "
                f"adapter_isolated_worktree={adapter.isolated_worktree}"
            ),
        )
    )

    work_item = _conformance_work_item()
    supervisor = Supervisor(
        PolicyVerifier(trust_policy=WorkerTrustPolicy(config.worker_trust_level)),
        command_policy=config.verification.policy(),
    )
    if adapter.isolated_worktree:
        attempts_root = repository.parent / f".{repository.name}-attempts"
        try:
            run_result = supervisor.dispatch_work_item_in_attempt(
                repository,
                work_item,
                adapter.target,
                adapter.worker,
                attempts_root=attempts_root,
            )
        finally:
            shutil.rmtree(attempts_root, ignore_errors=True)
    else:
        run_result = supervisor.dispatch_work_item(
            repository,
            work_item,
            adapter.target,
            adapter.worker,
        )

    checks.append(
        WorkerConformanceCheck(
            name="supervisor_run_ok",
            ok=run_result.ok,
            detail=f"commit={run_result.commit}",
        )
    )
    checks.append(
        WorkerConformanceCheck(
            name="canonical_worktree_clean",
            ok=_git(repository, "status", "--porcelain").stdout.strip() == "",
            detail="git status --porcelain is empty",
        )
    )
    return WorkerConformanceReport(
        repository=repository,
        worker_type=worker_config.type,
        adapter_name=adapter.name,
        checks=tuple(checks),
        run_result=run_result,
    )


def _conformance_work_item() -> WorkItem:
    return WorkItem(
        id="worker-conformance",
        title="Worker adapter conformance",
        objective=(
            "Create HARNESS_DEMO.md with a short confirmation that this worker can produce "
            "candidate repository changes for HoH."
        ),
        acceptance_criteria=("HARNESS_DEMO.md exists after supervisor-owned apply.",),
        verification_commands=(
            f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'HARNESS_DEMO.md\').exists()"',
        ),
        allowed_paths=("HARNESS_DEMO.md",),
        non_goals=(
            "Do not commit, branch, merge, push, or decide readiness.",
            "Do not change files outside HARNESS_DEMO.md.",
        ),
    )


def _init_repository(repository: Path) -> None:
    repository.mkdir(parents=True, exist_ok=True)
    _git(repository, "init", check=True)
    _git(repository, "config", "user.email", "conformance@example.local", check=True)
    _git(repository, "config", "user.name", "HoH Conformance", check=True)
    readme = repository / "README.md"
    if not readme.exists():
        readme.write_text("# HoH worker conformance\n", encoding="utf-8")
    _git(repository, "add", "README.md", check=True)
    _git(repository, "commit", "-m", "Initial conformance repository", check=True)


def _git(
    repository: Path,
    *args: str,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed
