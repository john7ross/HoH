from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable, Mapping, Protocol

from .acp import AcpError, JsonRpcStdioClient
from .process_launch import process_group_kwargs, resolve_executable, subprocess_creation_flag, terminate_process_tree


class AgentAuthenticationError(RuntimeError):
    pass


class AuthProcess(Protocol):
    stdin: Any
    stdout: Any
    stderr: Any

    def terminate(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


@dataclass(frozen=True)
class AgentAuthMethod:
    method_id: str
    name: str
    description: str
    method_type: str = "agent"
    args: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    variables: tuple[tuple[str, str, bool, bool], ...] = ()
    link: str | None = None


@dataclass(frozen=True)
class AgentAuthInspection:
    methods: tuple[AgentAuthMethod, ...]
    agent_name: str | None
    agent_version: str | None
    logout_supported: bool


@dataclass(frozen=True)
class VendorLoginProfile:
    profile_id: str
    display_name: str
    command: str
    login_args: tuple[str, ...]
    status_args: tuple[str, ...] | None


@dataclass(frozen=True)
class LoginResult:
    succeeded: bool
    detail: str


ProcessFactory = Callable[[tuple[str, ...], Mapping[str, str]], AuthProcess]
InteractiveRunner = Callable[[tuple[str, ...], Mapping[str, str], float], int]


VENDOR_LOGIN_PROFILES: tuple[VendorLoginProfile, ...] = (
    VendorLoginProfile("codex", "OpenAI Codex CLI", "codex", ("login",), ("login", "status")),
    VendorLoginProfile("claude", "Claude Code", "claude", ("auth", "login"), ("auth", "status")),
    VendorLoginProfile("gemini", "Gemini CLI", "gemini", (), None),
    VendorLoginProfile("github-copilot-cli", "GitHub Copilot CLI", "copilot", ("login",), None),
    VendorLoginProfile("anthropic-platform", "Anthropic Platform CLI", "ant", ("auth", "login"), ("auth", "status")),
    VendorLoginProfile(
        "github",
        "GitHub CLI",
        "gh",
        ("auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--web"),
        ("auth", "status", "--hostname", "github.com"),
    ),
    VendorLoginProfile("jules", "Google Jules CLI", "jules", ("login",), None),
)


class AcpAuthService:
    def __init__(
        self,
        *,
        process_factory: ProcessFactory | None = None,
        interactive_runner: InteractiveRunner | None = None,
    ) -> None:
        self._process_factory = process_factory or _start_acp_process
        self._interactive_runner = interactive_runner or _run_interactive

    def inspect(
        self,
        command: str,
        args: tuple[str, ...],
        environment: tuple[tuple[str, str], ...] = (),
    ) -> AgentAuthInspection:
        process = self._process_factory(
            (resolve_executable(command), *args),
            {**os.environ, **dict(environment)},
        )
        try:
            response = JsonRpcStdioClient(process).initialize(terminal_auth=True)
            result = response.get("result")
            if not isinstance(result, dict):
                raise AgentAuthenticationError("ACP initialize returned no result object.")
            methods = _parse_auth_methods(result.get("authMethods"))
            info = result.get("agentInfo")
            capabilities = result.get("agentCapabilities")
            auth_capability = capabilities.get("auth") if isinstance(capabilities, dict) else None
            return AgentAuthInspection(
                methods=methods,
                agent_name=str(info.get("name")) if isinstance(info, dict) and info.get("name") else None,
                agent_version=str(info.get("version")) if isinstance(info, dict) and info.get("version") else None,
                logout_supported=isinstance(auth_capability, dict) and auth_capability.get("logout") is not None,
            )
        except AcpError as exc:
            raise AgentAuthenticationError(str(exc)) from exc
        finally:
            _close_process(process)

    def login(
        self,
        command: str,
        args: tuple[str, ...],
        method: AgentAuthMethod,
        environment: tuple[tuple[str, str], ...] = (),
        *,
        timeout_seconds: float = 900.0,
    ) -> LoginResult:
        merged = {**os.environ, **dict(environment)}
        if method.method_type == "terminal":
            merged.update(dict(method.environment))
            code = self._interactive_runner(
                (resolve_executable(command), *args, *method.args), merged, timeout_seconds
            )
            return LoginResult(
                code == 0,
                "Interactive vendor login completed. Credentials remain in the vendor store."
                if code == 0
                else f"Interactive vendor login exited with code {code}.",
            )
        if method.method_type not in {"agent", "env_var"}:
            raise AgentAuthenticationError(
                f"ACP authentication method type '{method.method_type}' is not supported."
            )
        missing = [name for name, _label, _secret, optional in method.variables if not optional and not merged.get(name)]
        if missing:
            raise AgentAuthenticationError(
                "Missing authentication environment variables: " + ", ".join(sorted(missing))
            )
        process = self._process_factory((resolve_executable(command), *args), merged)
        try:
            client = JsonRpcStdioClient(process)
            response = client.initialize(terminal_auth=True)
            result = response.get("result")
            advertised = _parse_auth_methods(result.get("authMethods") if isinstance(result, dict) else None)
            selected = next((item for item in advertised if item.method_id == method.method_id), None)
            if selected is None or selected.method_type == "terminal":
                raise AgentAuthenticationError("The selected ACP authentication method is no longer advertised.")
            client.authenticate(method.method_id)
            return LoginResult(True, "ACP authentication completed; HoH did not read or store the vendor token.")
        except AcpError as exc:
            raise AgentAuthenticationError(str(exc)) from exc
        finally:
            _close_process(process)


def vendor_login_profile(profile_id: str) -> VendorLoginProfile | None:
    folded = profile_id.strip().casefold()
    aliases = {"codex-acp": "codex", "claude-acp": "claude", "gemini-cli": "gemini"}
    folded = aliases.get(folded, folded)
    return next((item for item in VENDOR_LOGIN_PROFILES if item.profile_id == folded), None)


def vendor_login_status(profile: VendorLoginProfile, *, timeout_seconds: float = 30.0) -> LoginResult:
    executable = shutil.which(profile.command)
    if executable is None:
        return LoginResult(False, f"{profile.display_name} is not installed or is not on PATH.")
    if profile.status_args is None:
        return LoginResult(False, f"{profile.display_name} has no non-interactive login status command.")
    try:
        completed = subprocess.run(
            (executable, *profile.status_args),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            encoding="utf-8",
            **process_group_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return LoginResult(False, f"Could not check {profile.display_name} login: {exc}")
    return LoginResult(
        completed.returncode == 0,
        f"{profile.display_name} session is active."
        if completed.returncode == 0
        else f"{profile.display_name} is not authenticated.",
    )


def vendor_login(
    profile: VendorLoginProfile,
    *,
    timeout_seconds: float = 900.0,
    interactive_runner: InteractiveRunner | None = None,
) -> LoginResult:
    executable = shutil.which(profile.command)
    if executable is None:
        return LoginResult(False, f"{profile.display_name} is not installed or is not on PATH.")
    current = vendor_login_status(profile) if profile.status_args is not None else None
    if current is not None and current.succeeded:
        return LoginResult(True, f"{profile.display_name} is already authenticated; existing credentials were preserved.")
    runner = interactive_runner or _run_interactive
    try:
        code = runner((executable, *profile.login_args), os.environ, timeout_seconds)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return LoginResult(False, f"Could not start {profile.display_name} login: {exc}")
    if code != 0:
        return LoginResult(False, f"{profile.display_name} login exited with code {code}.")
    if profile.status_args is None:
        return LoginResult(
            True,
            f"{profile.display_name} login flow exited successfully; the vendor exposes no status probe.",
        )
    verified = vendor_login_status(profile)
    return verified if verified.succeeded else LoginResult(
        False,
        f"{profile.display_name} login exited successfully, but its status command did not verify a session.",
    )


def _parse_auth_methods(value: object) -> tuple[AgentAuthMethod, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise AgentAuthenticationError("ACP authMethods must be an array.")
    methods: list[AgentAuthMethod] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise AgentAuthenticationError("ACP authentication method must be an object.")
        method_id = raw.get("id")
        name = raw.get("name")
        if not isinstance(method_id, str) or not method_id.strip() or not isinstance(name, str) or not name.strip():
            raise AgentAuthenticationError("ACP authentication method requires non-empty id and name.")
        if method_id in seen:
            raise AgentAuthenticationError(f"ACP authentication method id is duplicated: {method_id}")
        seen.add(method_id)
        method_type = str(raw.get("type", "agent")).strip().casefold()
        args = _strings(raw.get("args"), "auth method args")
        environment = _string_mapping(raw.get("env"), "auth method env")
        variables: list[tuple[str, str, bool, bool]] = []
        raw_variables = raw.get("vars", [])
        if not isinstance(raw_variables, list):
            raise AgentAuthenticationError("ACP env_var authentication vars must be an array.")
        for variable in raw_variables:
            if not isinstance(variable, dict) or not isinstance(variable.get("name"), str) or not variable["name"].strip():
                raise AgentAuthenticationError("ACP authentication variable requires a name.")
            variables.append(
                (
                    variable["name"],
                    str(variable.get("label") or variable["name"]),
                    bool(variable.get("secret", True)),
                    bool(variable.get("optional", False)),
                )
            )
        link = raw.get("link")
        if link is not None and (not isinstance(link, str) or not link.casefold().startswith("https://")):
            raise AgentAuthenticationError("ACP authentication links must use HTTPS.")
        methods.append(
            AgentAuthMethod(
                method_id,
                name,
                str(raw.get("description") or ""),
                method_type,
                args,
                environment,
                tuple(variables),
                link,
            )
        )
    return tuple(methods)


def _strings(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or "\x00" in item for item in value):
        raise AgentAuthenticationError(f"ACP {label} must contain only strings.")
    return tuple(value)


def _string_mapping(value: object, label: str) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) or "\x00" in key or "\x00" in item
        for key, item in value.items()
    ):
        raise AgentAuthenticationError(f"ACP {label} must contain only strings.")
    return tuple(sorted(value.items()))


def _start_acp_process(args: tuple[str, ...], environment: Mapping[str, str]) -> AuthProcess:
    try:
        return subprocess.Popen(
            args,
            env=dict(environment),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            **process_group_kwargs(),
        )
    except OSError as exc:
        raise AgentAuthenticationError(f"Could not start ACP agent for authentication: {exc}") from exc


def _run_interactive(args: tuple[str, ...], environment: Mapping[str, str], timeout_seconds: float) -> int:
    launch: dict[str, object] = (
        {"creationflags": subprocess_creation_flag("CREATE_NEW_CONSOLE")}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    process = subprocess.Popen(args, env=dict(environment), **launch)
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        terminate_process_tree(process)
        raise


def _close_process(process: AuthProcess) -> None:
    try:
        if hasattr(process, "poll"):
            terminate_process_tree(process)  # type: ignore[arg-type]
        else:
            process.terminate()
            process.wait(timeout=5.0)
    except (OSError, subprocess.TimeoutExpired, AttributeError):
        return
