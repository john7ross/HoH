from __future__ import annotations

from pathlib import Path
from typing import Any

from .acp_registry import (
    load_acp_registry,
    refresh_acp_registry,
    registry_cache_path,
    resolve_acp_launch,
)
from .config import CloudModelEndpoint, HarnessConfig
from .a2a import connection_from_role, probe_a2a_agent
from .agent_matrix import infer_declared_version
from .model_providers import model_endpoint_preflight
from .role_profiles import (
    DIRECT_CRITIC_MODEL_AGENT,
    DIRECT_SUPERVISOR_MODEL_AGENT,
    load_role_profile,
    role_profile_payload,
)
from .scanner import scan_agents, scan_local_models


def build_role_catalog(
    repository: Path,
    base: HarnessConfig,
    *,
    refresh_registry: bool = False,
) -> dict[str, Any]:
    """Build the structured three-role catalog shared by CLI and desktop GUI."""
    agents = scan_agents(base.agents)
    models = scan_local_models(base.local_models)
    profile = load_role_profile(repository)
    registry = refresh_acp_registry() if refresh_registry else load_acp_registry()
    configured_by_name = {item.name: item for item in base.agents}
    configured_agents: list[dict[str, Any]] = []
    for item in agents:
        configured = configured_by_name[item.name]
        process_profile = configured.worker_process_profile
        configured_agents.append(
            {
                "name": item.name,
                "display_name": item.name,
                "command": item.command,
                "available": item.available,
                "source": "config",
                "version": infer_declared_version(configured.args),
                "distribution": "configured",
                "detail": item.path or "command is not on PATH",
                "supervisor_driver": configured.supervisor_driver,
                "worker_driver": configured.worker_driver,
                "critic_driver": configured.critic_driver,
                "process_profile": process_profile.profile_id if process_profile is not None else None,
                "version_args": list(process_profile.version_args) if process_profile is not None else None,
            }
        )
    direct_agents = [
        _direct_model_agent(
            DIRECT_SUPERVISOR_MODEL_AGENT,
            "Direct Supervisor model",
            "supervisor",
            base.supervisor_model,
        ),
        _direct_model_agent(
            DIRECT_CRITIC_MODEL_AGENT,
            "Direct Critic model",
            "critic",
            base.verifier_model,
        ),
    ]
    remote_agents: list[dict[str, Any]] = []
    for remote in base.a2a_agents:
        available, detail, version = probe_a2a_agent(
            connection_from_role(remote.card_url, remote.environment, min(remote.timeout_seconds, 5.0))
        )
        remote_agents.append(
            {
                "name": remote.name,
                "display_name": remote.name,
                "command": remote.card_url,
                "available": available,
                "source": "a2a",
                "version": version,
                "distribution": "a2a-jsonrpc-v1",
                "detail": detail,
                "supervisor_driver": remote.supervisor_driver,
                "worker_driver": remote.worker_driver,
                "critic_driver": remote.critic_driver,
            }
        )
    registry_agents: list[dict[str, Any]] = []
    configured_names = {item["name"].casefold() for item in (*configured_agents, *remote_agents)}
    if registry is not None:
        for agent in registry.agents:
            if agent.id.casefold() in configured_names:
                continue
            launch = resolve_acp_launch(agent)
            registry_agents.append(
                {
                    "name": agent.id,
                    "display_name": agent.name,
                    "command": launch.command,
                    "available": launch.available,
                    "source": "acp_registry",
                    "version": agent.version,
                    "distribution": launch.distribution,
                    "detail": launch.detail,
                    "supervisor_driver": "acp",
                    "worker_driver": "acp",
                    "critic_driver": "acp",
                }
            )
    return {
        "agents": direct_agents + configured_agents + remote_agents + registry_agents,
        "models": [
            {"name": item.name, "available": item.available, "detail": item.detail}
            for item in models
        ],
        "current_profile": role_profile_payload(profile) if profile else None,
        "registry": {
            "cache_path": str(registry_cache_path()),
            "loaded": registry is not None,
            "version": registry.version if registry is not None else None,
            "agent_count": len(registry.agents) if registry is not None else 0,
        },
        "briefing_questions": [
            "Какого агента использовать как Supervisor — постановщика и ответственного перед вами? Модель можно не указывать, тогда используется выбранная в агенте.",
            "Какого агента использовать как Worker — исполнителя изменений? Модель можно не указывать.",
            "Нужен ли независимый Critic? Если да, выберите агента и при желании модель; если нет, Supervisor закроет задачу только после детерминированных проверок.",
            "Сколько попыток Worker разрешить до обязательной остановки и вопроса вам?",
        ],
    }


def _direct_model_agent(
    name: str,
    display_name: str,
    role: str,
    endpoint: CloudModelEndpoint,
) -> dict[str, Any]:
    if endpoint.provider == "stub":
        available = False
        detail = "configure a non-stub model provider in Project settings"
    else:
        available, detail = model_endpoint_preflight(endpoint)
    return {
        "name": name,
        "display_name": display_name,
        "command": "",
        "available": available,
        "source": "direct_model",
        "version": endpoint.model,
        "distribution": endpoint.provider,
        "detail": detail,
        "supervisor_driver": "model_json" if role == "supervisor" else None,
        "worker_driver": None,
        "critic_driver": "model_json" if role == "critic" else None,
    }
