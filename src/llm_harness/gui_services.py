from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Callable, Mapping

from .config import (
    A2AAgentConfig,
    AgentConfig,
    CloudModelEndpoint,
    HarnessConfig,
    MetricsConfig,
    ModelPriceConfig,
    ProcessProfileConfig,
    RuntimeConfig,
    SchedulerConfig,
    config_payload,
    load_config,
    project_config_path,
    write_config,
)
from .acp_registry import find_registry_agent, load_acp_registry, refresh_acp_registry, resolve_acp_launch
from .agent_auth import (
    AgentAuthenticationError,
    AcpAuthService,
    AgentAuthInspection,
    AgentAuthMethod,
    LoginResult,
    vendor_login,
    vendor_login_profile,
)
from .agent_installation import ManagedAgentInstaller, ManagedAgentStatus
from .a2a import A2AClient, connection_from_role, probe_a2a_agent
from .oauth import DeviceAuthorization
from .mcp import McpServerConfig, RoleMcpPolicyConfig, SecretBinding
from .durable_io import atomic_write_json, read_json
from .role_catalog import build_role_catalog
from .role_profiles import (
    ProjectRoleProfile,
    RoleSelection,
    load_role_profile,
    write_role_profile,
)
from .supervisor_protocol import SupervisorStatusReport, build_supervisor_status
from .state import HohStateStore, default_state_root
from .trust import parse_worker_trust_level
from .background_scheduler import ScheduledProjectResult, run_due_projects, run_registered_project
from .desktop_notifications import DesktopNotifier
from .workspace import ProjectQueueSummary, WorkspaceRegistry, WorkspaceProject, workspace_registry_path
from .scheduler_service import SchedulerServiceResult, manage_scheduler_service
from .secret_store import SecretStore, SecretStoreError, resolve_credential, secret_store_path
from .signed_catalog import import_publisher, publisher_trust_path
from .updater import check_for_update, download_release, install_release, installation_root
from . import __version__


SUPPORTED_LOCALES = ("ru", "en")
SUPPORTED_THEMES = ("light", "dark")


@dataclass(frozen=True)
class GuiSettings:
    locale: str = "ru"
    theme: str = "dark"
    last_project_root: str = ""
    onboarding_completed: bool = False
    update_catalog_url: str = ""
    automatic_updates: bool = False
    window_geometry: str = ""

    def __post_init__(self) -> None:
        if self.locale not in SUPPORTED_LOCALES:
            raise ValueError(f"Unsupported GUI locale: {self.locale}")
        if self.theme not in SUPPORTED_THEMES:
            raise ValueError(f"Unsupported GUI theme: {self.theme}")


@dataclass(frozen=True)
class ProjectSnapshot:
    root: Path
    config: HarnessConfig
    config_path: Path
    profile: ProjectRoleProfile | None
    catalog: dict[str, Any]
    status: SupervisorStatusReport


@dataclass(frozen=True)
class GuiCommandResult:
    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.return_code == 0


@dataclass(frozen=True)
class EditableProjectSettings:
    supervisor_provider: str
    supervisor_model: str
    supervisor_base_url: str = ""
    supervisor_api_key_env: str = ""
    verifier_provider: str = "stub"
    verifier_model: str = "verifier-stub"
    verifier_base_url: str = ""
    verifier_api_key_env: str = ""
    require_embedded_python: bool = True
    embedded_python_path: str = "runtime/python/python.exe"
    wheels_path: str = "vendor/wheels"
    require_tests: bool = True
    worker_trust_level: str = "patch_only"
    max_parallel_tasks: int = 1


@dataclass(frozen=True)
class EditableProcessProfile:
    name: str
    command: str
    args: tuple[str, ...] = ()
    prompt_transport: str = "stdin"
    prompt_argument: str = ""
    required_args: tuple[str, ...] = ()
    forbidden_args: tuple[str, ...] = ()
    model_argument: str = ""
    version_args: tuple[str, ...] = ("--version",)


@dataclass(frozen=True)
class EditableA2AAgent:
    name: str
    card_url: str
    roles: tuple[str, ...] = ("supervisor", "worker", "critic")
    auth_kind: str = "none"
    credential_env: str = ""
    api_key_header: str = ""
    oauth_flow: str = "client_credentials"
    client_id_env: str = ""
    client_secret_env: str = ""
    token_url: str = ""
    device_authorization_url: str = ""
    oidc_discovery_url: str = ""
    scopes: tuple[str, ...] = ()
    client_auth_method: str = "basic"
    prefer_streaming: bool = True
    push_callback_url: str = ""
    push_token_env: str = ""
    timeout_seconds: float = 300.0
    poll_interval_seconds: float = 1.0


@dataclass(frozen=True)
class EditableModelPrice:
    provider: str
    model: str
    input_per_million: float
    output_per_million: float
    currency: str = "USD"


@dataclass(frozen=True)
class EditableMcpServer:
    name: str
    transport: str = "stdio"
    command: str = ""
    args: tuple[str, ...] = ()
    url: str = ""
    environment: tuple[tuple[str, str], ...] = ()
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class EditableRoleMcpPolicy:
    role: str
    permission_mode: str
    allowed_tool_kinds: tuple[str, ...]
    servers: tuple[EditableMcpServer, ...]


@dataclass(frozen=True)
class GuiTaskChoice:
    task_id: str
    status: str
    title: str


def gui_settings_path(environment: Mapping[str, str] | None = None) -> Path:
    env = environment if environment is not None else os.environ
    override = env.get("HOH_GUI_SETTINGS")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and env.get("LOCALAPPDATA"):
        return Path(env["LOCALAPPDATA"]) / "HoH" / "gui-settings.json"
    root = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    return root / "hoh" / "gui-settings.json"


def load_gui_settings(path: Path | None = None) -> GuiSettings:
    target = path or gui_settings_path()
    payload = read_json(target, default={})
    if not isinstance(payload, dict):
        raise ValueError("GUI settings must contain a JSON object.")
    last_project_root = _existing_directory_or_empty(payload.get("last_project_root", ""))
    return GuiSettings(
        locale=str(payload.get("locale", "ru")),
        theme=str(payload.get("theme", "dark")),
        last_project_root=last_project_root,
        onboarding_completed=bool(payload.get("onboarding_completed", False)),
        update_catalog_url=str(payload.get("update_catalog_url", "")),
        automatic_updates=bool(payload.get("automatic_updates", False)),
        window_geometry=str(payload.get("window_geometry", "")),
    )


def _existing_directory_or_empty(value: Any) -> str:
    """Return a remembered directory only while it still exists.

    The remembered path is a convenience for startup, not an explicit user
    selection. Clearing only paths whose directory has disappeared prevents a
    deleted temporary workspace from triggering an error dialog on every
    launch while preserving validation for existing non-Git paths when the
    user explicitly loads them.
    """
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    try:
        return candidate if Path(candidate).expanduser().is_dir() else ""
    except (OSError, ValueError):
        return ""


def save_gui_settings(settings: GuiSettings, path: Path | None = None) -> Path:
    target = path or gui_settings_path()
    atomic_write_json(
        target,
        {
            "locale": settings.locale,
            "theme": settings.theme,
            "last_project_root": settings.last_project_root,
            "onboarding_completed": settings.onboarding_completed,
            "update_catalog_url": settings.update_catalog_url,
            "automatic_updates": settings.automatic_updates,
            "window_geometry": settings.window_geometry,
        },
    )
    return target


def application_update_status(source: str) -> dict[str, Any]:
    if not source.strip():
        raise ValueError("A signed release catalog HTTPS URL is required.")
    artifact, catalog = check_for_update(source.strip(), __version__)
    return {
        "current_version": __version__,
        "update_available": artifact is not None,
        "version": artifact.version if artifact is not None else __version__,
        "bytes": artifact.bytes if artifact is not None else 0,
        "generated_at_utc": catalog.get("generated_at_utc"),
    }


def install_application_update(source: str) -> Path:
    if not source.strip():
        raise ValueError("A signed release catalog HTTPS URL is required.")
    artifact, _catalog = check_for_update(source.strip(), __version__)
    if artifact is None:
        raise ValueError("No newer trusted release is available.")
    root = installation_root()
    package = root / "downloads" / f"HoH-{artifact.version}-{artifact.platform}.zip"
    download_release(artifact, package)
    return install_release(package, artifact, root=root)


def trust_publisher_identity(path: Path) -> str:
    return import_publisher(path, publisher_trust_path())


def load_project_snapshot(project_root: Path, *, refresh_registry: bool = False) -> ProjectSnapshot:
    root = _validated_project_root(project_root)
    path = project_config_path(root)
    config = load_config(path) if path.exists() else HarnessConfig()
    return ProjectSnapshot(
        root=root,
        config=config,
        config_path=path,
        profile=load_role_profile(root),
        catalog=build_role_catalog(root, config, refresh_registry=refresh_registry),
        status=build_supervisor_status(root),
    )


def save_project_roles(
    project_root: Path,
    *,
    supervisor_agent: str,
    supervisor_model: str | None,
    supervisor_driver: str | None,
    worker_agent: str,
    worker_model: str | None,
    worker_driver: str | None,
    critic_enabled: bool,
    critic_agent: str | None,
    critic_model: str | None,
    critic_driver: str | None,
    max_attempts: int,
) -> Path:
    root = _validated_project_root(project_root)
    if critic_enabled and not (critic_agent or "").strip():
        raise ValueError("Critic agent is required when Critic is enabled.")
    profile = ProjectRoleProfile(
        supervisor=RoleSelection(
            supervisor_agent.strip(), _optional(supervisor_model), _optional(supervisor_driver)
        ),
        worker=RoleSelection(worker_agent.strip(), _optional(worker_model), _optional(worker_driver)),
        critic=(
            RoleSelection(
                (critic_agent or "").strip(), _optional(critic_model), _optional(critic_driver)
            )
            if critic_enabled
            else None
        ),
        max_attempts=max_attempts,
    )
    # Resolve the complete profile before persisting it so unavailable role/driver combinations fail early.
    snapshot = load_project_snapshot(root)
    _validate_role_selection(snapshot.catalog, "supervisor", profile.supervisor)
    _validate_role_selection(snapshot.catalog, "worker", profile.worker)
    if profile.critic is not None:
        _validate_role_selection(snapshot.catalog, "critic", profile.critic)
    from .role_profiles import apply_role_profile

    apply_role_profile(snapshot.config, profile, root.name)
    return write_role_profile(root, profile)


def editable_project_settings(config: HarnessConfig) -> EditableProjectSettings:
    return EditableProjectSettings(
        supervisor_provider=config.supervisor_model.provider,
        supervisor_model=config.supervisor_model.model,
        supervisor_base_url=config.supervisor_model.base_url or "",
        supervisor_api_key_env=config.supervisor_model.api_key_env or "",
        verifier_provider=config.verifier_model.provider,
        verifier_model=config.verifier_model.model,
        verifier_base_url=config.verifier_model.base_url or "",
        verifier_api_key_env=config.verifier_model.api_key_env or "",
        require_embedded_python=config.runtime.require_embedded_python,
        embedded_python_path=config.runtime.embedded_python_path,
        wheels_path=config.runtime.wheels_path,
        require_tests=config.require_tests,
        worker_trust_level=config.worker_trust_level.value,
        max_parallel_tasks=config.scheduler.max_parallel_tasks,
    )


def save_editable_project_settings(project_root: Path, values: EditableProjectSettings) -> Path:
    root = _validated_project_root(project_root)
    path = project_config_path(root)
    config = load_config(path) if path.exists() else HarnessConfig()
    supervisor_model = _updated_endpoint(
        config.supervisor_model,
        values.supervisor_provider,
        values.supervisor_model,
        values.supervisor_base_url,
        values.supervisor_api_key_env,
    )
    verifier_model = _updated_endpoint(
        config.verifier_model,
        values.verifier_provider,
        values.verifier_model,
        values.verifier_base_url,
        values.verifier_api_key_env,
    )
    updated = replace(
        config,
        supervisor_model=supervisor_model,
        verifier_model=verifier_model,
        runtime=RuntimeConfig(
            require_embedded_python=values.require_embedded_python,
            embedded_python_path=values.embedded_python_path.strip(),
            wheels_path=values.wheels_path.strip(),
        ),
        scheduler=SchedulerConfig(max_parallel_tasks=values.max_parallel_tasks),
        require_tests=values.require_tests,
        worker_trust_level=parse_worker_trust_level(values.worker_trust_level),
    )
    return write_config(path, updated)


def editable_role_mcp_policy(config: HarnessConfig, role: str) -> EditableRoleMcpPolicy:
    normalized = _mcp_role(role)
    policy = getattr(config, normalized).mcp
    return EditableRoleMcpPolicy(
        role=normalized,
        permission_mode=policy.permission_mode,
        allowed_tool_kinds=policy.allowed_tool_kinds,
        servers=tuple(
            EditableMcpServer(
                name=item.name,
                transport=item.transport,
                command=item.command or "",
                args=item.args,
                url=item.url or "",
                environment=tuple((binding.name, binding.source_env) for binding in item.environment),
                headers=tuple((binding.name, binding.source_env) for binding in item.headers),
            )
            for item in policy.servers
        ),
    )


def save_role_mcp_policy(
    project_root: Path,
    role: str,
    *,
    permission_mode: str,
    allowed_tool_kinds: tuple[str, ...],
) -> Path:
    root, path, config, normalized, current = _mcp_context(project_root, role)
    del root
    policy = RoleMcpPolicyConfig(permission_mode, allowed_tool_kinds, current.mcp.servers)
    return write_config(path, _replace_role_mcp(config, normalized, policy))


def save_mcp_server(project_root: Path, role: str, values: EditableMcpServer) -> Path:
    root, path, config, normalized, current = _mcp_context(project_root, role)
    del root
    server = McpServerConfig(
        name=values.name,
        transport=values.transport,
        command=_optional(values.command),
        args=values.args,
        url=_optional(values.url),
        environment=tuple(SecretBinding(name, source) for name, source in values.environment),
        headers=tuple(SecretBinding(name, source) for name, source in values.headers),
    )
    servers = list(current.mcp.servers)
    index = next(
        (position for position, item in enumerate(servers) if item.name.casefold() == server.name.casefold()),
        None,
    )
    if index is None:
        servers.append(server)
    else:
        servers[index] = server
    policy = replace(current.mcp, servers=tuple(servers))
    return write_config(path, _replace_role_mcp(config, normalized, policy))


def remove_mcp_server(project_root: Path, role: str, name: str) -> Path:
    _root, path, config, normalized, current = _mcp_context(project_root, role)
    folded = name.strip().casefold()
    if not any(item.name.casefold() == folded for item in current.mcp.servers):
        raise ValueError(f"Unknown MCP server for {normalized}: {name}")
    policy = replace(
        current.mcp,
        servers=tuple(item for item in current.mcp.servers if item.name.casefold() != folded),
    )
    return write_config(path, _replace_role_mcp(config, normalized, policy))


def _mcp_context(project_root: Path, role: str):
    root = _validated_project_root(project_root)
    path = project_config_path(root)
    config = load_config(path) if path.exists() else HarnessConfig()
    normalized = _mcp_role(role)
    return root, path, config, normalized, getattr(config, normalized)


def _mcp_role(role: str) -> str:
    normalized = role.strip().casefold()
    if normalized not in {"supervisor", "worker", "critic"}:
        raise ValueError("MCP role must be supervisor, worker, or critic.")
    return normalized


def _replace_role_mcp(
    config: HarnessConfig,
    role: str,
    policy: RoleMcpPolicyConfig,
) -> HarnessConfig:
    current = getattr(config, role)
    return replace(config, **{role: replace(current, mcp=policy)})


def editable_process_profiles(config: HarnessConfig) -> tuple[EditableProcessProfile, ...]:
    return tuple(
        EditableProcessProfile(
            name=agent.name,
            command=agent.command,
            args=agent.args,
            prompt_transport=agent.worker_process_profile.prompt_transport,
            prompt_argument=agent.worker_process_profile.prompt_argument or "",
            required_args=agent.worker_process_profile.required_args,
            forbidden_args=agent.worker_process_profile.forbidden_args,
            model_argument=agent.worker_process_profile.model_argument or "",
            version_args=agent.worker_process_profile.version_args,
        )
        for agent in config.agents
        if agent.worker_driver == "process" and agent.worker_process_profile is not None
    )


def save_process_profile(project_root: Path, values: EditableProcessProfile) -> Path:
    root = _validated_project_root(project_root)
    path = project_config_path(root)
    config = load_config(path) if path.exists() else HarnessConfig()
    name = values.name.strip()
    command = values.command.strip()
    if not name or not command:
        raise ValueError("Process profile name and executable command are required.")
    profile = ProcessProfileConfig(
        profile_id=name,
        prompt_transport=values.prompt_transport,
        prompt_argument=_optional(values.prompt_argument),
        required_args=tuple(values.required_args),
        forbidden_args=tuple(values.forbidden_args),
        model_argument=_optional(values.model_argument),
        version_args=tuple(values.version_args),
    )
    agents = list(config.agents)
    existing = next(
        (index for index, item in enumerate(agents) if item.name.casefold() == name.casefold()),
        None,
    )
    if existing is None:
        replacement = AgentConfig(
            name=name,
            command=command,
            args=tuple(values.args),
            worker_driver="process",
            worker_process_profile=profile,
        )
        agents.append(replacement)
    else:
        current = agents[existing]
        if current.worker_driver != "process":
            raise ValueError(f"Agent '{current.name}' already exists and is not a process profile.")
        replacement = AgentConfig(
            name=name,
            command=command,
            args=tuple(values.args),
            environment=current.environment,
            supervisor_driver=current.supervisor_driver,
            worker_driver="process",
            critic_driver=current.critic_driver,
            worker_process_profile=profile,
        )
        agents[existing] = replacement
    return write_config(path, replace(config, agents=tuple(agents)))


def remove_process_profile(project_root: Path, name: str) -> Path:
    root = _validated_project_root(project_root)
    path = project_config_path(root)
    config = load_config(path) if path.exists() else HarnessConfig()
    normalized = name.strip().casefold()
    matching = next(
        (item for item in config.agents if item.name.casefold() == normalized),
        None,
    )
    if matching is None or matching.worker_driver != "process":
        raise ValueError(f"Unknown process profile: {name}")
    role_profile = load_role_profile(root)
    if role_profile is not None and role_profile.worker.agent.casefold() == normalized:
        raise ValueError("Select and save a different Worker before removing its process profile.")
    updated = replace(
        config,
        agents=tuple(item for item in config.agents if item.name.casefold() != normalized),
    )
    return write_config(path, updated)


def editable_a2a_agents(config: HarnessConfig) -> tuple[EditableA2AAgent, ...]:
    return tuple(
        EditableA2AAgent(
            name=item.name,
            card_url=item.card_url,
            roles=item.roles,
            auth_kind=item.auth_kind,
            credential_env=item.credential_env or "",
            api_key_header=item.api_key_header or "",
            oauth_flow=item.oauth_flow,
            client_id_env=item.client_id_env or "",
            client_secret_env=item.client_secret_env or "",
            token_url=item.token_url or "",
            device_authorization_url=item.device_authorization_url or "",
            oidc_discovery_url=item.oidc_discovery_url or "",
            scopes=item.scopes,
            client_auth_method=item.client_auth_method,
            prefer_streaming=item.prefer_streaming,
            push_callback_url=item.push_callback_url or "",
            push_token_env=item.push_token_env or "",
            timeout_seconds=item.timeout_seconds,
            poll_interval_seconds=item.poll_interval_seconds,
        )
        for item in config.a2a_agents
    )


def save_a2a_agent(project_root: Path, values: EditableA2AAgent) -> Path:
    root = _validated_project_root(project_root)
    path = project_config_path(root)
    config = load_config(path) if path.exists() else HarnessConfig()
    replacement = A2AAgentConfig(
        name=values.name,
        card_url=values.card_url,
        roles=values.roles,
        auth_kind=values.auth_kind,
        credential_env=_optional(values.credential_env),
        api_key_header=_optional(values.api_key_header),
        oauth_flow=values.oauth_flow,
        client_id_env=_optional(values.client_id_env),
        client_secret_env=_optional(values.client_secret_env),
        token_url=_optional(values.token_url),
        device_authorization_url=_optional(values.device_authorization_url),
        oidc_discovery_url=_optional(values.oidc_discovery_url),
        scopes=values.scopes,
        client_auth_method=values.client_auth_method,
        prefer_streaming=values.prefer_streaming,
        push_callback_url=_optional(values.push_callback_url),
        push_token_env=_optional(values.push_token_env),
        timeout_seconds=values.timeout_seconds,
        poll_interval_seconds=values.poll_interval_seconds,
    )
    conflicts = [item for item in config.agents if item.name.casefold() == replacement.name.casefold()]
    if conflicts:
        raise ValueError(f"A local agent already uses the name '{replacement.name}'.")
    agents = list(config.a2a_agents)
    index = next(
        (position for position, item in enumerate(agents) if item.name.casefold() == replacement.name.casefold()),
        None,
    )
    if index is None:
        agents.append(replacement)
    else:
        agents[index] = replacement
    return write_config(path, replace(config, a2a_agents=tuple(agents)))


def remove_a2a_agent(project_root: Path, name: str) -> Path:
    root = _validated_project_root(project_root)
    path = project_config_path(root)
    config = load_config(path) if path.exists() else HarnessConfig()
    folded = name.strip().casefold()
    matching = next((item for item in config.a2a_agents if item.name.casefold() == folded), None)
    if matching is None:
        raise ValueError(f"Unknown A2A agent: {name}")
    profile = load_role_profile(root)
    selected = (
        profile is not None
        and any(
            selection is not None and selection.agent.casefold() == folded
            for selection in (profile.supervisor, profile.worker, profile.critic)
        )
    )
    if selected:
        raise ValueError("Select and save different roles before removing this A2A agent.")
    return write_config(
        path,
        replace(config, a2a_agents=tuple(item for item in config.a2a_agents if item.name.casefold() != folded)),
    )


def test_a2a_agent(values: EditableA2AAgent) -> tuple[bool, str, str | None]:
    candidate = A2AAgentConfig(
        name=values.name,
        card_url=values.card_url,
        roles=values.roles,
        auth_kind=values.auth_kind,
        credential_env=_optional(values.credential_env),
        api_key_header=_optional(values.api_key_header),
        oauth_flow=values.oauth_flow,
        client_id_env=_optional(values.client_id_env),
        client_secret_env=_optional(values.client_secret_env),
        token_url=_optional(values.token_url),
        device_authorization_url=_optional(values.device_authorization_url),
        oidc_discovery_url=_optional(values.oidc_discovery_url),
        scopes=values.scopes,
        client_auth_method=values.client_auth_method,
        prefer_streaming=values.prefer_streaming,
        push_callback_url=_optional(values.push_callback_url),
        push_token_env=_optional(values.push_token_env),
        timeout_seconds=values.timeout_seconds,
        poll_interval_seconds=values.poll_interval_seconds,
    )
    return probe_a2a_agent(
        connection_from_role(candidate.card_url, candidate.environment, candidate.timeout_seconds)
    )


def authorize_a2a_agent(
    values: EditableA2AAgent,
    on_user_code: Callable[[DeviceAuthorization], None],
) -> str:
    candidate = A2AAgentConfig(
        name=values.name,
        card_url=values.card_url,
        roles=values.roles,
        auth_kind=values.auth_kind,
        credential_env=_optional(values.credential_env),
        api_key_header=_optional(values.api_key_header),
        oauth_flow=values.oauth_flow,
        client_id_env=_optional(values.client_id_env),
        client_secret_env=_optional(values.client_secret_env),
        token_url=_optional(values.token_url),
        device_authorization_url=_optional(values.device_authorization_url),
        oidc_discovery_url=_optional(values.oidc_discovery_url),
        scopes=values.scopes,
        client_auth_method=values.client_auth_method,
        prefer_streaming=values.prefer_streaming,
        push_callback_url=_optional(values.push_callback_url),
        push_token_env=_optional(values.push_token_env),
        timeout_seconds=values.timeout_seconds,
        poll_interval_seconds=values.poll_interval_seconds,
    )
    client = A2AClient(connection_from_role(candidate.card_url, candidate.environment, candidate.timeout_seconds))
    if candidate.auth_kind not in {"oauth2", "oidc"}:
        raise ValueError("A2A login is available only for OAuth2 or OIDC connections.")
    if candidate.oauth_flow == "device_code":
        client.authorize_device(on_user_code)
        return "OAuth/OIDC device authorization completed for this HoH process."
    # Client credentials are non-interactive. A discovery/test request obtains
    # and caches the access token without exposing it to the UI.
    client.authenticate()
    return "OAuth client-credentials token acquired for this HoH process."


def registry_agent_statuses(
    *, refresh: bool = False, fetch_if_missing: bool = False
) -> tuple[tuple[Any, ManagedAgentStatus], ...]:
    """Which Registry agents exist and whether each is installed for this user.

    `fetch_if_missing` is for the first run: a machine that has never cached the
    Registry should not be told to go and refresh something before it can install
    the team it just chose.
    """
    registry = refresh_acp_registry() if refresh else load_acp_registry()
    if registry is None and fetch_if_missing:
        registry = refresh_acp_registry()
    if registry is None:
        raise ValueError("ACP Registry is not cached. Refresh the registry first.")
    installer = ManagedAgentInstaller()
    return tuple((agent, installer.status(agent)) for agent in registry.agents)


def install_registry_agent(agent_id: str) -> str:
    agent = _registry_agent(agent_id)
    launch = ManagedAgentInstaller().install(agent)
    return launch.detail


def uninstall_registry_agent(agent_id: str) -> bool:
    agent = _registry_agent(agent_id)
    return ManagedAgentInstaller().uninstall(agent)


def inspect_registry_agent_auth(agent_id: str) -> AgentAuthInspection:
    agent = _registry_agent(agent_id)
    launch = resolve_acp_launch(agent)
    if not launch.available:
        raise ValueError(f"Agent is unavailable: {launch.detail}")
    return AcpAuthService().inspect(launch.command, launch.args, launch.environment)


def login_registry_agent(agent_id: str, method_id: str | None = None) -> LoginResult:
    agent = _registry_agent(agent_id)
    launch = resolve_acp_launch(agent)
    if not launch.available:
        raise ValueError(f"Agent is unavailable: {launch.detail}")
    service = AcpAuthService()
    try:
        inspection = service.inspect(launch.command, launch.args, launch.environment)
    except AgentAuthenticationError:
        profile = vendor_login_profile(agent.id)
        if profile is None:
            raise
        return vendor_login(profile)
    if not inspection.methods:
        profile = vendor_login_profile(agent.id)
        if profile is None:
            raise ValueError("The ACP agent advertised no authentication methods and has no vendor login profile.")
        return vendor_login(profile)
    selected = next((item for item in inspection.methods if item.method_id == method_id), None)
    if selected is None:
        if method_id is None and len(inspection.methods) == 1:
            selected = inspection.methods[0]
        else:
            raise ValueError("Select one of the currently advertised ACP authentication methods.")
    return service.login(launch.command, launch.args, selected, launch.environment)


def set_session_auth_variables(method: AgentAuthMethod, values: Mapping[str, str]) -> tuple[str, ...]:
    allowed = {name for name, _label, _secret, _optional_value in method.variables}
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError("Unknown ACP authentication variables: " + ", ".join(unknown))
    required = {
        name for name, _label, _secret, optional_value in method.variables if not optional_value
    }
    missing = sorted(name for name in required if not values.get(name))
    if missing:
        raise ValueError("Missing ACP authentication values: " + ", ".join(missing))
    for name, value in values.items():
        if value:
            os.environ[name] = value
    return tuple(sorted(name for name, value in values.items() if value))


def _registry_agent(agent_id: str):
    registry = load_acp_registry()
    if registry is None:
        raise ValueError("ACP Registry is not cached.")
    agent = find_registry_agent(registry, agent_id)
    if agent is None:
        raise ValueError(f"Unknown ACP Registry agent: {agent_id}")
    return agent


def set_session_environment_variables(
    config: HarnessConfig,
    values: Mapping[str, str],
) -> tuple[str, ...]:
    allowed = {name for name, _available, _label in environment_variable_status(config)}
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError("Unknown environment variable names: " + ", ".join(unknown))
    empty = sorted(name for name, value in values.items() if not value)
    if empty:
        raise ValueError("Secret values cannot be empty: " + ", ".join(empty))
    for name, value in values.items():
        os.environ[name] = value
    return tuple(sorted(values))


def queue_task_choices(project_root: Path) -> tuple[GuiTaskChoice, ...]:
    root = _validated_project_root(project_root)
    store = HohStateStore(default_state_root(root))
    latest: dict[str, Any] = {}
    for task in store.list_tasks():
        latest[task.work_item.id] = task
    return tuple(
        GuiTaskChoice(task.work_item.id, task.status, task.work_item.title)
        for task in latest.values()
    )


def usage_metrics_summary(project_root: Path) -> dict[str, Any]:
    root = _validated_project_root(project_root)
    return HohStateStore(default_state_root(root)).metrics.summary()


def workspace_project_summaries() -> tuple[ProjectQueueSummary, ...]:
    return WorkspaceRegistry().summaries()


def register_workspace_project(project_root: Path, name: str | None = None) -> WorkspaceProject:
    return WorkspaceRegistry().register(_validated_project_root(project_root), name)


def remove_workspace_project(project_id: str) -> WorkspaceProject:
    return WorkspaceRegistry().remove(project_id)


def configure_workspace_schedule(
    project_id: str,
    *,
    enabled: bool,
    interval_minutes: int,
    final_audit: bool,
    notify_windows: bool,
    notify_telegram: bool,
    start_immediately: bool = False,
) -> WorkspaceProject:
    return WorkspaceRegistry().configure_schedule(
        project_id,
        enabled=enabled,
        interval_minutes=interval_minutes,
        final_audit=final_audit,
        notify_windows=notify_windows,
        notify_telegram=notify_telegram,
        start_immediately=start_immediately,
    )


def run_workspace_project(project_id: str) -> ScheduledProjectResult:
    registry = WorkspaceRegistry()
    project = next((item for item in registry.load().projects if item.project_id == project_id), None)
    if project is None:
        raise ValueError(f"Unknown workspace project: {project_id}")
    result = run_registered_project(project, desktop_notifier=DesktopNotifier())
    error = " | ".join(
        item for item in (result.stderr.strip(), *result.notification_errors) if item
    ) or None
    registry.record_run(project.project_id, result.status, error)
    return result


def run_due_workspace_projects() -> tuple[ScheduledProjectResult, ...]:
    return run_due_projects(WorkspaceRegistry(), desktop_notifier=DesktopNotifier())


def workspace_scheduler_service(action: str, every_minutes: int = 1) -> SchedulerServiceResult:
    return manage_scheduler_service(action, every_minutes=every_minutes)


def editable_model_prices(config: HarnessConfig) -> tuple[EditableModelPrice, ...]:
    return tuple(
        EditableModelPrice(
            item.provider,
            item.model,
            item.input_per_million,
            item.output_per_million,
            item.currency,
        )
        for item in config.metrics.prices
    )


def save_model_price(project_root: Path, values: EditableModelPrice) -> Path:
    root = _validated_project_root(project_root)
    path = project_config_path(root)
    config = load_config(path) if path.exists() else HarnessConfig()
    replacement = ModelPriceConfig(
        values.provider,
        values.model,
        values.input_per_million,
        values.output_per_million,
        values.currency,
    )
    prices = list(config.metrics.prices)
    index = next(
        (
            position for position, item in enumerate(prices)
            if item.provider == replacement.provider and item.model.casefold() == replacement.model.casefold()
        ),
        None,
    )
    if index is None:
        prices.append(replacement)
    else:
        prices[index] = replacement
    return write_config(path, replace(config, metrics=MetricsConfig(tuple(prices))))


def remove_model_price(project_root: Path, provider: str, model: str) -> Path:
    root = _validated_project_root(project_root)
    path = project_config_path(root)
    config = load_config(path) if path.exists() else HarnessConfig()
    folded = (provider.strip().casefold(), model.strip().casefold())
    remaining = tuple(
        item for item in config.metrics.prices
        if (item.provider, item.model.casefold()) != folded
    )
    if len(remaining) == len(config.metrics.prices):
        raise ValueError(f"Unknown model price: {provider}/{model}")
    return write_config(path, replace(config, metrics=MetricsConfig(remaining)))


def save_credential(name: str, value: str) -> Path:
    """Keep a provider key in the user-scoped store instead of asking for a shell export."""
    return SecretStore().set(name, value)


def forget_credential(name: str) -> bool:
    return SecretStore().delete(name)


def stored_credential_names() -> tuple[str, ...]:
    try:
        return SecretStore().names()
    except SecretStoreError:
        return ()


def environment_variable_status(config: HarnessConfig) -> tuple[tuple[str, bool, str], ...]:
    records: list[tuple[str, bool, str]] = []
    for label, endpoint in (("Supervisor", config.supervisor_model), ("Verifier", config.verifier_model)):
        if endpoint.provider == "stub":
            continue
        default_names = {"openai": "OPENAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}
        name = endpoint.api_key_env or default_names.get(endpoint.provider, "")
        if name:
            records.append((name, bool(resolve_credential(name)), label))
    for remote in config.a2a_agents:
        for name in (
            remote.credential_env,
            remote.client_id_env,
            remote.client_secret_env,
            remote.push_token_env,
        ):
            if not name:
                continue
            records.append(
                (name, bool(resolve_credential(name)), f"A2A {remote.name}")
            )
    for role in ("supervisor", "worker", "critic"):
        policy = getattr(config, role).mcp
        for server in policy.servers:
            for binding in (*server.environment, *server.headers):
                records.append(
                    (
                        binding.source_env,
                        bool(os.environ.get(binding.source_env)),
                        f"MCP {role}/{server.name}",
                    )
                )
    deduplicated: dict[str, tuple[bool, str]] = {}
    for name, available, label in records:
        current = deduplicated.get(name)
        deduplicated[name] = (available or (current[0] if current else False), label if current is None else current[1] + ", " + label)
    return tuple((name, available, label) for name, (available, label) in deduplicated.items())


def project_config_text(project_root: Path) -> str:
    root = _validated_project_root(project_root)
    path = project_config_path(root)
    config = load_config(path) if path.exists() else HarnessConfig()
    return json.dumps(config_payload(config), indent=2, ensure_ascii=False) + "\n"


def save_project_config_text(project_root: Path, text: str) -> Path:
    root = _validated_project_root(project_root)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid project configuration JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Project configuration must contain a JSON object.")
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "harness.json"
        candidate.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        validated = load_config(candidate)
    return write_config(project_config_path(root), validated)


def run_gui_command(
    project_root: Path,
    operation: str,
    *,
    task_id: str | None = None,
    reason: str | None = None,
    timeout_seconds: float = 900.0,
) -> GuiCommandResult:
    root = _validated_project_root(project_root)
    if operation in {"run_next", "run_all"}:
        _validate_saved_role_profile(root)
    operations = {
        "doctor": ("doctor", "--project-root", str(root)),
        "status": ("supervisor-status", "--project-root", str(root), "--json"),
        "queue": ("queue-list", "--project-root", str(root), "--json"),
        "history": ("queue-history", "--project-root", str(root), "--json"),
        "audit_history": ("audit-history", "--project-root", str(root), "--json"),
        "run_next": ("queue-run-next", "--project-root", str(root), "--json"),
        "run_all": ("queue-run-loop", "--project-root", str(root), "--json"),
        "audit": ("audit", "--project-root", str(root), "--json"),
        "compatibility": ("compatibility-matrix", "--project-root", str(root), "--json"),
    }
    if operation in {"retry", "recover", "requeue_escalated", "fail_escalated"}:
        normalized_task_id = (task_id or "").strip()
        if not normalized_task_id:
            raise ValueError("Select a task first.")
        if operation == "retry":
            operations[operation] = (
                "queue-retry", "--project-root", str(root), "--task-id", normalized_task_id, "--json",
            )
        elif operation == "recover":
            normalized_reason = (reason or "").strip()
            if not normalized_reason:
                raise ValueError("Recovery reason is required.")
            operations[operation] = (
                "queue-recover-running", "--project-root", str(root), "--task-id", normalized_task_id,
                "--reason", normalized_reason, "--json",
            )
        else:
            normalized_reason = (reason or "").strip()
            if not normalized_reason:
                raise ValueError("Escalation resolution reason is required.")
            operations[operation] = (
                "queue-resolve-escalation", "--project-root", str(root), "--task-id", normalized_task_id,
                "--resolution", "requeue" if operation == "requeue_escalated" else "fail",
                "--reason", normalized_reason, "--json",
            )
    if operation.startswith("check_role_"):
        role = operation.removeprefix("check_role_")
        if role not in {"supervisor", "worker", "critic"}:
            raise ValueError(f"Unsupported role: {role}")
        operations[operation] = (
            "role-conformance",
            "--project-root",
            str(root),
            "--role",
            role,
            "--json",
        )
    if operation not in operations:
        raise ValueError(f"Unsupported GUI operation: {operation}")
    command = (sys.executable, "-m", "llm_harness", *operations[operation])
    completed = subprocess.run(
        command,
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )
    return GuiCommandResult(command, completed.returncode, completed.stdout, completed.stderr)


def _validate_saved_role_profile(project_root: Path) -> None:
    """Fail before queue mutation when a saved role can no longer be launched."""
    snapshot = load_project_snapshot(project_root)
    profile = snapshot.profile
    if profile is None:
        raise ValueError("Save the Supervisor/Worker role profile before running queued tasks.")
    _validate_role_selection(snapshot.catalog, "supervisor", profile.supervisor)
    _validate_role_selection(snapshot.catalog, "worker", profile.worker)
    if profile.critic is not None:
        _validate_role_selection(snapshot.catalog, "critic", profile.critic)


def _validated_project_root(project_root: Path) -> Path:
    root = project_root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Project directory does not exist: {root}")
    if not (root / ".git").exists():
        raise ValueError(f"Project directory is not a Git repository: {root}")
    return root


def _optional(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def _validate_role_selection(catalog: dict[str, Any], role: str, selection: RoleSelection) -> None:
    agent = next(
        (item for item in catalog["agents"] if item["name"].casefold() == selection.agent.casefold()),
        None,
    )
    if agent is None:
        raise ValueError(f"Unknown {role} agent '{selection.agent}'. Refresh the ACP Registry or configure it first.")
    declared_driver = agent.get(f"{role}_driver")
    if not declared_driver:
        raise ValueError(f"Agent '{selection.agent}' cannot serve as {role}: the role driver is not declared.")
    if not agent.get("available"):
        raise ValueError(
            f"Agent '{selection.agent}' is unavailable for {role}: {agent.get('detail') or 'launcher not found'}. "
            "Install its launcher or choose an available agent."
        )
    if selection.driver and selection.driver != declared_driver:
        raise ValueError(
            f"Agent '{selection.agent}' declares {role} driver '{declared_driver}', not '{selection.driver}'."
        )


def _updated_endpoint(
    current: CloudModelEndpoint,
    provider: str,
    model: str,
    base_url: str,
    api_key_env: str,
) -> CloudModelEndpoint:
    return CloudModelEndpoint(
        provider=provider.strip(),
        model=model.strip(),
        base_url=_optional(base_url),
        api_key_env=_optional(api_key_env),
        timeout_seconds=current.timeout_seconds,
        max_retries=current.max_retries,
        max_output_tokens=current.max_output_tokens,
    )
