from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePath
import platform
import shutil
from typing import Any, Mapping
from urllib.request import Request, urlopen

from . import __version__
from .durable_io import atomic_write_text


ACP_REGISTRY_URL = "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json"
MAX_REGISTRY_BYTES = 5 * 1024 * 1024


class AcpRegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class AcpRegistryAgent:
    id: str
    name: str
    version: str
    description: str
    distribution: Mapping[str, Any]


@dataclass(frozen=True)
class AcpRegistry:
    version: str
    agents: tuple[AcpRegistryAgent, ...]


@dataclass(frozen=True)
class AcpLaunch:
    command: str
    args: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    distribution: str
    available: bool
    detail: str


def registry_cache_path(environment: Mapping[str, str] | None = None) -> Path:
    env = environment if environment is not None else os.environ
    override = env.get("HOH_ACP_REGISTRY_CACHE")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        root = env.get("LOCALAPPDATA")
        if root:
            return Path(root) / "HoH" / "acp-registry.json"
    xdg_cache = env.get("XDG_CACHE_HOME")
    root = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    return root / "hoh" / "acp-registry.json"


def refresh_acp_registry(
    cache_path: Path | None = None,
    *,
    url: str = ACP_REGISTRY_URL,
    timeout_seconds: float = 20.0,
) -> AcpRegistry:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": f"HoH/{__version__}"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read(MAX_REGISTRY_BYTES + 1)
    except OSError as exc:
        raise AcpRegistryError(f"Failed to refresh ACP Registry: {exc}") from exc
    if len(payload) > MAX_REGISTRY_BYTES:
        raise AcpRegistryError("ACP Registry response exceeds the 5 MiB safety limit.")
    registry = _decode_registry(payload)
    target = cache_path or registry_cache_path()
    atomic_write_text(target, payload.decode("utf-8") + ("" if payload.endswith(b"\n") else "\n"))
    return registry


def load_acp_registry(cache_path: Path | None = None) -> AcpRegistry | None:
    target = cache_path or registry_cache_path()
    if not target.exists():
        return None
    try:
        payload = target.read_bytes()
    except OSError as exc:
        raise AcpRegistryError(f"Failed to read cached ACP Registry: {exc}") from exc
    if len(payload) > MAX_REGISTRY_BYTES:
        raise AcpRegistryError("Cached ACP Registry exceeds the 5 MiB safety limit.")
    return _decode_registry(payload)


def find_registry_agent(registry: AcpRegistry, agent_id: str) -> AcpRegistryAgent | None:
    folded = agent_id.strip().casefold()
    return next((agent for agent in registry.agents if agent.id.casefold() == folded), None)


def resolve_acp_launch(agent: AcpRegistryAgent) -> AcpLaunch:
    # Prefer an exact, user-scope installation materialized by HoH. Import lazily
    # to keep the registry parser independent from the installation engine.
    from .agent_installation import AgentInstallationError, resolve_managed_launch

    try:
        managed = resolve_managed_launch(agent)
    except AgentInstallationError as exc:
        return AcpLaunch(
            command="",
            args=(),
            environment=(),
            distribution="managed-invalid",
            available=False,
            detail=f"managed installation is invalid: {exc}",
        )
    if managed is not None:
        return managed

    distribution = agent.distribution
    npx = distribution.get("npx")
    if isinstance(npx, dict):
        executable = shutil.which("npx")
        package = npx.get("package")
        if isinstance(package, str) and package:
            return AcpLaunch(
                command=executable or "npx",
                args=("-y", package, *_string_tuple(npx.get("args"))),
                environment=_environment(npx.get("env")),
                distribution="npx",
                available=executable is not None,
                detail="npx available" if executable else "npx is not on PATH",
            )

    uvx = distribution.get("uvx")
    if isinstance(uvx, dict):
        executable = shutil.which("uvx")
        package = uvx.get("package")
        if isinstance(package, str) and package:
            return AcpLaunch(
                command=executable or "uvx",
                args=(package, *_string_tuple(uvx.get("args"))),
                environment=_environment(uvx.get("env")),
                distribution="uvx",
                available=executable is not None,
                detail="uvx available" if executable else "uvx is not on PATH",
            )

    binary = distribution.get("binary")
    platform_key = current_platform_key()
    entry = binary.get(platform_key) if isinstance(binary, dict) else None
    if isinstance(entry, dict):
        command_value = entry.get("cmd")
        if isinstance(command_value, str) and command_value:
            command_name = PurePath(command_value.replace("\\", "/")).name
            executable = shutil.which(command_name)
            return AcpLaunch(
                command=executable or command_name,
                args=_string_tuple(entry.get("args")),
                environment=_environment(entry.get("env")),
                distribution="binary",
                available=executable is not None,
                detail=(
                    f"binary available at {executable}"
                    if executable
                    else f"registry binary is not installed on PATH ({command_name})"
                ),
            )

    return AcpLaunch(
        command="",
        args=(),
        environment=(),
        distribution="unsupported",
        available=False,
        detail=f"no supported distribution for {platform_key}",
    )


def current_platform_key() -> str:
    systems = {"Windows": "windows", "Linux": "linux", "Darwin": "darwin"}
    machines = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }
    system = systems.get(platform.system(), platform.system().casefold())
    machine = machines.get(platform.machine().casefold(), platform.machine().casefold())
    return f"{system}-{machine}"


def _decode_registry(payload: bytes) -> AcpRegistry:
    try:
        raw = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcpRegistryError("ACP Registry is not valid UTF-8 JSON.") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("version"), str):
        raise AcpRegistryError("ACP Registry root must contain a string version.")
    raw_agents = raw.get("agents")
    if not isinstance(raw_agents, list):
        raise AcpRegistryError("ACP Registry agents must be an array.")
    agents: list[AcpRegistryAgent] = []
    seen: set[str] = set()
    for index, raw_agent in enumerate(raw_agents):
        if not isinstance(raw_agent, dict):
            raise AcpRegistryError(f"ACP Registry agent {index + 1} must be an object.")
        values = tuple(raw_agent.get(key) for key in ("id", "name", "version", "description"))
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise AcpRegistryError(f"ACP Registry agent {index + 1} has invalid identity fields.")
        distribution = raw_agent.get("distribution")
        if not isinstance(distribution, dict) or not distribution:
            raise AcpRegistryError(f"ACP Registry agent {raw_agent['id']} has no distribution.")
        folded = raw_agent["id"].casefold()
        if folded in seen:
            raise AcpRegistryError(f"ACP Registry contains duplicate agent id: {raw_agent['id']}")
        seen.add(folded)
        agents.append(
            AcpRegistryAgent(
                id=raw_agent["id"],
                name=raw_agent["name"],
                version=raw_agent["version"],
                description=raw_agent["description"],
                distribution=distribution,
            )
        )
    return AcpRegistry(version=raw["version"], agents=tuple(agents))


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return ()
    return tuple(value)


def _environment(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        return ()
    return tuple(
        sorted((key, item) for key, item in value.items() if isinstance(key, str) and isinstance(item, str))
    )
