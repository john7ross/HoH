from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Any

from .acp_registry import AcpRegistry, find_registry_agent, load_acp_registry, resolve_acp_launch
from .config import (
    A2AAgentConfig,
    AgentConfig,
    CloudModelEndpoint,
    CriticConfig,
    HarnessConfig,
    RoleIdentityConfig,
    SupervisorConfig,
    ThreeHeadConfig,
    WorkerConfig,
)
from .durable_io import atomic_write_text


ROLE_PROFILE_VERSION = "2.0"
SUPPORTED_ROLE_PROFILE_VERSIONS = {"1.0", ROLE_PROFILE_VERSION}
ROLE_PROFILE_RELATIVE_PATH = Path(".hoh") / "role-profile.json"
DIRECT_SUPERVISOR_MODEL_AGENT = "direct-supervisor-model"
DIRECT_CRITIC_MODEL_AGENT = "direct-critic-model"


class RoleProfileError(ValueError):
    pass


@dataclass(frozen=True)
class RoleSelection:
    agent: str
    model: str | None = None
    driver: str | None = None

    def __post_init__(self) -> None:
        if not self.agent.strip():
            raise RoleProfileError("Role agent must be non-empty.")
        if self.model is not None and not self.model.strip():
            raise RoleProfileError("Role model must be non-empty when provided.")
        if self.driver is not None and not self.driver.strip():
            raise RoleProfileError("Role driver must be non-empty when provided.")


@dataclass(frozen=True)
class ProjectRoleProfile:
    supervisor: RoleSelection
    worker: RoleSelection
    critic: RoleSelection | None = None
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise RoleProfileError("Role profile max_attempts must be at least 1.")

    @property
    def critic_enabled(self) -> bool:
        return self.critic is not None


def role_profile_path(project_root: Path) -> Path:
    return project_root.resolve() / ROLE_PROFILE_RELATIVE_PATH


def write_role_profile(project_root: Path, profile: ProjectRoleProfile) -> Path:
    path = role_profile_path(project_root)
    atomic_write_text(
        path,
        json.dumps(role_profile_payload(profile), indent=2, ensure_ascii=False) + "\n",
    )
    return path


def load_role_profile(project_root: Path) -> ProjectRoleProfile | None:
    path = role_profile_path(project_root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RoleProfileError(f"Invalid role profile JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RoleProfileError("Role profile must contain a JSON object.")
    if payload.get("profile_version") not in SUPPORTED_ROLE_PROFILE_VERSIONS:
        raise RoleProfileError("Unsupported role profile version.")
    roles = payload.get("roles")
    if not isinstance(roles, dict):
        raise RoleProfileError("Role profile roles must be an object.")
    return ProjectRoleProfile(
        supervisor=_selection(roles.get("supervisor"), "supervisor"),
        worker=_selection(roles.get("worker"), "worker"),
        critic=_selection(roles.get("critic"), "critic") if roles.get("critic") is not None else None,
        max_attempts=int(payload.get("max_attempts", 3)),
    )


def role_profile_payload(profile: ProjectRoleProfile) -> dict[str, Any]:
    return {
        "profile_version": ROLE_PROFILE_VERSION,
        "critic_enabled": profile.critic_enabled,
        "max_attempts": profile.max_attempts,
        "roles": {
            "supervisor": _selection_payload(profile.supervisor),
            "worker": _selection_payload(profile.worker),
            "critic": _selection_payload(profile.critic) if profile.critic else None,
        },
    }


def apply_role_profile(
    base: HarnessConfig,
    profile: ProjectRoleProfile,
    project_id: str = "project",
    registry: AcpRegistry | None = None,
) -> HarnessConfig:
    registry = registry if registry is not None else load_acp_registry()
    supervisor_model = base.supervisor_model
    verifier_model = base.verifier_model
    if (
        profile.supervisor.agent.casefold() == DIRECT_SUPERVISOR_MODEL_AGENT
        and profile.supervisor.model is not None
    ):
        supervisor_model = replace(supervisor_model, model=profile.supervisor.model)
    if (
        profile.critic is not None
        and profile.critic.agent.casefold() == DIRECT_CRITIC_MODEL_AGENT
        and profile.critic.model is not None
    ):
        verifier_model = replace(verifier_model, model=profile.critic.model)
    supervisor = base.supervisor
    if replace(base.supervisor, mcp=SupervisorConfig().mcp) == SupervisorConfig():
        if profile.supervisor.agent.casefold() == DIRECT_SUPERVISOR_MODEL_AGENT:
            supervisor = SupervisorConfig(
                driver="model_json",
                timeout_seconds=base.supervisor.timeout_seconds,
                mcp=base.supervisor.mcp,
            )
        else:
            supervisor_agent = _resolve_agent(base, profile.supervisor.agent, "supervisor", registry)
            supervisor_driver = profile.supervisor.driver or supervisor_agent.supervisor_driver
            if supervisor_driver is None:
                raise RoleProfileError(
                    f"Agent '{supervisor_agent.name}' cannot serve as Supervisor: no supervisor driver is declared."
                )
            supervisor = SupervisorConfig(
                driver=supervisor_driver,
                command=supervisor_agent.command,
                args=supervisor_agent.args,
                environment=supervisor_agent.environment,
                timeout_seconds=base.supervisor.timeout_seconds,
                model=profile.supervisor.model,
                mcp=base.supervisor.mcp,
            )
    if replace(base.worker, mcp=WorkerConfig().mcp) != WorkerConfig():
        # The role profile selects who performs the role. An explicit worker
        # configuration selects how HoH transports the task to that agent and
        # must not be replaced by name-based inference (for example, Hermes
        # through the generic command adapter instead of ACP).
        worker = base.worker
    else:
        worker_agent = _resolve_agent(base, profile.worker.agent, "worker", registry)
        worker_driver = profile.worker.driver or worker_agent.worker_driver
        worker = WorkerConfig(
            driver=worker_driver,
            command=worker_agent.command,
            args=worker_agent.args,
            environment=worker_agent.environment,
            timeout_seconds=base.worker.timeout_seconds,
            model=profile.worker.model,
            process_profile=worker_agent.worker_process_profile,
            mcp=base.worker.mcp,
        )
    critic_config = base.critic
    if profile.critic_enabled and replace(base.critic, mcp=CriticConfig().mcp) == CriticConfig():
        if profile.critic.agent.casefold() == DIRECT_CRITIC_MODEL_AGENT:
            critic_config = CriticConfig(
                driver="model_json",
                command="",
                timeout_seconds=base.critic.timeout_seconds,
                mcp=base.critic.mcp,
            )
        else:
            critic_agent = _resolve_agent(base, profile.critic.agent, "critic", registry)
            critic_driver = profile.critic.driver or critic_agent.critic_driver
            if critic_driver is None:
                raise RoleProfileError(
                    f"Agent '{critic_agent.name}' cannot serve as Critic: no critic driver is declared."
                )
            critic_config = CriticConfig(
                driver=critic_driver,
                command=critic_agent.command,
                args=critic_agent.args,
                environment=critic_agent.environment,
                timeout_seconds=base.critic.timeout_seconds,
                mcp=base.critic.mcp,
            )
    critic = profile.critic or RoleSelection("disabled", "disabled")
    three_head = ThreeHeadConfig(
        mode="required" if profile.critic_enabled else "disabled",
        max_attempts=profile.max_attempts,
        logic=_identity(
            project_id,
            "supervisor",
            profile.supervisor,
            supervisor_model if profile.supervisor.agent.casefold() == DIRECT_SUPERVISOR_MODEL_AGENT else None,
        ),
        worker=_identity(project_id, "worker", profile.worker),
        critic=_identity(
            project_id,
            "critic",
            critic,
            verifier_model if critic.agent.casefold() == DIRECT_CRITIC_MODEL_AGENT else None,
        ),
    )
    return replace(
        base,
        supervisor_model=supervisor_model,
        verifier_model=verifier_model,
        supervisor=supervisor,
        worker=worker,
        critic=critic_config,
        three_head=three_head,
    )


def load_effective_config(
    project_root: Path,
    config_path: Path | None = None,
    base: HarnessConfig | None = None,
) -> HarnessConfig:
    from .config import load_config, project_config_path

    discovered_config = project_config_path(project_root)
    selected_config = config_path or (discovered_config if discovered_config.exists() else None)
    config = base or (load_config(selected_config) if selected_config else HarnessConfig())
    profile = load_role_profile(project_root)
    return apply_role_profile(config, profile, project_root.resolve().name) if profile else config


def _selection(value: Any, label: str) -> RoleSelection:
    if not isinstance(value, dict):
        raise RoleProfileError(f"Role profile {label} must be an object.")
    agent = value.get("agent")
    model = value.get("model")
    driver = value.get("driver")
    if not isinstance(agent, str):
        raise RoleProfileError(f"Role profile {label}.agent must be a string.")
    if model is not None and not isinstance(model, str):
        raise RoleProfileError(f"Role profile {label}.model must be a string or null.")
    if driver is not None and not isinstance(driver, str):
        raise RoleProfileError(f"Role profile {label}.driver must be a string or null.")
    return RoleSelection(agent=agent, model=model, driver=driver)


def _selection_payload(selection: RoleSelection) -> dict[str, Any]:
    return {"agent": selection.agent, "model": selection.model, "driver": selection.driver}


def _identity(
    project_id: str,
    role: str,
    selection: RoleSelection,
    endpoint: CloudModelEndpoint | None = None,
) -> RoleIdentityConfig:
    safe_project = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in project_id).strip("-") or "project"
    return RoleIdentityConfig(
        identity=f"{safe_project}-{role}",
        provider=endpoint.provider if endpoint is not None else selection.agent,
        model=selection.model or (endpoint.model if endpoint is not None else "agent-default"),
    )


def _resolve_agent(
    base: HarnessConfig,
    name: str,
    role: str,
    registry: AcpRegistry | None,
) -> AgentConfig | A2AAgentConfig:
    configured = next((item for item in base.agents if item.name.casefold() == name.casefold()), None)
    if configured is not None:
        return configured
    remote = next((item for item in base.a2a_agents if item.name.casefold() == name.casefold()), None)
    if remote is not None:
        driver = {
            "supervisor": remote.supervisor_driver,
            "worker": remote.worker_driver,
            "critic": remote.critic_driver,
        }[role]
        if driver is None:
            raise RoleProfileError(f"A2A agent '{name}' is not enabled for the {role} role.")
        return remote
    registry_agent = find_registry_agent(registry, name) if registry is not None else None
    if registry_agent is None:
        known = ", ".join(item.name for item in (*base.agents, *base.a2a_agents))
        hint = " Run 'llm-harness role-catalog --refresh-registry' to discover ACP agents."
        raise RoleProfileError(f"Unknown {role} agent '{name}'. Configured agents: {known}.{hint}")
    launch = resolve_acp_launch(registry_agent)
    if not launch.available:
        raise RoleProfileError(f"ACP Registry agent '{name}' is unavailable: {launch.detail}")
    return AgentConfig(
        name=registry_agent.id,
        command=launch.command,
        args=launch.args,
        environment=launch.environment,
        supervisor_driver="acp",
        worker_driver="acp",
        critic_driver="acp",
    )
