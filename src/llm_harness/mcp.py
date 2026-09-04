from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping
from urllib.parse import urlparse


ACP_TOOL_KINDS = (
    "read",
    "edit",
    "delete",
    "move",
    "search",
    "execute",
    "think",
    "fetch",
    "switch_mode",
    "other",
)
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


@dataclass(frozen=True)
class SecretBinding:
    """Map a protocol field name to a process environment variable name."""

    name: str
    source_env: str

    def __post_init__(self) -> None:
        name = self.name.strip()
        source = self.source_env.strip()
        if not name:
            raise ValueError("MCP secret binding name must be non-empty.")
        if not _ENVIRONMENT_NAME.fullmatch(source):
            raise ValueError("MCP secret source_env must be an environment-variable name.")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "source_env", source)


@dataclass(frozen=True)
class McpServerConfig:
    """Declarative ACP MCP server config; it never stores credential values."""

    name: str
    transport: str = "stdio"
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    environment: tuple[SecretBinding, ...] = ()
    headers: tuple[SecretBinding, ...] = ()

    def __post_init__(self) -> None:
        name = self.name.strip()
        transport = self.transport.strip().casefold()
        command = (self.command or "").strip() or None
        url = (self.url or "").strip() or None
        if not name:
            raise ValueError("MCP server name must be non-empty.")
        if transport not in {"stdio", "http", "sse"}:
            raise ValueError("MCP transport must be stdio, http, or sse.")
        if transport == "stdio":
            if command is None:
                raise ValueError("MCP stdio server requires command.")
            if "\x00" in command:
                raise ValueError("MCP stdio command must not contain NUL bytes.")
            if url is not None or self.headers:
                raise ValueError("MCP stdio server cannot define url or HTTP headers.")
            for item in self.environment:
                if not _ENVIRONMENT_NAME.fullmatch(item.name):
                    raise ValueError(f"Invalid MCP process environment name: {item.name}")
        else:
            if url is None:
                raise ValueError("MCP HTTP/SSE server requires url.")
            if command is not None or self.args or self.environment:
                raise ValueError("MCP HTTP/SSE server cannot define command, args, or process environment.")
            _validate_remote_url(url)
            for item in self.headers:
                if not _HTTP_HEADER_NAME.fullmatch(item.name):
                    raise ValueError(f"Invalid MCP HTTP header name: {item.name}")
                if item.name.casefold() in {"host", "content-length"}:
                    raise ValueError("MCP headers cannot override Host or Content-Length.")
        if any(not item or "\x00" in item for item in self.args):
            raise ValueError("MCP server args must be non-empty and contain no NUL bytes.")
        bindings = self.environment if transport == "stdio" else self.headers
        names = [item.name.casefold() for item in bindings]
        if len(names) != len(set(names)):
            raise ValueError("MCP binding names must be unique within a server.")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "transport", transport)
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "url", url)


@dataclass(frozen=True)
class RoleMcpPolicyConfig:
    """Fail-closed MCP and ACP permission policy for one orchestration role."""

    permission_mode: str = "reject"
    allowed_tool_kinds: tuple[str, ...] = ()
    servers: tuple[McpServerConfig, ...] = ()

    def __post_init__(self) -> None:
        mode = self.permission_mode.strip().casefold()
        if mode not in {"reject", "allow_once"}:
            raise ValueError("MCP permission_mode must be reject or allow_once.")
        kinds = tuple(dict.fromkeys(item.strip().casefold() for item in self.allowed_tool_kinds))
        unknown = sorted(set(kinds) - set(ACP_TOOL_KINDS))
        if unknown:
            raise ValueError("Unknown ACP tool kinds: " + ", ".join(unknown))
        if mode == "reject" and kinds:
            raise ValueError("MCP reject policy cannot contain allowed_tool_kinds.")
        names = [item.name.casefold() for item in self.servers]
        if len(names) != len(set(names)):
            raise ValueError("MCP server names must be unique within a role policy.")
        object.__setattr__(self, "permission_mode", mode)
        object.__setattr__(self, "allowed_tool_kinds", kinds)

    def allows(self, kind: object) -> bool:
        normalized = str(kind).strip().casefold() if isinstance(kind, str) else "other"
        if normalized not in ACP_TOOL_KINDS:
            normalized = "other"
        return self.permission_mode == "allow_once" and normalized in self.allowed_tool_kinds


def worker_mcp_policy() -> RoleMcpPolicyConfig:
    """Compatibility default: preserve the existing isolated-worker allow-once behavior."""

    return RoleMcpPolicyConfig("allow_once", ACP_TOOL_KINDS)


def build_mcp_server_payloads(
    policy: RoleMcpPolicyConfig,
    *,
    environment: Mapping[str, str] | None = None,
    agent_capabilities: Mapping[str, bool] | None = None,
) -> list[dict[str, Any]]:
    env = environment if environment is not None else os.environ
    capabilities = agent_capabilities or {}
    payloads: list[dict[str, Any]] = []
    for server in policy.servers:
        if server.transport in {"http", "sse"} and not capabilities.get(server.transport, False):
            raise ValueError(
                f"ACP agent does not advertise MCP {server.transport.upper()} support required by '{server.name}'."
            )
        if server.transport == "stdio":
            command = _absolute_executable(server.command or "")
            payloads.append(
                {
                    "name": server.name,
                    "command": command,
                    "args": list(server.args),
                    "env": _resolve_bindings(server.environment, env, server.name),
                }
            )
            continue
        payloads.append(
            {
                "type": server.transport,
                "name": server.name,
                "url": server.url,
                "headers": _resolve_bindings(server.headers, env, server.name),
            }
        )
    return payloads


def redact_acp_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """Return an event-safe copy of a protocol message containing MCP credentials."""

    copied = _copy_json_value(message)
    params = copied.get("params") if isinstance(copied, dict) else None
    servers = params.get("mcpServers") if isinstance(params, dict) else None
    if isinstance(servers, list):
        for server in servers:
            if not isinstance(server, dict):
                continue
            for key in ("env", "headers"):
                bindings = server.get(key)
                if isinstance(bindings, list):
                    for binding in bindings:
                        if isinstance(binding, dict) and "value" in binding:
                            binding["value"] = "<redacted>"
    return copied


def _resolve_bindings(
    bindings: tuple[SecretBinding, ...],
    environment: Mapping[str, str],
    server_name: str,
) -> list[dict[str, str]]:
    missing = sorted(item.source_env for item in bindings if not environment.get(item.source_env))
    if missing:
        raise ValueError(
            f"MCP server '{server_name}' requires environment variables: {', '.join(missing)}"
        )
    return [{"name": item.name, "value": environment[item.source_env]} for item in bindings]


def _absolute_executable(command: str) -> str:
    candidate = Path(command).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    elif candidate.parent != Path("."):
        resolved = candidate.resolve()
    else:
        found = shutil.which(command)
        if not found:
            raise ValueError(f"MCP executable is unavailable: {command}")
        resolved = Path(found).resolve()
    if not resolved.is_file():
        raise ValueError(f"MCP executable is unavailable: {resolved}")
    return str(resolved)


def _validate_remote_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("MCP url must be an absolute HTTP(S) URL.")
    if parsed.username or parsed.password:
        raise ValueError("MCP url must not contain credentials.")
    if parsed.scheme == "http" and parsed.hostname.casefold() not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Remote MCP servers require HTTPS; HTTP is allowed only for loopback.")


def _copy_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _copy_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json_value(item) for item in value]
    return value
