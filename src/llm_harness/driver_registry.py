from __future__ import annotations

from dataclasses import asdict, dataclass


DRIVER_PROTOCOL = "hoh.driver"
DRIVER_PROTOCOL_VERSION = "1.0"


class DriverRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class DriverManifest:
    driver_id: str
    role: str
    adapter: str
    task_transport: str
    artifact_contract: str | None
    requires_isolated_worktree: bool
    supports_subagents: bool
    safety_profile: str
    description: str
    protocol: str = DRIVER_PROTOCOL
    protocol_version: str = DRIVER_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.protocol != DRIVER_PROTOCOL or self.protocol_version != DRIVER_PROTOCOL_VERSION:
            raise DriverRegistryError("Unsupported HoH driver manifest protocol version.")
        if self.role not in {"supervisor", "worker", "critic"}:
            raise DriverRegistryError("Driver role must be 'supervisor', 'worker', or 'critic'.")
        for label, value in (
            ("driver_id", self.driver_id),
            ("adapter", self.adapter),
            ("task_transport", self.task_transport),
            ("safety_profile", self.safety_profile),
            ("description", self.description),
        ):
            if not value.strip():
                raise DriverRegistryError(f"Driver {label} must be non-empty.")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_BUILTIN_DRIVERS = (
    DriverManifest(
        "model_json", "supervisor", "model_json", "structured_model_request", None, False, False,
        "no-repository-authority-structured-plan",
        "Direct structured-output model used for planning; HoH retains repository and state authority.",
    ),
    DriverManifest(
        "acp", "supervisor", "acp", "acp_stdio", None, False, False,
        "acp-reject-all-isolated-planning",
        "Universal ACP v1 planning supervisor in an isolated directory with tool permissions rejected.",
    ),
    DriverManifest(
        "command_json", "supervisor", "command_json", "stdin_prompt", None, False, False,
        "isolated-planning-json",
        "Universal out-of-process planning supervisor: prompt on stdin, project-spec JSON on stdout.",
    ),
    DriverManifest(
        "a2a", "supervisor", "a2a", "a2a_jsonrpc_v1", None, False, False,
        "remote-opaque-planning-json",
        "Remote A2A v1 planning supervisor returning a strict project-spec JSON artifact.",
    ),
    DriverManifest(
        "acp", "worker", "acp", "acp_stdio", "worktree_diff", True, False,
        "acp-role-mediated-isolated-worktree",
        "Universal ACP v1 worker: capability handshake, bounded permissions, model selection, and isolated worktree diff.",
    ),
    DriverManifest(
        "command", "worker", "command", "stdin_prompt", "worktree_diff", True, False,
        "operator-configured-isolated-worktree",
        "Universal out-of-process worker: prompt on stdin, repository changes in an isolated worktree.",
    ),
    DriverManifest(
        "process", "worker", "process", "profiled_process", "worktree_diff", True, False,
        "declarative-one-shot-isolated-worktree",
        "Declarative one-shot CLI worker with HoH-owned prompt transport, model routing, and safety arguments.",
    ),
    DriverManifest(
        "a2a", "worker", "a2a", "a2a_jsonrpc_v1", "unified_diff", False, False,
        "remote-bounded-snapshot-unified-diff",
        "Remote A2A v1 worker receives only allowed files and returns a unified diff for local verification.",
    ),
    DriverManifest(
        "hermes_acp", "worker", "hermes_acp", "acp_stdio", "worktree_diff", True, False,
        "acp-allow-once-isolated-worktree", "Bundled Hermes ACP worker driver.",
    ),
    DriverManifest(
        "claude_code", "worker", "claude_code", "stdin_prompt", "worktree_diff", True, False,
        "dontAsk-file-tools-only-safe-mode", "Bundled Claude Code worker driver.",
    ),
    DriverManifest(
        "openclaw", "worker", "openclaw", "stdin_prompt", "worktree_diff", True, False,
        "local-ephemeral-config-workspace-file-tools-only", "Bundled OpenClaw worker driver.",
    ),
    DriverManifest(
        "stub", "worker", "stub", "in_process", "direct_patch", False, False,
        "in-process-test-only", "Deterministic in-process test driver; not for production work.",
    ),
    DriverManifest(
        "model_json", "critic", "model_json", "structured_model_request", None, False, False,
        "read-only-structured-model-judgment",
        "Direct provider-neutral structured-output critic using immutable review evidence.",
    ),
    DriverManifest(
        "command_json", "critic", "command_json", "stdin_json", None, False, False,
        "read-only-json-judgment", "Universal out-of-process critic: review prompt on stdin, judgment JSON on stdout.",
    ),
    DriverManifest(
        "acp", "critic", "acp", "acp_stdio", None, False, False,
        "acp-reject-all-read-only-json-judgment",
        "Universal ACP v1 critic: isolated empty cwd, rejected tool permissions, and judgment JSON.",
    ),
    DriverManifest(
        "a2a", "critic", "a2a", "a2a_jsonrpc_v1", None, False, False,
        "remote-immutable-evidence-json-judgment",
        "Remote A2A v1 critic receives immutable evidence and returns a strict judgment JSON artifact.",
    ),
    DriverManifest(
        "claude_code", "critic", "claude_code", "stdin_prompt", None, False, False,
        "plan-mode-no-tools", "Bundled Claude Code read-only critic driver.",
    ),
    DriverManifest(
        "manual", "critic", "manual", "file_exchange", None, False, False,
        "human-or-external-service", "Manual protocol-v2 review export/import boundary.",
    ),
)

_ALIASES = {
    ("worker", "hermes"): "hermes_acp",
    ("worker", "claude"): "claude_code",
}


def driver_catalog(role: str | None = None) -> tuple[DriverManifest, ...]:
    if role is None:
        return _BUILTIN_DRIVERS
    normalized = role.strip().casefold()
    if normalized not in {"supervisor", "worker", "critic"}:
        raise DriverRegistryError("Driver catalog role must be 'supervisor', 'worker', or 'critic'.")
    return tuple(item for item in _BUILTIN_DRIVERS if item.role == normalized)


def resolve_driver(driver_id: str, role: str) -> DriverManifest:
    normalized_role = role.strip().casefold()
    normalized_id = driver_id.strip().casefold()
    normalized_id = _ALIASES.get((normalized_role, normalized_id), normalized_id)
    match = next(
        (item for item in _BUILTIN_DRIVERS if item.role == normalized_role and item.driver_id == normalized_id),
        None,
    )
    if match is None:
        available = ", ".join(item.driver_id for item in driver_catalog(normalized_role))
        raise DriverRegistryError(
            f"Unsupported {normalized_role} driver '{driver_id}'. Available drivers: {available}"
        )
    return match
