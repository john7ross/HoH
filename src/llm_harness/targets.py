from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExecutionTargetType(StrEnum):
    LOCAL_AGENT = "local_agent"
    LOCAL_MODEL_ENDPOINT = "local_model_endpoint"


@dataclass(frozen=True)
class LocalAgentTarget:
    name: str
    command: str
    args: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    target_type: ExecutionTargetType = ExecutionTargetType.LOCAL_AGENT


@dataclass(frozen=True)
class LocalModelEndpointTarget:
    name: str
    base_url: str
    model: str
    protocol: str = "openai_compatible"
    target_type: ExecutionTargetType = ExecutionTargetType.LOCAL_MODEL_ENDPOINT


ExecutionTarget = LocalAgentTarget | LocalModelEndpointTarget


@dataclass(frozen=True)
class TargetProbe:
    name: str
    target_type: ExecutionTargetType
    available: bool
    detail: str
