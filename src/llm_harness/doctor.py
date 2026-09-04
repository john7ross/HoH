from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from shutil import which
import subprocess
from typing import Mapping

from .config import HarnessConfig
from .critic_adapters import CriticAdapterError, create_critic_adapter
from .runtime import inspect_runtime
from .scanner import ModelEndpointProbe, Resolver, scan_agents, scan_local_models
from .worker_adapters import WorkerAdapterError, create_worker_adapter
from .language_audit import ANALYZER_RUNNERS
from .model_providers import model_endpoint_preflight
from .operations import OperationLedger


SUPPORTED_WORKER_ARTIFACT_CONTRACTS = {"worktree_diff", "direct_patch"}


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    warning: bool = False
    next_step: str = ""

    @property
    def state(self) -> str:
        if not self.ok:
            return "failed"
        return "warning" if self.warning else "ok"


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def warnings(self) -> tuple[DoctorCheck, ...]:
        return tuple(check for check in self.checks if check.ok and check.warning)

    @property
    def next_steps(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(check.next_step for check in self.checks if check.next_step))

    @property
    def ready_for_real_work(self) -> bool:
        """`ok` alone can mean "the placeholders are intact"; this also requires no warnings."""
        return self.ok and not self.warnings

    def to_text(self) -> str:
        lines = [f"ok={self.ok} warnings={len(self.warnings)}"]
        for check in self.checks:
            lines.append(f"check={check.name} state={check.state} detail={check.detail}")
        for step in self.next_steps:
            lines.append(f"next_step={step}")
        return "\n".join(lines) + "\n"


def run_doctor(
    project_root: Path,
    config: HarnessConfig,
    env: Mapping[str, str] | None = None,
    agent_resolver: Resolver | None = None,
    model_probe: ModelEndpointProbe | None = None,
) -> DoctorReport:
    environment = env if env is not None else os.environ
    checks: list[DoctorCheck] = []

    checks.append(_check_git(project_root))
    checks.append(_check_runtime(project_root, config))
    checks.append(_check_supervisor(config, agent_resolver))
    checks.append(_check_worker(config, agent_resolver))
    checks.append(_check_scheduler(config))
    checks.append(_check_coordination(config))
    checks.append(_check_reconciliation(project_root))
    checks.append(_check_audit_analyzers(config))
    checks.append(_check_critic(config, agent_resolver, environment))
    checks.extend(
        _check_mcp(role, getattr(config, role), environment, agent_resolver)
        for role in ("supervisor", "worker", "critic")
    )
    checks.append(_check_three_head(config))
    checks.append(_check_agents(config, agent_resolver))
    checks.append(_check_local_models(config, model_probe))
    checks.append(
        _check_model_role("supervisor_model", config.supervisor_model, environment)
        if config.supervisor.driver == "model_json"
        else DoctorCheck("supervisor_model", True, "unused by configured supervisor process driver")
    )
    checks.append(_check_model_role("verifier_model", config.verifier_model, environment))
    checks.append(_check_telegram(config, environment))

    return DoctorReport(checks=tuple(checks))


def _check_git(project_root: Path) -> DoctorCheck:
    completed = subprocess.run(
        ["git", "--version"],
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0:
        return DoctorCheck("git", True, completed.stdout.strip())
    return DoctorCheck("git", False, completed.stderr.strip() or "git executable is not available")


def _check_runtime(project_root: Path, config: HarnessConfig) -> DoctorCheck:
    status = inspect_runtime(project_root, config.runtime)
    if status.ok:
        return DoctorCheck("runtime", True, "embedded runtime layout is present")
    if not config.runtime.require_embedded_python:
        return DoctorCheck("runtime", True, "embedded runtime is not required by config")
    return DoctorCheck(
        "runtime",
        False,
        "; ".join(status.findings),
        next_step=(
            "The embedded runtime belongs to the HoH installation, not to this project. "
            "An installed package ships it; a source checkout generates it with "
            "scripts/bootstrap-runtime.ps1 (Windows) or scripts/bootstrap-runtime.sh. "
            "Set runtime.require_embedded_python to false to use the system Python instead."
        ),
    )


def _check_scheduler(config: HarnessConfig) -> DoctorCheck:
    configured = config.scheduler.max_parallel_tasks
    barriers: list[str] = []
    if not config.worker.capabilities.requires_isolated_worktree:
        barriers.append("worker_not_isolated")
    if config.three_head.required:
        barriers.append("three_head_required")
    effective = 1 if barriers else configured
    return DoctorCheck(
        "scheduler",
        True,
        (
            f"configured_parallelism={configured} effective_parallelism={effective} "
            f"resource_policy=allowed_paths barriers={','.join(barriers) or 'none'}"
        ),
    )


def _check_coordination(config: HarnessConfig) -> DoctorCheck:
    backend = "msvcrt.locking" if os.name == "nt" else "fcntl.flock"
    policy = config.coordination
    return DoctorCheck(
        "coordination",
        True,
        (
            f"backend={backend} "
            f"state_timeout_seconds={policy.state_lock_timeout_seconds} "
            f"execution_timeout_seconds={policy.execution_lock_timeout_seconds} "
            f"poll_interval_seconds={policy.poll_interval_seconds} "
            "stale_recovery=os_lock_only"
        ),
    )


def _check_reconciliation(project_root: Path) -> DoctorCheck:
    report = OperationLedger(project_root).reconcile(auto_complete_state=False)
    if report.ok:
        return DoctorCheck("reconciliation", True, f"operations={len(report.items)} pending=0")
    detail = ",".join(
        f"{item.operation_id}:{item.classification}" for item in report.blocking
    )
    return DoctorCheck(
        "reconciliation",
        False,
        f"operations={len(report.items)} pending={len(report.blocking)} details={detail}",
    )


def _check_audit_analyzers(config: HarnessConfig) -> DoctorCheck:
    configured = config.audit.language_analyzers
    supported = tuple(ANALYZER_RUNNERS)
    unsupported = tuple(item for item in configured if item not in supported)
    if unsupported:
        return DoctorCheck(
            "audit_analyzers",
            False,
            f"unsupported={','.join(unsupported)} supported={','.join(supported)}",
        )
    return DoctorCheck(
        "audit_analyzers",
        True,
        (
            f"configured={','.join(configured) or 'none'} "
            f"entry_points={len(config.audit.entry_points)} "
            f"exclude_paths={len(config.audit.exclude_paths)} "
            f"fail_on_unavailable={config.audit.fail_on_unavailable}"
        ),
    )


def _check_agents(config: HarnessConfig, agent_resolver: Resolver | None) -> DoctorCheck:
    probes = scan_agents(config.agents, resolver=agent_resolver) if agent_resolver else scan_agents(config.agents)
    available = tuple(probe.name for probe in probes if probe.available)
    missing = tuple(probe.name for probe in probes if not probe.available)
    if available:
        detail = f"available={','.join(available)}"
        if missing:
            detail += f" missing={','.join(missing)}"
        return DoctorCheck("agents", True, detail)
    if not probes:
        return DoctorCheck("agents", False, "no agent candidates configured")
    return DoctorCheck("agents", False, "no configured agent executable is available")


def _check_worker(config: HarnessConfig, agent_resolver: Resolver | None) -> DoctorCheck:
    try:
        adapter = create_worker_adapter(config.worker)
    except WorkerAdapterError as exc:
        return DoctorCheck("worker", False, str(exc))

    capabilities = config.worker.capabilities
    if capabilities.artifact_contract not in SUPPORTED_WORKER_ARTIFACT_CONTRACTS:
        return DoctorCheck(
            "worker",
            False,
            f"driver={config.worker.driver} unsupported artifact_contract={capabilities.artifact_contract}",
        )
    if capabilities.requires_isolated_worktree != adapter.isolated_worktree:
        return DoctorCheck(
            "worker",
            False,
            (
                f"driver={config.worker.driver} manifest_requires_isolated_worktree="
                f"{capabilities.requires_isolated_worktree} adapter_isolated_worktree={adapter.isolated_worktree}"
            ),
        )

    worker_driver = str(config.worker.driver).casefold()
    if worker_driver == "stub":
        return DoctorCheck(
            "worker",
            True,
            (
                "driver=stub executable_check=skipped "
                f"artifact_contract={capabilities.artifact_contract} "
                f"requires_isolated_worktree={capabilities.requires_isolated_worktree} "
                "in-process test driver that cannot do real work"
            ),
            warning=True,
            next_step=(
                "Configure a real Worker agent under [worker]; the stub driver only runs "
                "deterministic tests and demos."
            ),
        )

    if not config.worker.command:
        return DoctorCheck("worker", False, f"driver={config.worker.driver} command is required")

    resolver = agent_resolver or which
    path = resolver(config.worker.command)
    if path is None:
        return DoctorCheck("worker", False, f"driver={config.worker.driver} missing command={config.worker.command}")
    return DoctorCheck(
        "worker",
        True,
        (
            f"driver={config.worker.driver} command={config.worker.command} path={path} "
            f"provider={adapter.provider} safety_profile={adapter.safety_profile} "
            f"artifact_contract={capabilities.artifact_contract} "
            f"requires_isolated_worktree={capabilities.requires_isolated_worktree} "
            f"supports_subagents={capabilities.supports_subagents}"
        ),
    )


def _check_supervisor(config: HarnessConfig, agent_resolver: Resolver | None) -> DoctorCheck:
    if config.supervisor.driver == "model_json":
        return DoctorCheck("supervisor", True, "driver=model_json structured provider boundary")
    resolver = agent_resolver or which
    path = resolver(config.supervisor.command)
    if path is None:
        return DoctorCheck(
            "supervisor",
            False,
            f"driver={config.supervisor.driver} missing command={config.supervisor.command}",
        )
    return DoctorCheck(
        "supervisor",
        True,
        (
            f"driver={config.supervisor.driver} command={config.supervisor.command} "
            f"path={path} timeout_seconds={config.supervisor.timeout_seconds}"
        ),
    )


def _check_local_models(config: HarnessConfig, model_probe: ModelEndpointProbe | None) -> DoctorCheck:
    if not config.local_models:
        return DoctorCheck("local_models", True, "no local model endpoints configured")

    probes = scan_local_models(config.local_models, endpoint_probe=model_probe)
    available = tuple(probe.name for probe in probes if probe.available)
    missing = tuple(probe.name for probe in probes if not probe.available)
    if available:
        detail = f"available={','.join(available)}"
        if missing:
            detail += f" missing={','.join(missing)}"
        return DoctorCheck("local_models", True, detail)
    return DoctorCheck("local_models", False, "configured local model endpoints are unavailable")


def _check_model_role(name: str, endpoint, environment: Mapping[str, str]) -> DoctorCheck:
    ok, detail = model_endpoint_preflight(endpoint, environment)
    if ok and str(endpoint.provider).casefold() == "stub":
        return DoctorCheck(
            name,
            True,
            detail + " stand-in that cannot answer a request",
            warning=True,
            next_step=f"Set a real provider and model under [{name}]; the stub provider makes no call.",
        )
    return DoctorCheck(name, ok, detail)


def _check_critic(
    config: HarnessConfig,
    agent_resolver: Resolver | None,
    environment: Mapping[str, str],
) -> DoctorCheck:
    if not config.critic.automatic:
        return DoctorCheck("critic", True, "driver=manual automatic_review=False")
    if not config.three_head.required:
        return DoctorCheck(
            "critic",
            False,
            f"driver={config.critic.driver} requires three_head.mode=required",
        )
    try:
        adapter = create_critic_adapter(config.critic, config.three_head.critic, config.verifier_model)
    except CriticAdapterError as exc:
        return DoctorCheck("critic", False, str(exc))
    if config.critic.driver == "model_json":
        if config.verifier_model.provider == "stub":
            return DoctorCheck(
                "critic",
                False,
                "driver=model_json requires a non-stub verifier_model provider",
            )
        ok, detail = model_endpoint_preflight(config.verifier_model, environment)
        return DoctorCheck(
            "critic",
            ok,
            f"driver=model_json adapter={adapter.name} {detail}",
        )
    resolver = agent_resolver or which
    path = resolver(config.critic.command)
    if path is None:
        return DoctorCheck(
            "critic",
            False,
            f"driver={config.critic.driver} missing command={config.critic.command}",
        )
    return DoctorCheck(
        "critic",
        True,
        (
            f"driver={config.critic.driver} adapter={adapter.name} "
            f"command={config.critic.command} path={path} "
            f"timeout_seconds={config.critic.timeout_seconds}"
        ),
    )


def _check_three_head(config: HarnessConfig) -> DoctorCheck:
    policy = config.three_head
    roles = (policy.logic, policy.worker, policy.critic)
    identities = ",".join(role.identity for role in roles)
    fingerprints = ",".join(f"{role.provider}/{role.model}" for role in roles)
    return DoctorCheck(
        "three_head",
        True,
        f"mode={policy.mode} max_attempts={policy.max_attempts} identities={identities} models={fingerprints}",
    )


def _check_mcp(
    role: str,
    role_config,
    environment: Mapping[str, str],
    agent_resolver: Resolver | None,
) -> DoctorCheck:
    policy = role_config.mcp
    driver = str(role_config.driver).casefold()
    active = driver in {"acp", "hermes_acp"}
    if not active:
        detail = (
            f"inactive driver={driver} configured_servers={len(policy.servers)} "
            f"permission_mode={policy.permission_mode}"
        )
        return DoctorCheck(f"mcp_{role}", not policy.servers, detail)
    missing_environment = sorted(
        {
            binding.source_env
            for server in policy.servers
            for binding in (*server.environment, *server.headers)
            if not environment.get(binding.source_env)
        }
    )
    if missing_environment:
        return DoctorCheck(
            f"mcp_{role}",
            False,
            "missing env=" + ",".join(missing_environment),
        )
    resolver = agent_resolver or which
    missing_commands = sorted(
        server.command or ""
        for server in policy.servers
        if server.transport == "stdio" and resolver(server.command or "") is None
    )
    if missing_commands:
        return DoctorCheck(
            f"mcp_{role}",
            False,
            "missing command=" + ",".join(missing_commands),
        )
    transports = ",".join(server.transport for server in policy.servers) or "none"
    return DoctorCheck(
        f"mcp_{role}",
        True,
        (
            f"permission_mode={policy.permission_mode} "
            f"allowed_kinds={','.join(policy.allowed_tool_kinds) or 'none'} "
            f"servers={len(policy.servers)} transports={transports}"
        ),
    )


def _check_telegram(config: HarnessConfig, env: Mapping[str, str]) -> DoctorCheck:
    telegram = config.telegram
    if not telegram.enabled:
        return DoctorCheck("telegram", True, "disabled")
    if not telegram.bot_token_env:
        return DoctorCheck("telegram", False, "bot_token_env is required")
    if not telegram.chat_id_env:
        return DoctorCheck("telegram", False, "chat_id_env is required")
    if not telegram.user_id_env:
        return DoctorCheck("telegram", False, "user_id_env is required")
    missing = tuple(
        name
        for name in (telegram.bot_token_env, telegram.chat_id_env, telegram.user_id_env)
        if not env.get(name)
    )
    if missing:
        return DoctorCheck("telegram", False, "missing env=" + ",".join(missing))
    return DoctorCheck("telegram", True, "configured")
