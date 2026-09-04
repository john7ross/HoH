from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from typing import Iterable
import zipfile

from .durable_io import atomic_write_json


PACKAGE_ITEMS = (
    "ARCHITECTURE.md",
    "ARCHITECTURE.ru.md",
    "catalogs",
    "config.example.toml",
    "docs",
    "examples",
    "LICENSE",
    "NOTICE",
    "pyproject.toml",
    "README.md",
    "README.ru.md",
    "RELEASE_NOTES.md",
    "RELEASE_NOTES.ru.md",
    "requirements.lock",
    "runtime",
    "schemas",
    "scripts",
    "SECURITY.md",
    "SECURITY.ru.md",
    "src",
    "vendor",
)


class PortablePackageError(RuntimeError):
    pass


@dataclass(frozen=True)
class PortablePackageResult:
    package: Path
    manifest: Path
    sha256: str
    bytes: int


def build_posix_portable_package(
    project_root: Path,
    output_dir: Path,
    *,
    product_version: str,
    source_commit: str,
    source_clean: bool,
) -> PortablePackageResult:
    root = project_root.resolve()
    output = output_dir.resolve()
    system = platform.system().casefold()
    if system not in {"linux", "darwin"}:
        raise PortablePackageError("POSIX portable packages can be built only on Linux or macOS.")
    if not source_clean:
        raise PortablePackageError("Refusing to create a release package from a dirty Git checkout.")
    platform_key = ("macos" if system == "darwin" else "linux") + "-" + _machine()
    output.mkdir(parents=True, exist_ok=True)
    package = output / f"HoH-{product_version}-{platform_key}.zip"
    manifest = output / f"HoH-{product_version}-{platform_key}.manifest.json"
    if package.exists():
        package.unlink()
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in _package_paths(root):
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo.from_file(path, relative)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    size = package.stat().st_size
    atomic_write_json(
        manifest,
        {
            "schema": "hoh.release-manifest",
            "manifest_version": "1.0",
            "product": {"name": "HoH", "version": product_version},
            "source": {"commit": source_commit, "git_clean": True},
            "target": {
                "operating_system": platform.system(),
                "architecture": platform.machine(),
                "platform": platform_key,
                "runtime": _runtime_description(root),
            },
            "gates": {"tests": "passed", "compile": "passed", "doctor": "passed"},
            "package": {"name": package.name, "bytes": size, "sha256": digest},
        },
    )
    return PortablePackageResult(package, manifest, digest, size)


def run_posix_package_gates(project_root: Path) -> None:
    root = project_root.resolve()
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "src") + (
        os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else ""
    )
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".toml") as config:
        config.write(
            "[runtime]\nrequire_embedded_python = false\nwheels_path = \"vendor/wheels\"\n"
            "[worker]\ndriver = \"stub\"\ncommand = \"stub\"\nargs = []\n"
        )
        config.flush()
        commands = (
            (sys.executable, "-m", "unittest", "discover", "-s", "tests"),
            (sys.executable, "-m", "compileall", "-q", "src", "tests"),
            (
                sys.executable, "-m", "llm_harness", "doctor", "--project-root", str(root),
                "--config", config.name,
            ),
        )
        for command in commands:
            completed = subprocess.run(command, cwd=root, env=environment, check=False)
            if completed.returncode != 0:
                raise PortablePackageError(f"Package gate failed ({completed.returncode}): {' '.join(command)}")


def git_release_state(project_root: Path) -> tuple[str, bool]:
    root = project_root.resolve()
    commit = subprocess.run(
        ("git", "-C", str(root), "rev-parse", "HEAD"),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    status = subprocess.run(
        ("git", "-C", str(root), "status", "--porcelain"),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if commit.returncode != 0 or status.returncode != 0 or not commit.stdout.strip():
        raise PortablePackageError("Cannot determine Git release state.")
    return commit.stdout.strip(), not bool(status.stdout.strip())


def _runtime_description(root: Path) -> str:
    """Say which interpreter the package carries, or that it expects one on the host.

    The Debian package embeds its own, so an update payload built from the same tree
    has to say so -- otherwise the manifest would promise a host Python to a machine
    that was installed precisely because it has none.
    """
    embedded = root / "runtime" / "python" / "bin" / "python3"
    if embedded.exists():
        completed = subprocess.run(
            [str(embedded), "-c", "import sys; print(sys.version.split()[0])"],
            capture_output=True,
            text=True,
            check=False,
        )
        version = completed.stdout.strip()
        if completed.returncode == 0 and version:
            return f"Python {version} (embedded)"
        raise PortablePackageError(f"The embedded interpreter at {embedded} does not run.")
    return f"Python >=3.11 (host), built with {platform.python_version()}"


def _package_paths(root: Path) -> Iterable[Path]:
    for item in PACKAGE_ITEMS:
        path = root / item
        if path.is_file():
            yield path
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and "__pycache__" not in child.parts and child.suffix not in {".pyc", ".pyo"}:
                    yield child


def _machine() -> str:
    return {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(
        platform.machine().casefold(), platform.machine().casefold()
    )
