from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable

from .config import HarnessConfig, load_config, project_config_path
from .desktop_notifications import DesktopNotifier
from .telegram import build_notifier
from .workspace import WorkspaceProject, WorkspaceRegistry


@dataclass(frozen=True)
class ScheduledProjectResult:
    project_id: str
    name: str
    root: str
    command: tuple[str, ...]
    return_code: int
    status: str
    completed: int
    stdout: str
    stderr: str
    notification_errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.return_code == 0


def run_due_projects(
    registry: WorkspaceRegistry,
    *,
    timeout_seconds: float = 3600.0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    desktop_notifier: DesktopNotifier | None = None,
) -> tuple[ScheduledProjectResult, ...]:
    results: list[ScheduledProjectResult] = []
    for project in registry.due_projects():
        result = run_registered_project(
            project,
            timeout_seconds=timeout_seconds,
            runner=runner,
            desktop_notifier=desktop_notifier,
        )
        registry.record_run(project.project_id, result.status, _result_error(result))
        results.append(result)
    return tuple(results)


def run_registered_project(
    project: WorkspaceProject,
    *,
    timeout_seconds: float = 3600.0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    desktop_notifier: DesktopNotifier | None = None,
) -> ScheduledProjectResult:
    root = Path(project.root)
    command = [
        sys.executable,
        "-m",
        "llm_harness",
        "queue-run-loop",
        "--project-root",
        str(root),
        "--json",
    ]
    if project.schedule.final_audit:
        command.append("--final-audit")
    try:
        completed = runner(
            command,
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        status, count = _parse_run_output(completed.stdout, completed.returncode)
        result = ScheduledProjectResult(
            project.project_id,
            project.name,
            project.root,
            tuple(command),
            completed.returncode,
            status,
            count,
            completed.stdout,
            completed.stderr,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        result = ScheduledProjectResult(
            project.project_id,
            project.name,
            project.root,
            tuple(command),
            1,
            "launch_failed",
            0,
            "",
            str(exc),
        )
    errors = _send_notifications(project, result, desktop_notifier)
    return ScheduledProjectResult(**{**result.__dict__, "notification_errors": errors})


def scheduled_result_payload(result: ScheduledProjectResult) -> dict[str, object]:
    return {
        "project_id": result.project_id,
        "name": result.name,
        "root": result.root,
        "command": list(result.command),
        "return_code": result.return_code,
        "status": result.status,
        "completed": result.completed,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "notification_errors": list(result.notification_errors),
        "ok": result.ok,
    }


def _parse_run_output(stdout: str, return_code: int) -> tuple[str, int]:
    try:
        payload = json.loads(stdout)
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            return str(data.get("status") or ("completed" if return_code == 0 else "failed")), int(data.get("completed", 0))
    except (ValueError, TypeError):
        pass
    return ("completed" if return_code == 0 else "failed"), 0


def _send_notifications(
    project: WorkspaceProject,
    result: ScheduledProjectResult,
    desktop_notifier: DesktopNotifier | None,
) -> tuple[str, ...]:
    errors: list[str] = []
    title = f"HoH · {project.name}"
    text = f"status={result.status} · completed={result.completed}"
    if project.schedule.notify_windows:
        record = (desktop_notifier or DesktopNotifier()).notify(title, text)
        if not record.delivered:
            errors.append(f"desktop: {record.error or 'delivery failed'}")
    if project.schedule.notify_telegram:
        try:
            path = project_config_path(Path(project.root))
            config = load_config(path) if path.exists() else HarnessConfig()
            if not config.telegram.enabled:
                raise ValueError("Telegram summary is enabled for the schedule but disabled in project config.")
            notifier = build_notifier(config.telegram)
            if result.status in {"empty", "ready"} and result.ok:
                notifier.notify_project_ready(f"{title}\n{text}")
            elif result.status == "audit_failed":
                notifier.notify_audit_failed(f"{title}\n{text}")
            else:
                notifier.notify_user_action_required(f"{title}\n{text}")
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(f"telegram: {exc}")
    return tuple(errors)


def _result_error(result: ScheduledProjectResult) -> str | None:
    if result.ok and not result.notification_errors:
        return None
    details = [item for item in (result.stderr.strip(), *result.notification_errors) if item]
    return " | ".join(details) or f"queue-run-loop exit {result.return_code}"
