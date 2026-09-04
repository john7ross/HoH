from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from .acp_agents import AcpWorkerAdapter
from .command_worker import CommandWorker
from .config import WorkerConfig
from .driver_registry import DriverRegistryError, resolve_driver
from .hermes import HermesAcpAdapter
from .provider_workers import (
    ClaudeCodeWorker,
    OpenClawWorker,
    validate_provider_worker_config,
)
from .process_worker import ProfiledProcessWorker, validate_process_worker_config
from .targets import LocalAgentTarget
from .workers import JobWorker, StubPatchWorker


class WorkerAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerAdapterSpec:
    name: str
    target: LocalAgentTarget
    worker: JobWorker
    isolated_worktree: bool
    task_transport: str
    artifact_contract: str
    supports_subagents: bool
    provider: str = "generic"
    safety_profile: str = "isolated-worktree-diff"


def create_worker_adapter(
    config: WorkerConfig,
    timeout_override: float | None = None,
    event_sink: Callable[[str, dict[str, Any]], None] | None = None,
) -> WorkerAdapterSpec:
    try:
        manifest = resolve_driver(config.driver or config.type, "worker")
    except DriverRegistryError as exc:
        raise WorkerAdapterError(str(exc)) from exc
    worker_type = manifest.adapter
    timeout_seconds = timeout_override if timeout_override is not None else config.timeout_seconds
    if worker_type == "a2a":
        from .a2a_adapters import A2AWorkerAdapter

        target = LocalAgentTarget(
            name="a2a-agent",
            command=config.command,
            args=(),
            environment=config.environment,
        )
        adapter = A2AWorkerAdapter(config, event_sink=event_sink)
        return WorkerAdapterSpec(
            name=adapter.name,
            target=target,
            worker=adapter,
            isolated_worktree=False,
            task_transport=config.capabilities.task_transport,
            artifact_contract=config.capabilities.artifact_contract,
            supports_subagents=False,
            provider="a2a",
            safety_profile=manifest.safety_profile,
        )
    if worker_type == "acp":
        target = LocalAgentTarget(
            name="acp-agent",
            command=config.command,
            args=config.args,
            environment=config.environment,
        )
        adapter = AcpWorkerAdapter(
            target=target,
            turn_timeout_seconds=timeout_seconds,
            model=config.model,
            permission_policy=config.mcp,
            event_sink=event_sink,
        )
        return WorkerAdapterSpec(
            name=adapter.name,
            target=target,
            worker=adapter,
            isolated_worktree=True,
            task_transport=config.capabilities.task_transport,
            artifact_contract=config.capabilities.artifact_contract,
            supports_subagents=config.capabilities.supports_subagents,
            provider="acp",
            safety_profile=manifest.safety_profile,
        )
    if worker_type in {"hermes_acp", "hermes"}:
        target = LocalAgentTarget(
            name="hermes",
            command=config.command,
            args=config.args,
            environment=config.environment,
        )
        adapter = HermesAcpAdapter(
            target=target,
            turn_timeout_seconds=timeout_seconds,
            permission_policy=config.mcp,
            event_sink=event_sink,
        )
        return WorkerAdapterSpec(
            name=adapter.name,
            target=target,
            worker=adapter,
            isolated_worktree=True,
            task_transport=config.capabilities.task_transport,
            artifact_contract=config.capabilities.artifact_contract,
            supports_subagents=config.capabilities.supports_subagents,
            provider="hermes",
            safety_profile=manifest.safety_profile,
        )
    if worker_type == "stub":
        worker = StubPatchWorker()
        target = LocalAgentTarget(
            name="stub",
            command=config.command or "stub",
            args=config.args if config.command else (),
            environment=config.environment,
        )
        return WorkerAdapterSpec(
            name=worker.name,
            target=target,
            worker=worker,
            isolated_worktree=False,
            task_transport=config.capabilities.task_transport,
            artifact_contract=config.capabilities.artifact_contract,
            supports_subagents=config.capabilities.supports_subagents,
            provider="stub",
            safety_profile=manifest.safety_profile,
        )
    if worker_type == "command":
        target = LocalAgentTarget(
            name="command",
            command=config.command,
            args=config.args,
            environment=config.environment,
        )
        worker = CommandWorker(
            target=target,
            timeout_seconds=timeout_seconds,
            event_sink=event_sink,
        )
        return WorkerAdapterSpec(
            name=worker.name,
            target=target,
            worker=worker,
            isolated_worktree=True,
            task_transport=config.capabilities.task_transport,
            artifact_contract=config.capabilities.artifact_contract,
            supports_subagents=config.capabilities.supports_subagents,
            provider="generic",
            safety_profile=manifest.safety_profile,
        )
    if worker_type == "process":
        if config.process_profile is None:
            raise WorkerAdapterError("Process worker requires worker.process_profile.")
        try:
            validate_process_worker_config(config.args, config.process_profile)
        except ValueError as exc:
            raise WorkerAdapterError(str(exc)) from exc
        target = LocalAgentTarget(
            name=f"process-{config.process_profile.profile_id}",
            command=config.command,
            args=config.args,
            environment=config.environment,
        )
        worker = ProfiledProcessWorker(
            target=target,
            profile=config.process_profile,
            timeout_seconds=timeout_seconds,
            model=config.model,
            event_sink=event_sink,
        )
        return WorkerAdapterSpec(
            name=worker.name,
            target=target,
            worker=worker,
            isolated_worktree=True,
            task_transport=config.capabilities.task_transport,
            artifact_contract=config.capabilities.artifact_contract,
            supports_subagents=False,
            provider=f"process:{config.process_profile.profile_id}",
            safety_profile=manifest.safety_profile,
        )
    if worker_type in {"claude_code", "claude"}:
        try:
            validate_provider_worker_config(config)
        except ValueError as exc:
            raise WorkerAdapterError(str(exc)) from exc
        target = LocalAgentTarget(
            name="claude-code",
            command=config.command,
            args=config.args,
            environment=config.environment,
        )
        worker = ClaudeCodeWorker(
            target=target,
            timeout_seconds=timeout_seconds,
            model=config.model,
            max_budget_usd=config.max_budget_usd,
            event_sink=event_sink,
        )
        return WorkerAdapterSpec(
            name=worker.name,
            target=target,
            worker=worker,
            isolated_worktree=True,
            task_transport=config.capabilities.task_transport,
            artifact_contract=config.capabilities.artifact_contract,
            supports_subagents=False,
            provider="claude",
            safety_profile=manifest.safety_profile,
        )
    if worker_type == "openclaw":
        try:
            validate_provider_worker_config(config)
        except ValueError as exc:
            raise WorkerAdapterError(str(exc)) from exc
        target = LocalAgentTarget(
            name="openclaw",
            command=config.command,
            args=config.args,
            environment=config.environment,
        )
        worker = OpenClawWorker(
            target=target,
            timeout_seconds=timeout_seconds,
            model=config.model or "",
            thinking=config.thinking,
            event_sink=event_sink,
        )
        return WorkerAdapterSpec(
            name=worker.name,
            target=target,
            worker=worker,
            isolated_worktree=True,
            task_transport=config.capabilities.task_transport,
            artifact_contract=config.capabilities.artifact_contract,
            supports_subagents=False,
            provider="openclaw",
            safety_profile=manifest.safety_profile,
        )
    raise WorkerAdapterError(f"Unsupported worker driver adapter: {manifest.adapter}")


def with_timeout(config: WorkerConfig, timeout_seconds: float | None) -> WorkerConfig:
    if timeout_seconds is None:
        return config
    return replace(config, timeout_seconds=timeout_seconds)
