from __future__ import annotations

from dataclasses import dataclass
from shutil import which
from typing import Callable, Iterable
from urllib.error import URLError
from urllib.request import Request, urlopen

from .config import AgentConfig
from .targets import ExecutionTargetType, LocalModelEndpointTarget, TargetProbe


Resolver = Callable[[str], str | None]


@dataclass(frozen=True)
class AgentProbe:
    name: str
    command: str
    available: bool
    path: str | None


def scan_agents(agents: Iterable[AgentConfig], resolver: Resolver = which) -> tuple[AgentProbe, ...]:
    probes: list[AgentProbe] = []
    for agent in agents:
        path = resolver(agent.command)
        probes.append(
            AgentProbe(
                name=agent.name,
                command=agent.command,
                available=path is not None,
                path=path,
            )
        )
    return tuple(probes)


ModelEndpointProbe = Callable[[LocalModelEndpointTarget], bool]


def scan_local_models(
    models: Iterable[LocalModelEndpointTarget],
    endpoint_probe: ModelEndpointProbe | None = None,
) -> tuple[TargetProbe, ...]:
    probe = endpoint_probe or _probe_openai_compatible_model
    results: list[TargetProbe] = []
    for model in models:
        available = probe(model)
        results.append(
            TargetProbe(
                name=model.name,
                target_type=ExecutionTargetType.LOCAL_MODEL_ENDPOINT,
                available=available,
                detail=f"{model.protocol} {model.base_url} model={model.model}",
            )
        )
    return tuple(results)


def _probe_openai_compatible_model(model: LocalModelEndpointTarget) -> bool:
    if model.protocol != "openai_compatible":
        return False
    base_url = model.base_url.rstrip("/")
    request = Request(f"{base_url}/v1/models", method="GET")
    try:
        with urlopen(request, timeout=1) as response:
            return 200 <= response.status < 300
    except (OSError, URLError):
        return False
