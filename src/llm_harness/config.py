from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
import json
import os
import re
import tomllib
from typing import Any
from urllib.parse import urlparse

from .command_policy import DEFAULT_ALLOWED_COMMANDS, CommandPolicy
from .coordination import CoordinationConfig
from .driver_registry import resolve_driver
from .durable_io import atomic_write_json, read_authored_text
from .mcp import McpServerConfig, RoleMcpPolicyConfig, SecretBinding, worker_mcp_policy
from .runtime import RuntimeConfig
from .targets import LocalModelEndpointTarget
from .trust import WorkerTrustLevel, parse_worker_trust_level


_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class CloudModelEndpoint:
    provider: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    timeout_seconds: float = 60.0
    max_retries: int = 2
    max_output_tokens: int = 8192

    def __post_init__(self) -> None:
        provider = self.provider.strip().casefold()
        if provider not in {"stub", "openai", "deepseek", "openai_compatible"}:
            raise ValueError(
                "model provider must be 'stub', 'openai', 'deepseek', or 'openai_compatible'."
            )
        if not self.model.strip():
            raise ValueError("model name must be non-empty.")
        if provider == "openai_compatible" and not self.base_url:
            raise ValueError("openai_compatible model provider requires base_url.")
        if provider == "openai_compatible" and not self.api_key_env:
            raise ValueError("openai_compatible model provider requires api_key_env.")
        if self.timeout_seconds <= 0:
            raise ValueError("model timeout_seconds must be greater than zero.")
        if self.max_retries < 0:
            raise ValueError("model max_retries must be zero or greater.")
        if self.max_output_tokens < 1:
            raise ValueError("model max_output_tokens must be at least 1.")
        object.__setattr__(self, "provider", provider)


@dataclass(frozen=True)
class ProcessProfileConfig:
    """Declarative one-shot CLI invocation owned and validated by HoH."""

    profile_id: str
    prompt_transport: str = "stdin"
    prompt_argument: str | None = None
    required_args: tuple[str, ...] = ()
    forbidden_args: tuple[str, ...] = ()
    model_argument: str | None = None
    version_args: tuple[str, ...] = ("--version",)

    def __post_init__(self) -> None:
        profile_id = self.profile_id.strip().casefold()
        transport = self.prompt_transport.strip().casefold()
        if not profile_id:
            raise ValueError("process profile_id must be non-empty.")
        if transport not in {"stdin", "file"}:
            raise ValueError("process prompt_transport must be 'stdin' or 'file'.")
        if transport == "file" and not (self.prompt_argument or "").strip():
            raise ValueError("file process profiles require prompt_argument.")
        if transport == "stdin" and self.prompt_argument is not None:
            raise ValueError("stdin process profiles must not set prompt_argument.")
        for label, values in (
            ("required_args", self.required_args),
            ("forbidden_args", self.forbidden_args),
            ("version_args", self.version_args),
        ):
            if any(not item.strip() or "\x00" in item for item in values):
                raise ValueError(f"process {label} entries must be non-empty and contain no NUL bytes.")
        if self.model_argument is not None and not self.model_argument.strip():
            raise ValueError("process model_argument must be non-empty when provided.")
        required = {_argument_key(item) for item in self.required_args}
        forbidden = {_argument_key(item) for item in self.forbidden_args}
        overlap = sorted(required & forbidden)
        if overlap:
            raise ValueError(f"process profile arguments cannot be both required and forbidden: {', '.join(overlap)}")
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "prompt_transport", transport)


@dataclass(frozen=True)
class AgentConfig:
    name: str
    command: str
    args: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    supervisor_driver: str | None = None
    worker_driver: str = "command"
    critic_driver: str | None = None
    worker_process_profile: ProcessProfileConfig | None = None

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.command.strip():
            raise ValueError("agent name and command must be non-empty.")
        worker_driver = resolve_driver(self.worker_driver, "worker").driver_id
        supervisor_driver = (
            resolve_driver(self.supervisor_driver, "supervisor").driver_id
            if self.supervisor_driver is not None
            else None
        )
        critic_driver = (
            resolve_driver(self.critic_driver, "critic").driver_id
            if self.critic_driver is not None
            else None
        )
        object.__setattr__(self, "worker_driver", worker_driver)
        object.__setattr__(self, "supervisor_driver", supervisor_driver)
        object.__setattr__(self, "critic_driver", critic_driver)
        if worker_driver == "process" and self.worker_process_profile is None:
            raise ValueError("agent worker_driver='process' requires worker_process_profile.")
        if worker_driver != "process" and self.worker_process_profile is not None:
            raise ValueError("agent worker_process_profile requires worker_driver='process'.")
        if self.worker_process_profile is not None:
            validate_process_arguments(self.args, self.worker_process_profile)


@dataclass(frozen=True)
class A2AAgentConfig:
    """Remote A2A v1 endpoint; credential values stay in the process environment."""

    name: str
    card_url: str
    roles: tuple[str, ...] = ("supervisor", "worker", "critic")
    auth_kind: str = "none"
    credential_env: str | None = None
    api_key_header: str | None = None
    oauth_flow: str = "client_credentials"
    client_id_env: str | None = None
    client_secret_env: str | None = None
    token_url: str | None = None
    device_authorization_url: str | None = None
    oidc_discovery_url: str | None = None
    scopes: tuple[str, ...] = ()
    client_auth_method: str = "basic"
    prefer_streaming: bool = True
    push_callback_url: str | None = None
    push_token_env: str | None = None
    timeout_seconds: float = 300.0
    poll_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name:
            raise ValueError("A2A agent name must be non-empty.")
        parsed = urlparse(self.card_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("A2A card_url must be an absolute HTTP(S) URL.")
        if parsed.username or parsed.password:
            raise ValueError("A2A card_url must not contain credentials.")
        if parsed.scheme == "http" and parsed.hostname.casefold() not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote A2A endpoints require HTTPS; HTTP is allowed only for loopback.")
        roles = tuple(dict.fromkeys(item.strip().casefold() for item in self.roles))
        if not roles or any(item not in {"supervisor", "worker", "critic"} for item in roles):
            raise ValueError("A2A roles must contain supervisor, worker, and/or critic.")
        auth_kind = self.auth_kind.strip().casefold()
        if auth_kind not in {"none", "bearer", "api_key", "oauth2", "oidc"}:
            raise ValueError("A2A auth_kind must be none, bearer, api_key, oauth2, or oidc.")
        credential_env = (self.credential_env or "").strip() or None
        api_key_header = (self.api_key_header or "").strip() or None
        if auth_kind in {"bearer", "api_key"} and credential_env is None:
            raise ValueError("Authenticated A2A endpoints require credential_env.")
        if credential_env is not None and not _ENVIRONMENT_NAME.fullmatch(credential_env):
            raise ValueError("A2A credential_env must be an environment-variable name.")
        if auth_kind == "api_key":
            if api_key_header is None or not _valid_http_header_name(api_key_header):
                raise ValueError("A2A api_key authentication requires a valid api_key_header.")
            if api_key_header.casefold() in {"host", "content-length", "a2a-version", "a2a-extensions"}:
                raise ValueError("A2A api_key_header cannot override a protocol-owned HTTP header.")
        elif api_key_header is not None:
            raise ValueError("A2A api_key_header is valid only with auth_kind='api_key'.")
        oauth_flow = self.oauth_flow.strip().casefold()
        if oauth_flow not in {"client_credentials", "device_code"}:
            raise ValueError("A2A oauth_flow must be client_credentials or device_code.")
        client_auth_method = self.client_auth_method.strip().casefold()
        if client_auth_method not in {"basic", "post"}:
            raise ValueError("A2A client_auth_method must be basic or post.")
        environment_names = {
            "client_id_env": (self.client_id_env or "").strip() or None,
            "client_secret_env": (self.client_secret_env or "").strip() or None,
            "push_token_env": (self.push_token_env or "").strip() or None,
        }
        for label, name_value in environment_names.items():
            if name_value is not None and not _ENVIRONMENT_NAME.fullmatch(name_value):
                raise ValueError(f"A2A {label} must be an environment-variable name.")
        token_url = _optional_url(self.token_url, "A2A token_url")
        device_url = _optional_url(self.device_authorization_url, "A2A device_authorization_url")
        discovery_url = _optional_url(self.oidc_discovery_url, "A2A oidc_discovery_url")
        push_url = _optional_url(self.push_callback_url, "A2A push_callback_url")
        scopes = tuple(dict.fromkeys(item.strip() for item in self.scopes if item.strip()))
        if auth_kind in {"oauth2", "oidc"} and credential_env is None:
            if environment_names["client_id_env"] is None:
                raise ValueError("OAuth/OIDC A2A endpoints require client_id_env or credential_env.")
            if oauth_flow == "client_credentials" and environment_names["client_secret_env"] is None:
                raise ValueError("OAuth client_credentials requires client_secret_env.")
            # Endpoints may be declared by the selected Agent Card security scheme.
        if push_url is not None and environment_names["push_token_env"] is None:
            raise ValueError("A2A push callbacks require push_token_env for webhook authentication.")
        if self.timeout_seconds <= 0 or self.poll_interval_seconds <= 0:
            raise ValueError("A2A timeout and poll interval must be greater than zero.")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "card_url", self.card_url.strip())
        object.__setattr__(self, "roles", roles)
        object.__setattr__(self, "auth_kind", auth_kind)
        object.__setattr__(self, "credential_env", credential_env)
        object.__setattr__(self, "api_key_header", api_key_header)
        object.__setattr__(self, "oauth_flow", oauth_flow)
        object.__setattr__(self, "client_id_env", environment_names["client_id_env"])
        object.__setattr__(self, "client_secret_env", environment_names["client_secret_env"])
        object.__setattr__(self, "token_url", token_url)
        object.__setattr__(self, "device_authorization_url", device_url)
        object.__setattr__(self, "oidc_discovery_url", discovery_url)
        object.__setattr__(self, "scopes", scopes)
        object.__setattr__(self, "client_auth_method", client_auth_method)
        object.__setattr__(self, "push_callback_url", push_url)
        object.__setattr__(self, "push_token_env", environment_names["push_token_env"])

    @property
    def command(self) -> str:
        return self.card_url

    @property
    def args(self) -> tuple[str, ...]:
        return ()

    @property
    def environment(self) -> tuple[tuple[str, str], ...]:
        values = {
            "HOH_A2A_AUTH_KIND": self.auth_kind,
            "HOH_A2A_POLL_INTERVAL": str(self.poll_interval_seconds),
        }
        if self.credential_env is not None:
            values["HOH_A2A_CREDENTIAL_ENV"] = self.credential_env
        if self.api_key_header is not None:
            values["HOH_A2A_API_KEY_HEADER"] = self.api_key_header
        values["HOH_A2A_OAUTH_FLOW"] = self.oauth_flow
        values["HOH_A2A_CLIENT_AUTH_METHOD"] = self.client_auth_method
        values["HOH_A2A_PREFER_STREAMING"] = "1" if self.prefer_streaming else "0"
        for key, value in (
            ("HOH_A2A_CLIENT_ID_ENV", self.client_id_env),
            ("HOH_A2A_CLIENT_SECRET_ENV", self.client_secret_env),
            ("HOH_A2A_TOKEN_URL", self.token_url),
            ("HOH_A2A_DEVICE_AUTHORIZATION_URL", self.device_authorization_url),
            ("HOH_A2A_OIDC_DISCOVERY_URL", self.oidc_discovery_url),
            ("HOH_A2A_PUSH_CALLBACK_URL", self.push_callback_url),
            ("HOH_A2A_PUSH_TOKEN_ENV", self.push_token_env),
        ):
            if value is not None:
                values[key] = value
        if self.scopes:
            values["HOH_A2A_SCOPES"] = " ".join(self.scopes)
        return tuple(sorted(values.items()))

    @property
    def supervisor_driver(self) -> str | None:
        return "a2a" if "supervisor" in self.roles else None

    @property
    def worker_driver(self) -> str | None:
        return "a2a" if "worker" in self.roles else None

    @property
    def critic_driver(self) -> str | None:
        return "a2a" if "critic" in self.roles else None

    @property
    def worker_process_profile(self) -> None:
        return None


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool = False
    bot_token_env: str | None = None
    chat_id_env: str | None = None
    user_id_env: str | None = None


@dataclass(frozen=True)
class WorkerCapabilitiesConfig:
    task_transport: str = "acp_stdio"
    artifact_contract: str = "worktree_diff"
    requires_isolated_worktree: bool = True
    supports_subagents: bool = False


@dataclass(frozen=True)
class WorkerConfig:
    type: str = "hermes_acp"
    driver: str | None = None
    command: str = "hermes"
    args: tuple[str, ...] = ("acp",)
    environment: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float = 300.0
    model: str | None = None
    max_budget_usd: float | None = None
    thinking: str | None = None
    process_profile: ProcessProfileConfig | None = None
    capabilities: WorkerCapabilitiesConfig = WorkerCapabilitiesConfig()
    mcp: RoleMcpPolicyConfig = field(default_factory=worker_mcp_policy)

    def __post_init__(self) -> None:
        normalized_driver = (self.driver or self.type).strip().casefold()
        if not normalized_driver:
            raise ValueError("worker.driver must be non-empty.")
        normalized_driver = resolve_driver(normalized_driver, "worker").driver_id
        object.__setattr__(self, "driver", normalized_driver)
        object.__setattr__(self, "type", normalized_driver)
        if self.timeout_seconds <= 0:
            raise ValueError("worker.timeout_seconds must be greater than zero.")
        if self.model is not None and not self.model.strip():
            raise ValueError("worker.model must be non-empty when provided.")
        if self.max_budget_usd is not None and self.max_budget_usd <= 0:
            raise ValueError("worker.max_budget_usd must be greater than zero when provided.")
        if self.thinking is not None and not self.thinking.strip():
            raise ValueError("worker.thinking must be non-empty when provided.")
        if normalized_driver == "process" and self.process_profile is None:
            raise ValueError("worker.driver='process' requires worker.process_profile.")
        if normalized_driver != "process" and self.process_profile is not None:
            raise ValueError("worker.process_profile requires worker.driver='process'.")
        if self.process_profile is not None:
            validate_process_arguments(self.args, self.process_profile)
        if (
            normalized_driver == "process"
            and self.model is not None
            and self.process_profile is not None
            and self.process_profile.model_argument is None
        ):
            raise ValueError("worker.model requires process_profile.model_argument.")
        if normalized_driver in {"claude_code", "openclaw", "stub"}:
            if self.command == "hermes" and self.args == ("acp",):
                defaults = {
                    "claude_code": "claude",
                    "claude": "claude",
                    "openclaw": "openclaw",
                    "stub": "stub",
                }
                object.__setattr__(self, "command", defaults[normalized_driver])
                object.__setattr__(self, "args", ())
        default_capabilities = WorkerCapabilitiesConfig()
        if self.capabilities == default_capabilities and normalized_driver != "hermes_acp":
            object.__setattr__(self, "capabilities", _default_worker_capabilities(normalized_driver))


@dataclass(frozen=True)
class CriticConfig:
    type: str = "manual"
    driver: str | None = None
    command: str = "claude"
    args: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float = 300.0
    max_budget_usd: float | None = None
    mcp: RoleMcpPolicyConfig = field(default_factory=RoleMcpPolicyConfig)

    def __post_init__(self) -> None:
        normalized_driver = (self.driver or self.type).strip().casefold()
        try:
            normalized_driver = resolve_driver(normalized_driver, "critic").driver_id
        except ValueError as exc:
            raise ValueError(str(exc).replace("critic driver", "critic.type")) from exc
        if normalized_driver != "model_json" and not self.command.strip():
            raise ValueError("critic.command must be non-empty.")
        if self.timeout_seconds <= 0:
            raise ValueError("critic.timeout_seconds must be greater than zero.")
        if self.max_budget_usd is not None and self.max_budget_usd <= 0:
            raise ValueError("critic.max_budget_usd must be greater than zero when provided.")
        object.__setattr__(self, "driver", normalized_driver)
        object.__setattr__(self, "type", normalized_driver)

    @property
    def automatic(self) -> bool:
        return self.driver != "manual"


@dataclass(frozen=True)
class SupervisorConfig:
    driver: str = "model_json"
    command: str = ""
    args: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float = 300.0
    model: str | None = None
    mcp: RoleMcpPolicyConfig = field(default_factory=RoleMcpPolicyConfig)

    def __post_init__(self) -> None:
        driver = resolve_driver(self.driver, "supervisor").driver_id
        if driver != "model_json" and not self.command.strip():
            raise ValueError("supervisor.command must be non-empty for process drivers.")
        if self.timeout_seconds <= 0:
            raise ValueError("supervisor.timeout_seconds must be greater than zero.")
        if self.model is not None and not self.model.strip():
            raise ValueError("supervisor.model must be non-empty when provided.")
        object.__setattr__(self, "driver", driver)


@dataclass(frozen=True)
class RoleIdentityConfig:
    identity: str
    provider: str
    model: str

    @property
    def model_fingerprint(self) -> tuple[str, str]:
        return (self.provider.strip().casefold(), self.model.strip().casefold())


@dataclass(frozen=True)
class ThreeHeadConfig:
    mode: str = "manual"
    max_attempts: int = 3
    logic: RoleIdentityConfig = RoleIdentityConfig("external-supervisor", "external", "supervisor-agent")
    worker: RoleIdentityConfig = RoleIdentityConfig("configured-worker", "agent", "worker-agent")
    critic: RoleIdentityConfig = RoleIdentityConfig("external-critic", "external", "critic-agent")

    def __post_init__(self) -> None:
        normalized_mode = self.mode.strip().casefold()
        if normalized_mode not in {"disabled", "manual", "required"}:
            raise ValueError("three_head.mode must be 'disabled', 'manual', or 'required'.")
        object.__setattr__(self, "mode", normalized_mode)
        if self.max_attempts < 1:
            raise ValueError("three_head.max_attempts must be at least 1.")
        roles = (self.logic, self.worker, self.critic)
        for role in roles:
            if not role.identity.strip() or not role.provider.strip() or not role.model.strip():
                raise ValueError("three_head role identity, provider, and model must be non-empty.")
        if self.required:
            identities = [role.identity.strip().casefold() for role in roles]
            if len(set(identities)) != len(identities):
                raise ValueError("three_head required mode needs distinct logic, worker, and critic identities.")

    @property
    def required(self) -> bool:
        return self.mode == "required"


@dataclass(frozen=True)
class SchedulerConfig:
    max_parallel_tasks: int = 1

    def __post_init__(self) -> None:
        if self.max_parallel_tasks < 1:
            raise ValueError("scheduler.max_parallel_tasks must be at least 1.")


@dataclass(frozen=True)
class ModelPriceConfig:
    provider: str
    model: str
    input_per_million: float = 0.0
    output_per_million: float = 0.0
    currency: str = "USD"

    def __post_init__(self) -> None:
        provider = self.provider.strip().casefold()
        model = self.model.strip()
        currency = self.currency.strip().upper()
        if not provider or not model:
            raise ValueError("metrics price provider and model must be non-empty.")
        if self.input_per_million < 0 or self.output_per_million < 0:
            raise ValueError("metrics prices cannot be negative.")
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("metrics price currency must be a three-letter code.")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "currency", currency)


@dataclass(frozen=True)
class MetricsConfig:
    prices: tuple[ModelPriceConfig, ...] = ()

    def __post_init__(self) -> None:
        keys = [(item.provider, item.model.casefold()) for item in self.prices]
        if len(keys) != len(set(keys)):
            raise ValueError("metrics prices must contain unique provider/model pairs.")


@dataclass(frozen=True)
class AuditConfig:
    language_analyzers: tuple[str, ...] = ("python", "javascript", "typescript")
    entry_points: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()
    fail_on_unavailable: bool = True

    def __post_init__(self) -> None:
        normalized = tuple(dict.fromkeys(item.strip().casefold() for item in self.language_analyzers))
        if any(not item for item in normalized):
            raise ValueError("audit.language_analyzers entries must be non-empty.")
        object.__setattr__(self, "language_analyzers", normalized)


@dataclass(frozen=True)
class VerificationConfig:
    """Which programs a task's verification_commands may start.

    Verification commands come from the planning model, so they are executed as
    argv without a shell and only from this list unless the operator opts out.
    """

    allowed_commands: tuple[str, ...] = DEFAULT_ALLOWED_COMMANDS
    allow_unlisted_commands: bool = False
    inherit_secret_environment: bool = False

    def policy(self) -> CommandPolicy:
        return CommandPolicy(
            allowed_commands=self.allowed_commands,
            allow_unlisted_commands=self.allow_unlisted_commands,
            inherit_secret_environment=self.inherit_secret_environment,
        )


@dataclass(frozen=True)
class HarnessConfig:
    supervisor_model: CloudModelEndpoint = CloudModelEndpoint(provider="stub", model="supervisor-stub")
    verifier_model: CloudModelEndpoint = CloudModelEndpoint(provider="stub", model="verifier-stub")
    runtime: RuntimeConfig = RuntimeConfig()
    telegram: TelegramConfig = TelegramConfig()
    supervisor: SupervisorConfig = SupervisorConfig()
    worker: WorkerConfig = WorkerConfig()
    critic: CriticConfig = CriticConfig()
    three_head: ThreeHeadConfig = ThreeHeadConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    metrics: MetricsConfig = MetricsConfig()
    audit: AuditConfig = AuditConfig()
    verification: VerificationConfig = VerificationConfig()
    coordination: CoordinationConfig = CoordinationConfig()
    agents: tuple[AgentConfig, ...] = field(
        default_factory=lambda: (
            AgentConfig(
                name="codex",
                command="npx",
                args=("-y", "@agentclientprotocol/codex-acp@1.7.0"),
                supervisor_driver="acp",
                worker_driver="acp",
                critic_driver="acp",
            ),
            AgentConfig(
                name="claude", command="claude", worker_driver="claude_code", critic_driver="claude_code"
            ),
            AgentConfig(
                name="hermes",
                command="hermes",
                args=("acp",),
                supervisor_driver="acp",
                worker_driver="hermes_acp",
            ),
            AgentConfig(name="openclaw", command="openclaw", worker_driver="openclaw"),
            AgentConfig(
                name="aider",
                command="aider",
                worker_driver="process",
                worker_process_profile=ProcessProfileConfig(
                    profile_id="aider",
                    prompt_transport="file",
                    prompt_argument="--message-file",
                    required_args=(
                        "--no-stream",
                        "--yes",
                        "--no-auto-commits",
                        "--no-dirty-commits",
                    ),
                    forbidden_args=(
                        "--stream",
                        "--auto-commits",
                        "--dirty-commits",
                        "--commit",
                        "--message",
                        "--msg",
                        "-m",
                    ),
                    model_argument="--model",
                ),
            ),
        )
    )
    a2a_agents: tuple[A2AAgentConfig, ...] = ()
    local_models: tuple[LocalModelEndpointTarget, ...] = ()
    forbidden_paths: tuple[str, ...] = (".git/",)
    require_tests: bool = True
    worker_trust_level: WorkerTrustLevel = WorkerTrustLevel.PATCH_ONLY


PROJECT_CONFIG_RELATIVE_PATH = Path(".hoh") / "harness.json"


def project_config_path(project_root: Path) -> Path:
    return project_root.resolve() / PROJECT_CONFIG_RELATIVE_PATH


def config_payload(config: HarnessConfig) -> dict[str, Any]:
    """Return the complete JSON-safe project configuration used by the GUI and CLI."""
    payload = _json_value(asdict(config))
    for section in ("supervisor", "worker", "critic"):
        payload[section]["environment"] = dict(config.__getattribute__(section).environment)
    for index, agent in enumerate(config.agents):
        payload["agents"][index]["environment"] = dict(agent.environment)
    return payload


def write_config(path: Path, config: HarnessConfig) -> Path:
    """Validate and atomically persist a HarnessConfig as JSON."""
    if path.suffix.casefold() != ".json":
        raise ValueError("Writable project configuration must use the .json format.")
    atomic_write_json(path, config_payload(config))
    # Prove that the persisted representation remains loadable before reporting success.
    load_config(path)
    return path


def load_config(path: Path) -> HarnessConfig:
    raw = _load_mapping(path)
    return HarnessConfig(
        supervisor_model=_model(raw.get("supervisor_model"), "supervisor-stub"),
        verifier_model=_model(raw.get("verifier_model"), "verifier-stub"),
        runtime=_runtime(raw.get("runtime")),
        telegram=_telegram(raw.get("telegram")),
        supervisor=_supervisor(raw.get("supervisor")),
        worker=_worker(raw.get("worker")),
        critic=_critic(raw.get("critic")),
        three_head=_three_head(raw.get("three_head")),
        scheduler=_scheduler(raw.get("scheduler")),
        metrics=_metrics(raw.get("metrics")),
        audit=_audit(raw.get("audit")),
        verification=_verification(raw.get("verification")),
        coordination=_coordination(raw.get("coordination")),
        agents=tuple(_agent(item) for item in raw.get("agents", []))
        or HarnessConfig().agents,
        a2a_agents=tuple(_a2a_agent(item) for item in raw.get("a2a_agents", [])),
        local_models=tuple(_local_model(item) for item in raw.get("local_models", [])),
        forbidden_paths=tuple(raw.get("forbidden_paths", HarnessConfig().forbidden_paths)),
        require_tests=bool(raw.get("require_tests", True)),
        worker_trust_level=parse_worker_trust_level(
            raw.get("worker_trust_level", WorkerTrustLevel.PATCH_ONLY)
        ),
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _load_mapping(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        return json.loads(read_authored_text(path))
    if path.suffix.lower() == ".toml":
        return tomllib.loads(read_authored_text(path))
    raise ValueError(f"Unsupported config format: {path.suffix}")


def _model(value: Any, default_model: str) -> CloudModelEndpoint:
    if not value:
        return CloudModelEndpoint(provider="stub", model=default_model)
    return CloudModelEndpoint(
        provider=str(value["provider"]),
        model=str(value["model"]),
        base_url=value.get("base_url"),
        api_key_env=value.get("api_key_env"),
        timeout_seconds=float(value.get("timeout_seconds", 60.0)),
        max_retries=int(value.get("max_retries", 2)),
        max_output_tokens=int(value.get("max_output_tokens", 8192)),
    )


def _runtime(value: Any) -> RuntimeConfig:
    if not value:
        return RuntimeConfig()
    return RuntimeConfig(
        require_embedded_python=bool(value.get("require_embedded_python", os.name == "nt")),
        embedded_python_path=str(value.get("embedded_python_path", "runtime/python/python.exe")),
        wheels_path=str(value.get("wheels_path", "vendor/wheels")),
    )


def _scheduler(value: Any) -> SchedulerConfig:
    if not value:
        return SchedulerConfig()
    return SchedulerConfig(max_parallel_tasks=int(value.get("max_parallel_tasks", 1)))


def _metrics(value: Any) -> MetricsConfig:
    if not value:
        return MetricsConfig()
    prices = value.get("prices", ())
    if not isinstance(prices, (list, tuple)):
        raise ValueError("metrics.prices must be a list.")
    if any(not isinstance(item, dict) for item in prices):
        raise ValueError("metrics.prices entries must be objects.")
    return MetricsConfig(
        prices=tuple(
            ModelPriceConfig(
                provider=str(item["provider"]),
                model=str(item["model"]),
                input_per_million=float(item.get("input_per_million", 0.0)),
                output_per_million=float(item.get("output_per_million", 0.0)),
                currency=str(item.get("currency", "USD")),
            )
            for item in prices
        )
    )


def _audit(value: Any) -> AuditConfig:
    if not value:
        return AuditConfig()
    defaults = AuditConfig()
    return AuditConfig(
        language_analyzers=tuple(str(item) for item in value.get("language_analyzers", defaults.language_analyzers)),
        entry_points=tuple(str(item) for item in value.get("entry_points", ())),
        exclude_paths=tuple(str(item) for item in value.get("exclude_paths", ())),
        fail_on_unavailable=bool(value.get("fail_on_unavailable", True)),
    )


def _verification(value: Any) -> VerificationConfig:
    if not value:
        return VerificationConfig()
    defaults = VerificationConfig()
    return VerificationConfig(
        allowed_commands=tuple(
            str(item) for item in value.get("allowed_commands", defaults.allowed_commands)
        ),
        allow_unlisted_commands=bool(
            value.get("allow_unlisted_commands", defaults.allow_unlisted_commands)
        ),
        inherit_secret_environment=bool(
            value.get("inherit_secret_environment", defaults.inherit_secret_environment)
        ),
    )


def _coordination(value: Any) -> CoordinationConfig:
    if not value:
        return CoordinationConfig()
    defaults = CoordinationConfig()
    return CoordinationConfig(
        state_lock_timeout_seconds=float(
            value.get("state_lock_timeout_seconds", defaults.state_lock_timeout_seconds)
        ),
        execution_lock_timeout_seconds=float(
            value.get("execution_lock_timeout_seconds", defaults.execution_lock_timeout_seconds)
        ),
        poll_interval_seconds=float(
            value.get("poll_interval_seconds", defaults.poll_interval_seconds)
        ),
    )


def _telegram(value: Any) -> TelegramConfig:
    if not value:
        return TelegramConfig()
    return TelegramConfig(
        enabled=bool(value.get("enabled", False)),
        bot_token_env=value.get("bot_token_env"),
        chat_id_env=value.get("chat_id_env"),
        user_id_env=value.get("user_id_env"),
    )


def _worker(value: Any) -> WorkerConfig:
    if not value:
        return WorkerConfig()
    worker_driver = _driver_value(value, "worker", "hermes_acp")
    default_command, default_args = _default_worker_invocation(worker_driver)
    return WorkerConfig(
        driver=worker_driver,
        command=str(value.get("command", default_command)),
        args=tuple(str(item) for item in value.get("args", default_args)),
        environment=_environment(value.get("environment")),
        timeout_seconds=float(value.get("timeout_seconds", 300.0)),
        model=_optional_config_string(value.get("model")),
        max_budget_usd=(
            float(value["max_budget_usd"])
            if value.get("max_budget_usd") is not None
            else None
        ),
        thinking=_optional_config_string(value.get("thinking")),
        process_profile=_process_profile(value.get("process_profile"), "worker.process_profile"),
        capabilities=_worker_capabilities(value.get("capabilities"), worker_driver),
        mcp=_mcp_policy(value.get("mcp"), worker=True),
    )


def _critic(value: Any) -> CriticConfig:
    if not value:
        return CriticConfig()
    return CriticConfig(
        driver=_driver_value(value, "critic", "manual"),
        command=str(value.get("command", "claude")),
        args=tuple(str(item) for item in value.get("args", ())),
        environment=_environment(value.get("environment")),
        timeout_seconds=float(value.get("timeout_seconds", 300.0)),
        max_budget_usd=(
            float(value["max_budget_usd"])
            if value.get("max_budget_usd") is not None
            else None
        ),
        mcp=_mcp_policy(value.get("mcp"), worker=False),
    )


def _supervisor(value: Any) -> SupervisorConfig:
    if not value:
        return SupervisorConfig()
    return SupervisorConfig(
        driver=str(value.get("driver", "model_json")),
        command=str(value.get("command", "")),
        args=tuple(str(item) for item in value.get("args", ())),
        environment=_environment(value.get("environment")),
        timeout_seconds=float(value.get("timeout_seconds", 300.0)),
        model=_optional_config_string(value.get("model")),
        mcp=_mcp_policy(value.get("mcp"), worker=False),
    )


def _mcp_policy(value: Any, *, worker: bool) -> RoleMcpPolicyConfig:
    if not value:
        return worker_mcp_policy() if worker else RoleMcpPolicyConfig()
    if not isinstance(value, dict):
        raise ValueError("role.mcp must be an object.")
    default = worker_mcp_policy() if worker else RoleMcpPolicyConfig()
    raw_servers = value.get("servers", ())
    if not isinstance(raw_servers, (list, tuple)) or any(
        not isinstance(item, dict) for item in raw_servers
    ):
        raise ValueError("role.mcp.servers must be a list of objects.")
    return RoleMcpPolicyConfig(
        permission_mode=str(value.get("permission_mode", default.permission_mode)),
        allowed_tool_kinds=tuple(
            str(item) for item in value.get("allowed_tool_kinds", default.allowed_tool_kinds)
        ),
        servers=tuple(_mcp_server(item) for item in raw_servers),
    )


def _mcp_server(value: dict[str, Any]) -> McpServerConfig:
    return McpServerConfig(
        name=str(value["name"]),
        transport=str(value.get("transport", "stdio")),
        command=_optional_config_string(value.get("command")),
        args=tuple(str(item) for item in value.get("args", ())),
        url=_optional_config_string(value.get("url")),
        environment=_secret_bindings(value.get("environment"), "MCP environment"),
        headers=_secret_bindings(value.get("headers"), "MCP headers"),
    )


def _secret_bindings(value: Any, label: str) -> tuple[SecretBinding, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be a list of objects.")
    return tuple(
        SecretBinding(name=str(item["name"]), source_env=str(item["source_env"]))
        for item in value
    )


def _three_head(value: Any) -> ThreeHeadConfig:
    if not value:
        return ThreeHeadConfig()
    roles = value.get("roles") or {}
    if not isinstance(roles, dict):
        raise ValueError("three_head.roles must be an object.")
    defaults = ThreeHeadConfig()
    return ThreeHeadConfig(
        mode=str(value.get("mode", defaults.mode)),
        max_attempts=int(value.get("max_attempts", defaults.max_attempts)),
        logic=_role_identity(roles.get("logic"), defaults.logic),
        worker=_role_identity(roles.get("worker"), defaults.worker),
        critic=_role_identity(roles.get("critic"), defaults.critic),
    )


def _role_identity(value: Any, default: RoleIdentityConfig) -> RoleIdentityConfig:
    if not value:
        return default
    if not isinstance(value, dict):
        raise ValueError("three_head role manifest must be an object.")
    return RoleIdentityConfig(
        identity=str(value.get("identity", default.identity)),
        provider=str(value.get("provider", default.provider)),
        model=str(value.get("model", default.model)),
    )


def _worker_capabilities(value: Any, worker_type: str) -> WorkerCapabilitiesConfig:
    defaults = _default_worker_capabilities(worker_type)
    if not value:
        return defaults
    return WorkerCapabilitiesConfig(
        task_transport=str(value.get("task_transport", defaults.task_transport)),
        artifact_contract=str(value.get("artifact_contract", defaults.artifact_contract)),
        requires_isolated_worktree=bool(
            value.get("requires_isolated_worktree", defaults.requires_isolated_worktree)
        ),
        supports_subagents=bool(value.get("supports_subagents", defaults.supports_subagents)),
    )


def _default_worker_capabilities(worker_type: str) -> WorkerCapabilitiesConfig:
    manifest = resolve_driver(worker_type, "worker")
    return WorkerCapabilitiesConfig(
        task_transport=manifest.task_transport,
        artifact_contract=manifest.artifact_contract or "worktree_diff",
        requires_isolated_worktree=manifest.requires_isolated_worktree,
        supports_subagents=manifest.supports_subagents,
    )


def _default_worker_invocation(worker_type: str) -> tuple[str, tuple[str, ...]]:
    normalized = worker_type.strip().casefold()
    if normalized in {"hermes_acp", "hermes"}:
        return "hermes", ("acp",)
    if normalized == "acp":
        return "", ()
    if normalized in {"claude_code", "claude"}:
        return "claude", ()
    if normalized == "openclaw":
        return "openclaw", ()
    if normalized == "stub":
        return "stub", ()
    return "", ()


def _optional_config_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _agent(value: Any) -> AgentConfig:
    return AgentConfig(
        name=str(value["name"]),
        command=str(value["command"]),
        args=tuple(str(item) for item in value.get("args", ())),
        environment=_environment(value.get("environment")),
        supervisor_driver=_optional_config_string(value.get("supervisor_driver")),
        worker_driver=str(value.get("worker_driver", "command")),
        critic_driver=_optional_config_string(value.get("critic_driver")),
        worker_process_profile=_process_profile(
            value.get("worker_process_profile"), "agent.worker_process_profile"
        ),
    )


def _a2a_agent(value: Any) -> A2AAgentConfig:
    if not isinstance(value, dict):
        raise ValueError("a2a_agents entries must be objects.")
    return A2AAgentConfig(
        name=str(value["name"]),
        card_url=str(value["card_url"]),
        roles=tuple(str(item) for item in value.get("roles", ("supervisor", "worker", "critic"))),
        auth_kind=str(value.get("auth_kind", "none")),
        credential_env=_optional_config_string(value.get("credential_env")),
        api_key_header=_optional_config_string(value.get("api_key_header")),
        oauth_flow=str(value.get("oauth_flow", "client_credentials")),
        client_id_env=_optional_config_string(value.get("client_id_env")),
        client_secret_env=_optional_config_string(value.get("client_secret_env")),
        token_url=_optional_config_string(value.get("token_url")),
        device_authorization_url=_optional_config_string(value.get("device_authorization_url")),
        oidc_discovery_url=_optional_config_string(value.get("oidc_discovery_url")),
        scopes=tuple(str(item) for item in value.get("scopes", ())),
        client_auth_method=str(value.get("client_auth_method", "basic")),
        prefer_streaming=bool(value.get("prefer_streaming", True)),
        push_callback_url=_optional_config_string(value.get("push_callback_url")),
        push_token_env=_optional_config_string(value.get("push_token_env")),
        timeout_seconds=float(value.get("timeout_seconds", 300.0)),
        poll_interval_seconds=float(value.get("poll_interval_seconds", 1.0)),
    )


def _process_profile(value: Any, label: str) -> ProcessProfileConfig | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return ProcessProfileConfig(
        profile_id=str(value.get("profile_id", "")),
        prompt_transport=str(value.get("prompt_transport", "stdin")),
        prompt_argument=_optional_config_string(value.get("prompt_argument")),
        required_args=tuple(str(item) for item in value.get("required_args", ())),
        forbidden_args=tuple(str(item) for item in value.get("forbidden_args", ())),
        model_argument=_optional_config_string(value.get("model_argument")),
        version_args=tuple(str(item) for item in value.get("version_args", ("--version",))),
    )


def _argument_key(value: str) -> str:
    return value.split("=", 1)[0].strip().casefold()


def validate_process_arguments(
    configured_args: tuple[str, ...],
    profile: ProcessProfileConfig,
) -> None:
    sensitive = {"--api-key", "--apikey", "--password", "--secret", "--token"}
    configured = {_argument_key(item) for item in configured_args}
    required = {_argument_key(item) for item in profile.required_args}
    forbidden = {_argument_key(item) for item in profile.forbidden_args} | sensitive
    owned = {
        _argument_key(item)
        for item in (profile.prompt_argument, profile.model_argument)
        if item is not None
    }
    hits = sorted((configured & (forbidden | owned)) | (required & (sensitive | owned)))
    if hits:
        raise ValueError(
            "process args contain forbidden, secret-bearing, or HoH-owned flags: "
            + ", ".join(hits)
        )


def _environment(value: Any) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise ValueError("environment must be a string-to-string mapping.")
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise ValueError("environment must contain only string keys and values.")
    return tuple(sorted(value.items()))


def _valid_http_header_name(value: str) -> bool:
    allowed = "!#$%&'*+-.^_`|~"
    return bool(value) and all(character.isalnum() or character in allowed for character in value)


def _optional_url(value: str | None, label: str) -> str | None:
    normalized = (value or "").strip() or None
    if normalized is None:
        return None
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{label} must be an absolute HTTP(S) URL without credentials.")
    if parsed.scheme == "http" and parsed.hostname.casefold() not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(f"{label} requires HTTPS except on loopback.")
    return normalized


def _driver_value(value: dict[str, Any], role: str, default: str) -> str:
    driver = value.get("driver")
    legacy_type = value.get("type")
    if driver is not None and legacy_type is not None:
        resolved_driver = resolve_driver(str(driver), role).driver_id
        resolved_legacy = resolve_driver(str(legacy_type), role).driver_id
        if resolved_driver != resolved_legacy:
            raise ValueError(f"{role}.driver and legacy {role}.type must identify the same driver.")
    return str(driver if driver is not None else legacy_type if legacy_type is not None else default)


def _local_model(value: Any) -> LocalModelEndpointTarget:
    return LocalModelEndpointTarget(
        name=str(value["name"]),
        base_url=str(value["base_url"]),
        model=str(value["model"]),
        protocol=str(value.get("protocol", "openai_compatible")),
    )
