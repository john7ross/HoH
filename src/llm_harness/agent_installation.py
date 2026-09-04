from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
from typing import Any, Callable, Mapping
from urllib.request import Request, urlopen
import zipfile

from . import __version__
from .acp_registry import AcpLaunch, AcpRegistryAgent, current_platform_key
from .durable_io import atomic_write_json, read_json


MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 50_000
INSTALL_RECEIPT_VERSION = "1.0"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class AgentInstallationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ManagedAgentReceipt:
    receipt_version: str
    agent_id: str
    agent_version: str
    distribution: str
    source: str
    command: str
    args: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    installed_at_utc: str
    integrity_sha256: str | None = None


@dataclass(frozen=True)
class ManagedAgentStatus:
    agent_id: str
    version: str
    installed: bool
    installable: bool
    distribution: str
    detail: str


ProcessRunner = Callable[[tuple[str, ...], Mapping[str, str], float], subprocess.CompletedProcess[str]]
Downloader = Callable[[str, Path, int, float], str]


def managed_agents_root(environment: Mapping[str, str] | None = None) -> Path:
    env = environment if environment is not None else os.environ
    override = env.get("HOH_AGENT_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and env.get("LOCALAPPDATA"):
        return Path(env["LOCALAPPDATA"]) / "HoH" / "agents"
    xdg_data = env.get("XDG_DATA_HOME")
    root = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
    return root / "hoh" / "agents"


class ManagedAgentInstaller:
    def __init__(
        self,
        root: Path | None = None,
        *,
        process_runner: ProcessRunner | None = None,
        downloader: Downloader | None = None,
    ) -> None:
        self.root = (root or managed_agents_root()).resolve()
        self._run = process_runner or _run_process
        self._download = downloader or _download

    def status(self, agent: AcpRegistryAgent) -> ManagedAgentStatus:
        try:
            launch = self.resolve(agent)
        except AgentInstallationError as exc:
            return ManagedAgentStatus(
                agent.id,
                agent.version,
                False,
                self._installable(agent),
                _distribution_kind(agent),
                str(exc),
            )
        if launch is not None:
            return ManagedAgentStatus(
                agent.id,
                agent.version,
                True,
                True,
                launch.distribution,
                f"managed {agent.version} at {launch.command}",
            )
        detail = _installability_detail(agent)
        return ManagedAgentStatus(
            agent.id,
            agent.version,
            False,
            self._installable(agent),
            _distribution_kind(agent),
            detail,
        )

    def install(self, agent: AcpRegistryAgent, *, timeout_seconds: float = 900.0) -> AcpLaunch:
        _validate_identity(agent)
        existing = self.resolve(agent)
        if existing is not None:
            return existing
        distribution = _distribution_kind(agent)
        if distribution == "unsupported":
            raise AgentInstallationError(_installability_detail(agent))

        target = self._target(agent)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{agent.id}-", dir=target.parent))
        try:
            if distribution == "npx":
                receipt = self._install_npm(agent, staging, timeout_seconds)
            elif distribution == "uvx":
                receipt = self._install_uv(agent, staging, timeout_seconds)
            else:
                receipt = self._install_binary(agent, staging, timeout_seconds)
            atomic_write_json(staging / "receipt.json", _receipt_payload(receipt))
            if target.exists():
                raise AgentInstallationError(
                    f"Managed target already exists without a valid receipt: {target}"
                )
            staging.replace(target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        launch = self.resolve(agent)
        if launch is None:
            raise AgentInstallationError("Managed installation completed but its receipt is unusable.")
        return launch

    def uninstall(self, agent: AcpRegistryAgent) -> bool:
        _validate_identity(agent)
        target = self._target(agent)
        if not target.exists():
            return False
        _ensure_within(target, self.root)
        receipt = self._read_receipt(target / "receipt.json")
        if receipt.agent_id != agent.id or receipt.agent_version != agent.version:
            raise AgentInstallationError("Managed receipt identity does not match the uninstall target.")
        shutil.rmtree(target)
        parent = target.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
        return True

    def resolve(self, agent: AcpRegistryAgent) -> AcpLaunch | None:
        _validate_identity(agent)
        target = self._target(agent)
        receipt_path = target / "receipt.json"
        if not receipt_path.exists():
            return None
        receipt = self._read_receipt(receipt_path)
        if receipt.agent_id != agent.id or receipt.agent_version != agent.version:
            raise AgentInstallationError("Managed receipt identity or version does not match the registry.")
        command = Path(receipt.command)
        if not command.is_absolute():
            command = target / command
        command = command.resolve()
        if receipt.distribution == "npx" and not _is_within(command, target):
            current_node = shutil.which("node")
            if current_node is None or Path(current_node).resolve() != command:
                raise AgentInstallationError("Managed npm agent requires the same trusted Node.js executable.")
        else:
            _ensure_within(command, target)
        if not command.is_file():
            raise AgentInstallationError(f"Managed executable is missing: {command}")
        resolved_args: list[str] = []
        for value in receipt.args:
            if not value.startswith("@root/"):
                resolved_args.append(value)
                continue
            resolved = (target / value.removeprefix("@root/")).resolve()
            _ensure_within(resolved, target)
            resolved_args.append(str(resolved))
        args = tuple(resolved_args)
        return AcpLaunch(
            command=str(command),
            args=args,
            environment=receipt.environment,
            distribution=f"managed-{receipt.distribution}",
            available=True,
            detail=f"managed {receipt.agent_version} at {command}",
        )

    def _install_npm(
        self, agent: AcpRegistryAgent, staging: Path, timeout_seconds: float
    ) -> ManagedAgentReceipt:
        spec = agent.distribution["npx"]
        package = _required_string(spec, "package", "npx")
        package_name = _pinned_package_name(package, agent.version, "npm")
        npm = shutil.which("npm")
        node = shutil.which("node")
        if npm is None or node is None:
            raise AgentInstallationError("Node.js with npm is required for this ACP package.")
        content = staging / "content"
        content.mkdir()
        completed = self._run(
            (
                npm,
                "install",
                "--prefix",
                str(content),
                "--no-audit",
                "--no-fund",
                "--save-exact",
                package,
            ),
            os.environ,
            timeout_seconds,
        )
        _require_success(completed, "npm install")
        manifest = content / "node_modules" / Path(*package_name.split("/")) / "package.json"
        try:
            package_payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AgentInstallationError(f"Installed npm package has no readable manifest: {manifest}") from exc
        bin_path = _npm_bin_path(package_name, package_payload)
        entry = (manifest.parent / bin_path).resolve()
        _ensure_within(entry, content)
        if not entry.is_file():
            raise AgentInstallationError(f"Installed npm executable is missing: {entry}")
        lock = content / "package-lock.json"
        integrity = _sha256_file(lock) if lock.is_file() else None
        relative_entry = entry.relative_to(staging).as_posix()
        return _receipt(
            agent,
            "npx",
            package,
            Path(node).resolve().relative_to(staging).as_posix()
            if _is_within(Path(node).resolve(), staging)
            else str(Path(node).resolve()),
            (f"@root/{relative_entry}", *_string_tuple(spec.get("args"))),
            _environment(spec.get("env")),
            integrity,
        )

    def _install_uv(
        self, agent: AcpRegistryAgent, staging: Path, timeout_seconds: float
    ) -> ManagedAgentReceipt:
        spec = agent.distribution["uvx"]
        package = _required_string(spec, "package", "uvx")
        _pinned_package_name(package, agent.version, "uv")
        uv = shutil.which("uv")
        if uv is None:
            raise AgentInstallationError("uv is required for this ACP package.")
        tool_dir = staging / "content" / "tools"
        bin_dir = staging / "content" / "bin"
        environment = {**os.environ, "UV_TOOL_DIR": str(tool_dir), "UV_TOOL_BIN_DIR": str(bin_dir)}
        completed = self._run((uv, "tool", "install", "--force", package), environment, timeout_seconds)
        _require_success(completed, "uv tool install")
        executable = _select_installed_executable(bin_dir, agent.id, package)
        return _receipt(
            agent,
            "uvx",
            package,
            executable.relative_to(staging).as_posix(),
            _string_tuple(spec.get("args")),
            _environment(spec.get("env")),
            None,
        )

    def _install_binary(
        self, agent: AcpRegistryAgent, staging: Path, timeout_seconds: float
    ) -> ManagedAgentReceipt:
        del timeout_seconds
        binary = agent.distribution.get("binary")
        spec = binary.get(current_platform_key()) if isinstance(binary, dict) else None
        if not isinstance(spec, dict):
            raise AgentInstallationError(f"No binary is published for {current_platform_key()}.")
        archive_url = _required_string(spec, "archive", "binary")
        expected_value = spec.get("sha256")
        if not isinstance(expected_value, str) or not _SHA256.fullmatch(expected_value):
            raise AgentInstallationError(
                "The registry binary has no valid SHA-256 and cannot be installed automatically."
            )
        expected = expected_value
        if not archive_url.casefold().startswith("https://"):
            raise AgentInstallationError("Managed binary downloads require HTTPS.")
        archive = staging / "download"
        actual = self._download(archive_url, archive, MAX_ARCHIVE_BYTES, 120.0)
        if actual.casefold() != expected.casefold():
            raise AgentInstallationError(
                f"Binary SHA-256 mismatch: expected {expected.casefold()}, got {actual.casefold()}."
            )
        content = staging / "content"
        content.mkdir()
        _extract_archive(archive, archive_url, content)
        command_value = _required_string(spec, "cmd", "binary")
        executable = _resolve_binary_command(content, command_value)
        if os.name != "nt":
            executable.chmod(executable.stat().st_mode | 0o111)
        return _receipt(
            agent,
            "binary",
            archive_url,
            executable.relative_to(staging).as_posix(),
            _string_tuple(spec.get("args")),
            _environment(spec.get("env")),
            actual.casefold(),
        )

    def _target(self, agent: AcpRegistryAgent) -> Path:
        target = (self.root / agent.id / agent.version).resolve()
        _ensure_within(target, self.root)
        return target

    def _read_receipt(self, path: Path) -> ManagedAgentReceipt:
        payload = read_json(path)
        if not isinstance(payload, dict) or payload.get("receipt_version") != INSTALL_RECEIPT_VERSION:
            raise AgentInstallationError(f"Invalid managed agent receipt: {path}")
        try:
            return ManagedAgentReceipt(
                receipt_version=str(payload["receipt_version"]),
                agent_id=str(payload["agent_id"]),
                agent_version=str(payload["agent_version"]),
                distribution=str(payload["distribution"]),
                source=str(payload["source"]),
                command=str(payload["command"]),
                args=tuple(str(item) for item in payload.get("args", ())),
                environment=tuple(
                    sorted((str(key), str(value)) for key, value in payload.get("environment", {}).items())
                ),
                installed_at_utc=str(payload["installed_at_utc"]),
                integrity_sha256=(
                    str(payload["integrity_sha256"])
                    if payload.get("integrity_sha256") is not None
                    else None
                ),
            )
        except (KeyError, AttributeError, TypeError) as exc:
            raise AgentInstallationError(f"Invalid managed agent receipt: {path}") from exc

    @staticmethod
    def _installable(agent: AcpRegistryAgent) -> bool:
        return _installability_detail(agent).startswith("Ready")


def resolve_managed_launch(agent: AcpRegistryAgent, root: Path | None = None) -> AcpLaunch | None:
    return ManagedAgentInstaller(root).resolve(agent)


def _distribution_kind(agent: AcpRegistryAgent) -> str:
    for kind in ("npx", "uvx"):
        if isinstance(agent.distribution.get(kind), dict):
            return kind
    binary = agent.distribution.get("binary")
    if isinstance(binary, dict) and isinstance(binary.get(current_platform_key()), dict):
        return "binary"
    return "unsupported"


def _installability_detail(agent: AcpRegistryAgent) -> str:
    kind = _distribution_kind(agent)
    if kind == "npx":
        return "Ready for a pinned user-scope npm installation." if shutil.which("npm") and shutil.which("node") else "Node.js with npm is not installed."
    if kind == "uvx":
        return "Ready for a pinned user-scope uv tool installation." if shutil.which("uv") else "uv is not installed."
    if kind == "binary":
        spec = agent.distribution["binary"][current_platform_key()]
        return "Ready for a verified user-scope binary installation." if isinstance(spec.get("sha256"), str) and _SHA256.fullmatch(spec["sha256"]) else "Blocked: the registry archive has no valid SHA-256."
    return f"No supported distribution is published for {current_platform_key()}."


def _validate_identity(agent: AcpRegistryAgent) -> None:
    if not _SAFE_ID.fullmatch(agent.id) or not _SAFE_ID.fullmatch(agent.version):
        raise AgentInstallationError("Registry agent id and version must be filesystem-safe identifiers.")


def _receipt(
    agent: AcpRegistryAgent,
    distribution: str,
    source: str,
    command: str,
    args: tuple[str, ...],
    environment: tuple[tuple[str, str], ...],
    integrity: str | None,
) -> ManagedAgentReceipt:
    return ManagedAgentReceipt(
        INSTALL_RECEIPT_VERSION,
        agent.id,
        agent.version,
        distribution,
        source,
        command,
        args,
        environment,
        datetime.now(UTC).isoformat(),
        integrity,
    )


def _receipt_payload(receipt: ManagedAgentReceipt) -> dict[str, Any]:
    payload = asdict(receipt)
    payload["args"] = list(receipt.args)
    payload["environment"] = dict(receipt.environment)
    return payload


def _run_process(
    args: tuple[str, ...], environment: Mapping[str, str], timeout_seconds: float
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            env=dict(environment),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            encoding="utf-8",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AgentInstallationError(f"Could not run installer command {args[0]}: {exc}") from exc


def _require_success(completed: subprocess.CompletedProcess[str], label: str) -> None:
    if completed.returncode == 0:
        return
    detail = (completed.stderr or completed.stdout or "no output").strip()[:2000]
    raise AgentInstallationError(f"{label} exited with {completed.returncode}: {detail}")


def _download(url: str, destination: Path, max_bytes: int, timeout_seconds: float) -> str:
    request = Request(url, headers={"User-Agent": f"HoH/{__version__}", "Accept": "application/octet-stream"})
    digest = hashlib.sha256()
    total = 0
    try:
        with urlopen(request, timeout=timeout_seconds) as response, destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise AgentInstallationError("Agent archive exceeds the 1 GiB download limit.")
                digest.update(chunk)
                output.write(chunk)
    except OSError as exc:
        raise AgentInstallationError(f"Could not download agent archive: {exc}") from exc
    return digest.hexdigest()


def _extract_archive(archive: Path, source_url: str, destination: Path) -> None:
    folded = source_url.casefold()
    if folded.endswith(".zip"):
        _extract_zip(archive, destination)
        return
    if folded.endswith((".tar.gz", ".tgz")):
        _extract_tar(archive, destination)
        return
    raise AgentInstallationError("Only .zip, .tar.gz, and .tgz agent archives are supported.")


def _extract_zip(archive: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            _validate_member_count(len(members))
            total = 0
            for member in members:
                unix_mode = (member.external_attr >> 16) & 0xFFFF
                if (unix_mode & 0o170000) == 0o120000:
                    raise AgentInstallationError("Agent archive symbolic links are not allowed.")
                target = _safe_archive_target(destination, member.filename)
                total += member.file_size
                _validate_extracted_size(total)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    except (OSError, zipfile.BadZipFile) as exc:
        raise AgentInstallationError(f"Invalid ZIP agent archive: {exc}") from exc


def _extract_tar(archive: Path, destination: Path) -> None:
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            members = bundle.getmembers()
            _validate_member_count(len(members))
            total = 0
            for member in members:
                if member.issym() or member.islnk() or member.isdev():
                    raise AgentInstallationError("Agent archive links and device entries are not allowed.")
                target = _safe_archive_target(destination, member.name)
                total += member.size
                _validate_extracted_size(total)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    continue
                source = bundle.extractfile(member)
                if source is None:
                    raise AgentInstallationError(f"Could not read archive member: {member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                if os.name != "nt" and member.mode & 0o111:
                    target.chmod(target.stat().st_mode | 0o111)
    except (OSError, tarfile.TarError) as exc:
        raise AgentInstallationError(f"Invalid tar.gz agent archive: {exc}") from exc


def _safe_archive_target(root: Path, member_name: str) -> Path:
    normalized = PurePosixPath(member_name.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts or not normalized.parts:
        raise AgentInstallationError(f"Unsafe archive member path: {member_name}")
    target = (root / Path(*normalized.parts)).resolve()
    _ensure_within(target, root)
    return target


def _validate_member_count(count: int) -> None:
    if count > MAX_ARCHIVE_ENTRIES:
        raise AgentInstallationError("Agent archive contains too many entries.")


def _validate_extracted_size(size: int) -> None:
    if size > MAX_EXTRACTED_BYTES:
        raise AgentInstallationError("Expanded agent archive exceeds the 2 GiB safety limit.")


def _resolve_binary_command(content: Path, command_value: str) -> Path:
    relative = PurePosixPath(command_value.replace("\\", "/").removeprefix("./"))
    if relative.is_absolute() or ".." in relative.parts:
        raise AgentInstallationError("Registry binary command escapes the extracted archive.")
    exact = (content / Path(*relative.parts)).resolve()
    _ensure_within(exact, content)
    if exact.is_file():
        return exact
    matches = [item.resolve() for item in content.rglob(relative.name) if item.is_file()]
    if len(matches) != 1:
        raise AgentInstallationError(
            f"Expected one extracted executable named {relative.name}, found {len(matches)}."
        )
    _ensure_within(matches[0], content)
    return matches[0]


def _npm_bin_path(package_name: str, payload: object) -> str:
    if not isinstance(payload, dict):
        raise AgentInstallationError("Installed npm package manifest must be an object.")
    value = payload.get("bin")
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        preferred = package_name.rsplit("/", 1)[-1]
        selected = value.get(preferred)
        if not isinstance(selected, str):
            candidates = sorted(item for item in value.values() if isinstance(item, str) and item)
            selected = candidates[0] if candidates else None
        if isinstance(selected, str):
            return selected
    raise AgentInstallationError("Installed npm package does not declare an executable.")


def _select_installed_executable(bin_dir: Path, agent_id: str, package: str) -> Path:
    if not bin_dir.is_dir():
        raise AgentInstallationError("uv did not create the managed executable directory.")
    candidates = sorted(item.resolve() for item in bin_dir.iterdir() if item.is_file())
    if not candidates:
        raise AgentInstallationError("uv did not install an executable.")
    preferred = {agent_id.casefold(), _package_base_name(package).casefold()}
    matches = [item for item in candidates if item.stem.casefold() in preferred or item.name.casefold() in preferred]
    selected = matches[0] if matches else candidates[0] if len(candidates) == 1 else None
    if selected is None:
        raise AgentInstallationError("uv installed multiple executables and none matches the registry agent.")
    _ensure_within(selected, bin_dir)
    return selected


def _pinned_package_name(spec: str, version: str, ecosystem: str) -> str:
    if ecosystem == "npm":
        match = re.fullmatch(r"(@[^/]+/[^@]+|[^@]+)@(.+)", spec)
    else:
        match = re.fullmatch(r"([^=@\s]+)(?:==|@)(.+)", spec)
    if match is None or match.group(2) != version:
        raise AgentInstallationError(
            f"Registry {ecosystem} package must be pinned exactly to agent version {version}."
        )
    return match.group(1)


def _package_base_name(spec: str) -> str:
    value = spec.split("==", 1)[0]
    if value.startswith("@") and "/" in value:
        return value.rsplit("/", 1)[-1].split("@", 1)[0]
    return value.split("@", 1)[0]


def _required_string(value: Mapping[str, Any], key: str, label: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip() or "\x00" in item:
        raise AgentInstallationError(f"Registry {label} distribution requires a valid {key}.")
    return item


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or "\x00" in item for item in value):
        return ()
    return tuple(value)


def _environment(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        return ()
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise AgentInstallationError("Registry environment must contain only strings.")
    return tuple(sorted(value.items()))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_within(path: Path, root: Path) -> None:
    if not _is_within(path.resolve(), root.resolve()):
        raise AgentInstallationError(f"Managed path escapes its root: {path}")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
