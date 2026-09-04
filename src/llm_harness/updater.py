from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import tempfile
from typing import Any, Mapping
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile

from . import __version__
from .durable_io import atomic_write_json, atomic_write_text, read_json
from .signed_catalog import SignedCatalogError, fetch_signed_catalog


MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseArtifact:
    version: str
    platform: str
    url: str
    sha256: str
    bytes: int
    notes_url: str = ""


def installation_root(environment: Mapping[str, str] | None = None) -> Path:
    env = environment if environment is not None else os.environ
    override = env.get("HOH_INSTALL_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and env.get("LOCALAPPDATA"):
        return Path(env["LOCALAPPDATA"]) / "Programs" / "HoH"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "HoH"
    return Path.home() / ".local" / "share" / "hoh"


def update_platform_key() -> str:
    system = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}.get(platform.system(), platform.system().casefold())
    machine = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(
        platform.machine().casefold(), platform.machine().casefold()
    )
    return f"{system}-{machine}"


def check_for_update(
    source: str,
    current_version: str,
    *,
    trust_path: Path | None = None,
    platform_key: str | None = None,
) -> tuple[ReleaseArtifact | None, dict[str, Any]]:
    try:
        catalog = fetch_signed_catalog(source, trust_path=trust_path, expected_kind="releases")
    except SignedCatalogError as exc:
        raise UpdateError(str(exc)) from exc
    artifacts = catalog.get("artifacts")
    if not isinstance(artifacts, list):
        raise UpdateError("Release catalog has no artifacts array.")
    target = platform_key or update_platform_key()
    candidates = [_artifact_from_payload(item) for item in artifacts if isinstance(item, dict) and item.get("platform") == target]
    newer = [item for item in candidates if _version_tuple(item.version) > _version_tuple(current_version)]
    return (max(newer, key=lambda item: _version_tuple(item.version), default=None), catalog)


def download_release(artifact: ReleaseArtifact, destination: Path, *, timeout_seconds: float = 120.0) -> Path:
    if artifact.bytes <= 0 or artifact.bytes > MAX_PACKAGE_BYTES:
        raise UpdateError("Release package size is outside the allowed range.")
    parsed = urlparse(artifact.url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise UpdateError("Release downloads require HTTPS; HTTP is allowed only for loopback testing.")
    request = Request(artifact.url, headers={"Accept": "application/zip", "User-Agent": f"HoH/{__version__}"})
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    digest = hashlib.sha256()
    size = 0
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            try:
                with urlopen(request, timeout=timeout_seconds) as response:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > artifact.bytes or size > MAX_PACKAGE_BYTES:
                            raise UpdateError("Release download exceeds its signed size.")
                        digest.update(chunk)
                        handle.write(chunk)
            except OSError as exc:
                raise UpdateError(f"Release download failed: {exc}") from exc
            handle.flush()
            os.fsync(handle.fileno())
        if size != artifact.bytes or digest.hexdigest() != artifact.sha256:
            raise UpdateError("Release package does not match its signed size and SHA-256.")
        os.replace(temporary, destination)
        temporary = None
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def install_release(package_path: Path, artifact: ReleaseArtifact, *, root: Path | None = None) -> Path:
    target_root = (root or installation_root()).resolve()
    versions = target_root / "versions"
    version_dir = versions / artifact.version
    versions.mkdir(parents=True, exist_ok=True)
    _verify_local_package(package_path, artifact)
    if version_dir.exists():
        receipt = read_json(version_dir / ".hoh-release.json", default={})
        if not isinstance(receipt, dict) or receipt.get("sha256") != artifact.sha256 or receipt.get("bytes") != artifact.bytes:
            raise UpdateError(f"Installed release {artifact.version} has no matching integrity receipt.")
        _activate_release(target_root, artifact.version)
        return version_dir
    staging: Path | None = Path(tempfile.mkdtemp(prefix=f".{artifact.version}-", dir=versions))
    try:
        with zipfile.ZipFile(package_path) as archive:
            _safe_extract(archive, staging)
        if not any(staging.iterdir()):
            raise UpdateError("Release package is empty.")
        atomic_write_json(
            staging / ".hoh-release.json",
            {"schema": "hoh.installed-release", "version": artifact.version, "bytes": artifact.bytes, "sha256": artifact.sha256},
        )
        os.replace(staging, version_dir)
        staging = None
        _activate_release(target_root, artifact.version)
        return version_dir
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def current_release(root: Path | None = None) -> str | None:
    target_root = (root or installation_root()).resolve()
    payload = read_json(target_root / "current.json", default={})
    if not isinstance(payload, dict) or payload.get("schema") not in {None, "hoh.current-release"}:
        raise UpdateError("Installed release pointer is corrupted.")
    current = payload.get("current")
    return str(current) if current else None


def rollback_release(root: Path | None = None) -> str:
    target_root = (root or installation_root()).resolve()
    payload = read_json(target_root / "current.json", default={})
    if not isinstance(payload, dict):
        raise UpdateError("Installed release pointer is corrupted.")
    current = str(payload.get("current") or "")
    previous = str(payload.get("previous") or "")
    if not previous or not (target_root / "versions" / previous).is_dir():
        raise UpdateError("No installed previous release is available for rollback.")
    atomic_write_json(
        target_root / "current.json",
        {"schema": "hoh.current-release", "version": "1.0", "current": previous, "previous": current},
    )
    return previous


def _artifact_from_payload(value: Mapping[str, Any]) -> ReleaseArtifact:
    try:
        artifact = ReleaseArtifact(
            version=str(value["version"]), platform=str(value["platform"]), url=str(value["url"]),
            sha256=str(value["sha256"]).casefold(), bytes=int(value["bytes"]), notes_url=str(value.get("notes_url") or ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UpdateError("Release catalog contains an invalid artifact.") from exc
    if len(artifact.sha256) != 64 or any(character not in "0123456789abcdef" for character in artifact.sha256):
        raise UpdateError("Release artifact SHA-256 is invalid.")
    _version_tuple(artifact.version)
    return artifact


def _version_tuple(value: str) -> tuple[int, int, int, tuple[str, ...]]:
    main, separator, suffix = value.strip().partition("-")
    parts = main.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise UpdateError(f"Release version is not semantic major.minor.patch: {value}")
    # Stable releases sort after prereleases with the same numeric version.
    suffix_key = ("~",) if not separator else tuple(suffix.split("."))
    return int(parts[0]), int(parts[1]), int(parts[2]), suffix_key


def _verify_local_package(path: Path, artifact: ReleaseArtifact) -> None:
    try:
        stat = path.stat()
    except OSError as exc:
        raise UpdateError(f"Cannot read release package: {exc}") from exc
    if stat.st_size != artifact.bytes:
        raise UpdateError("Local release size does not match the signed catalog.")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != artifact.sha256:
        raise UpdateError("Local release SHA-256 does not match the signed catalog.")


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.infolist():
        member_path = (root / member.filename).resolve()
        try:
            member_path.relative_to(root)
        except ValueError as exc:
            raise UpdateError(f"Release archive contains an unsafe path: {member.filename}") from exc
        unix_mode = member.external_attr >> 16
        if unix_mode & 0o170000 == 0o120000:
            raise UpdateError(f"Release archive contains an unsupported symbolic link: {member.filename}")
    archive.extractall(root)
    _restore_executable_bits(archive, root)


def _restore_executable_bits(archive: zipfile.ZipFile, root: Path) -> None:
    """Zip extraction drops the execute bit, and PowerShell archives never carry one.

    Without this, the first self-update leaves scripts/*.sh non-executable and the
    generated launcher cannot start HoH at all on Linux and macOS.
    """
    if os.name == "nt":
        return
    for member in archive.infolist():
        if member.is_dir():
            continue
        extracted = root / member.filename
        if not extracted.is_file():
            continue
        archived_mode = (member.external_attr >> 16) & 0o777
        if archived_mode & 0o111:
            extracted.chmod(archived_mode | 0o644)
        elif _needs_execute_bit(extracted):
            extracted.chmod(0o755)


def _needs_execute_bit(path: Path) -> bool:
    if path.suffix == ".sh":
        return True
    try:
        with path.open("rb") as handle:
            return handle.read(2) == b"#!"
    except OSError:
        return False


def _write_launchers(root: Path) -> None:
    if os.name == "nt":
        atomic_write_text(
            root / "launch-hoh.ps1",
            "param([Parameter(Position=0,ValueFromRemainingArguments=$true)][string[]]$HarnessArgs)\n"
            "$ErrorActionPreference='Stop'\n"
            "$pointer=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'current.json') -Raw | ConvertFrom-Json\n"
            "$launcher=Join-Path $PSScriptRoot ('versions\\' + $pointer.current + '\\scripts\\hoh.ps1')\n"
            "& $launcher @HarnessArgs\nexit $LASTEXITCODE\n",
        )
        atomic_write_text(
            root / "launch-hoh-gui.ps1",
            "$ErrorActionPreference='Stop'\n"
            "$pointer=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'current.json') -Raw | ConvertFrom-Json\n"
            "$launcher=Join-Path $PSScriptRoot ('versions\\' + $pointer.current + '\\scripts\\hoh-gui.ps1')\n"
            "& $launcher\n",
        )
        return
    _write_posix_launchers(root)


def _write_posix_launchers(root: Path) -> None:
    launcher = root / "hoh"
    atomic_write_text(
        launcher,
        """#!/usr/bin/env sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["current"])' "$root/current.json")
exec "$root/versions/$version/scripts/hoh.sh" "$@"
""",
    )
    launcher.chmod(0o755)
    gui_launcher = root / "hoh-gui"
    atomic_write_text(
        gui_launcher,
        """#!/usr/bin/env sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["current"])' "$root/current.json")
exec "$root/versions/$version/scripts/hoh-gui.sh" "$@"
""",
    )
    gui_launcher.chmod(0o755)


def _activate_release(root: Path, version: str) -> None:
    previous = current_release(root)
    atomic_write_json(
        root / "current.json",
        {"schema": "hoh.current-release", "version": "1.0", "current": version, "previous": previous or ""},
    )
    _write_launchers(root)
