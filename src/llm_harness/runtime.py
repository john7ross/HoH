from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class RuntimeConfig:
    require_embedded_python: bool = field(default_factory=lambda: os.name == "nt")
    embedded_python_path: str = "runtime/python/python.exe"
    wheels_path: str = "vendor/wheels"


@dataclass(frozen=True)
class RuntimeStatus:
    ok: bool
    python_path: Path
    wheels_path: Path
    python_version: str | None
    wheel_count: int
    findings: tuple[str, ...]


DISTRIBUTION_ROOT_ENV = "HOH_DISTRIBUTION_ROOT"


def distribution_root() -> Path:
    """Where HoH itself is installed, which is not where the user's project is.

    The embedded interpreter and the vendored wheels belong to the installation.
    Resolving them against the project root told every installed Windows user that
    the interpreter they were running on was missing, and sent them to a build
    script that ships for maintainers. The launchers export the variable; the walk
    is for anything started without them, and covers both a source checkout
    (src/llm_harness) and an installed tree (runtime/site-packages/llm_harness).
    """
    configured = os.environ.get(DISTRIBUTION_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "runtime" / "python").exists() or (candidate / "src" / "llm_harness").is_dir():
            return candidate
    return here.parents[2]


def inspect_runtime(
    project_root: Path,
    config: RuntimeConfig,
    root: Path | None = None,
) -> RuntimeStatus:
    """Inspect the installation the caller is running on.

    project_root is kept because callers pass it and because a source checkout is
    its own distribution, but the paths below come from the installation.
    """
    installation = root or distribution_root()
    python_path = installation / config.embedded_python_path
    wheels_path = installation / config.wheels_path
    findings: list[str] = []
    python_version: str | None = None
    wheel_count = 0

    if config.require_embedded_python:
        if not python_path.exists():
            findings.append(f"Embedded Python is required but missing: {python_path}")
        elif not python_path.is_file():
            findings.append(f"Embedded Python path is not a file: {python_path}")
        else:
            try:
                completed = subprocess.run(
                    [str(python_path), "--version"],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
                if completed.returncode != 0:
                    message = completed.stderr.strip() or completed.stdout.strip() or "python --version failed"
                    findings.append(f"Embedded Python failed to start: {message}")
                else:
                    python_version = (completed.stdout.strip() or completed.stderr.strip()).strip()
            except (OSError, subprocess.TimeoutExpired) as exc:
                findings.append(f"Embedded Python failed to start: {exc}")

    if not wheels_path.exists():
        findings.append(f"Vendored wheels directory is missing: {wheels_path}")
    elif not wheels_path.is_dir():
        findings.append(f"Vendored wheels path is not a directory: {wheels_path}")
    else:
        wheel_count = len(tuple(wheels_path.glob("*.whl")))
        if wheel_count == 0:
            findings.append(f"Vendored wheels directory contains no .whl files: {wheels_path}")

    return RuntimeStatus(
        ok=not findings,
        python_path=python_path,
        wheels_path=wheels_path,
        python_version=python_version,
        wheel_count=wheel_count,
        findings=tuple(findings),
    )
