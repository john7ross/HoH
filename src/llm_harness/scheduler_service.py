from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import subprocess
from typing import Callable


@dataclass(frozen=True)
class SchedulerServiceResult:
    action: str
    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.return_code == 0


def manage_scheduler_service(
    action: str,
    *,
    every_minutes: int = 1,
    run_now: bool = False,
    distribution_root: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> SchedulerServiceResult:
    normalized = action.strip().casefold()
    if normalized not in {"install", "uninstall", "status"}:
        raise ValueError(f"Unsupported scheduler service action: {action}")
    if every_minutes < 1:
        raise ValueError("Scheduler service interval must be at least one minute.")
    configured_root = os.environ.get("HOH_DISTRIBUTION_ROOT", "").strip()
    root = (
        distribution_root
        or (Path(configured_root) if configured_root else None)
        or Path(__file__).resolve().parents[2]
    ).resolve()
    system = platform.system()
    if system == "Windows":
        executable = shutil.which("powershell.exe") or shutil.which("powershell")
        script = root / "scripts" / "register-workspace-scheduler-task.ps1"
    elif system in {"Linux", "Darwin"}:
        executable = shutil.which("bash")
        script = root / "scripts" / "register-workspace-scheduler.sh"
    else:
        raise ValueError(f"Scheduler service integration is unsupported on {system}.")
    if not executable:
        raise ValueError(f"Scheduler service shell is unavailable on {system}.")
    if not script.is_file():
        raise ValueError(f"Scheduler registration script is missing: {script}")
    if system == "Windows":
        command = [
            executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-DistributionRoot", str(root),
        ]
        if normalized == "install":
            command.extend(("-EveryMinutes", str(every_minutes)))
            if run_now:
                command.append("-RunNow")
        elif normalized == "uninstall":
            command.append("-Unregister")
        else:
            command.append("-Status")
    else:
        command = [executable, str(script), f"--{normalized}", "--distribution", str(root)]
        if normalized == "install":
            command.extend(("--every", str(every_minutes)))
            if run_now:
                command.append("--run-now")
    completed = runner(command, text=True, capture_output=True, check=False, timeout=60)
    return SchedulerServiceResult(normalized, tuple(command), completed.returncode, completed.stdout, completed.stderr)
