from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import hashlib
import os
from pathlib import Path
from typing import Any, Mapping

from .coordination import FileLease
from .durable_io import atomic_write_json, read_json
from .state import HohStateStore, default_state_root


WORKSPACE_VERSION = 1


@dataclass(frozen=True)
class ProjectSchedule:
    enabled: bool = False
    interval_minutes: int = 60
    next_run_at_utc: str | None = None
    final_audit: bool = False
    notify_windows: bool = True
    notify_telegram: bool = False

    def __post_init__(self) -> None:
        if self.interval_minutes < 1:
            raise ValueError("Project schedule interval must be at least one minute.")
        if self.next_run_at_utc is not None:
            _parse_utc(self.next_run_at_utc)


@dataclass(frozen=True)
class WorkspaceProject:
    project_id: str
    name: str
    root: str
    registered_at_utc: str
    schedule: ProjectSchedule = ProjectSchedule()
    last_run_at_utc: str | None = None
    last_run_status: str | None = None
    last_run_error: str | None = None

    def __post_init__(self) -> None:
        if not self.project_id.strip() or not self.name.strip() or not self.root.strip():
            raise ValueError("Workspace project id, name, and root must be non-empty.")
        _parse_utc(self.registered_at_utc)
        if self.last_run_at_utc is not None:
            _parse_utc(self.last_run_at_utc)


@dataclass(frozen=True)
class WorkspaceSnapshot:
    projects: tuple[WorkspaceProject, ...] = ()
    version: int = WORKSPACE_VERSION


@dataclass(frozen=True)
class ProjectQueueSummary:
    project_id: str
    name: str
    root: str
    exists: bool
    queued: int = 0
    ready: int = 0
    waiting: int = 0
    running: int = 0
    review_pending: int = 0
    rework_required: int = 0
    escalated: int = 0
    rolled_back: int = 0
    failed: int = 0
    done: int = 0
    next_task_id: str | None = None
    next_task_title: str | None = None
    schedule_enabled: bool = False
    schedule_interval_minutes: int = 60
    schedule_final_audit: bool = False
    schedule_notify_windows: bool = True
    schedule_notify_telegram: bool = False
    next_run_at_utc: str | None = None
    last_run_status: str | None = None
    error: str | None = None


def workspace_registry_path(environment: Mapping[str, str] | None = None) -> Path:
    env = environment if environment is not None else os.environ
    override = env.get("HOH_WORKSPACE_REGISTRY")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and env.get("LOCALAPPDATA"):
        return Path(env["LOCALAPPDATA"]) / "HoH" / "workspace-v1.json"
    state_home = Path(env["XDG_STATE_HOME"]) if env.get("XDG_STATE_HOME") else Path.home() / ".local" / "state"
    return state_home / "hoh" / "workspace-v1.json"


class WorkspaceRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or workspace_registry_path()).resolve()
        self.lease = FileLease(
            self.path.with_suffix(self.path.suffix + ".lock"),
            kind="workspace-registry",
            default_timeout_seconds=5.0,
            poll_interval_seconds=0.05,
        )

    def load(self) -> WorkspaceSnapshot:
        payload = read_json(self.path, default={"version": WORKSPACE_VERSION, "projects": []})
        if not isinstance(payload, dict) or not isinstance(payload.get("projects", []), list):
            raise ValueError("Workspace registry must contain a projects list.")
        version = int(payload.get("version", WORKSPACE_VERSION))
        if version != WORKSPACE_VERSION:
            raise ValueError(f"Unsupported workspace registry version: {version}")
        return WorkspaceSnapshot(tuple(_project_from_payload(item) for item in payload["projects"]), version)

    def register(self, root: Path, name: str | None = None) -> WorkspaceProject:
        resolved = _validated_git_root(root)
        now = _utc_now()
        project_id = hashlib.sha256(_path_key(resolved).encode("utf-8")).hexdigest()[:16]
        candidate = WorkspaceProject(project_id, (name or resolved.name).strip(), str(resolved), now)
        with self.lease.hold("workspace.register"):
            snapshot = self.load()
            projects = list(snapshot.projects)
            existing = next((item for item in projects if _path_key(Path(item.root)) == _path_key(resolved)), None)
            if existing is not None:
                candidate = replace(existing, name=candidate.name)
                projects[projects.index(existing)] = candidate
            else:
                projects.append(candidate)
            self._write(tuple(projects))
        return candidate

    def remove(self, project_id: str) -> WorkspaceProject:
        with self.lease.hold("workspace.remove"):
            snapshot = self.load()
            project = _find_project(snapshot, project_id)
            self._write(tuple(item for item in snapshot.projects if item.project_id != project.project_id))
        return project

    def configure_schedule(
        self,
        project_id: str,
        *,
        enabled: bool,
        interval_minutes: int,
        final_audit: bool = False,
        notify_windows: bool = True,
        notify_telegram: bool = False,
        start_immediately: bool = False,
    ) -> WorkspaceProject:
        with self.lease.hold("workspace.schedule"):
            snapshot = self.load()
            project = _find_project(snapshot, project_id)
            next_run = None
            if enabled:
                delay = 0 if start_immediately else interval_minutes
                next_run = (datetime.now(UTC) + timedelta(minutes=delay)).isoformat()
            schedule = ProjectSchedule(
                enabled=enabled,
                interval_minutes=interval_minutes,
                next_run_at_utc=next_run,
                final_audit=final_audit,
                notify_windows=notify_windows,
                notify_telegram=notify_telegram,
            )
            updated = replace(project, schedule=schedule)
            self._replace(snapshot, updated)
        return updated

    def due_projects(self, now: datetime | None = None) -> tuple[WorkspaceProject, ...]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        return tuple(
            project for project in self.load().projects
            if project.schedule.enabled
            and project.schedule.next_run_at_utc is not None
            and _parse_utc(project.schedule.next_run_at_utc) <= current
        )

    def record_run(
        self,
        project_id: str,
        status: str,
        error: str | None = None,
        now: datetime | None = None,
    ) -> WorkspaceProject:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        with self.lease.hold("workspace.record_run"):
            snapshot = self.load()
            project = _find_project(snapshot, project_id)
            schedule = project.schedule
            next_run = (
                (current + timedelta(minutes=schedule.interval_minutes)).isoformat()
                if schedule.enabled else None
            )
            updated = replace(
                project,
                schedule=replace(schedule, next_run_at_utc=next_run),
                last_run_at_utc=current.isoformat(),
                last_run_status=status.strip() or "unknown",
                last_run_error=(error or "").strip() or None,
            )
            self._replace(snapshot, updated)
        return updated

    def summaries(self) -> tuple[ProjectQueueSummary, ...]:
        return tuple(_project_summary(project) for project in self.load().projects)

    def _replace(self, snapshot: WorkspaceSnapshot, updated: WorkspaceProject) -> None:
        projects = tuple(updated if item.project_id == updated.project_id else item for item in snapshot.projects)
        self._write(projects)

    def _write(self, projects: tuple[WorkspaceProject, ...]) -> None:
        atomic_write_json(
            self.path,
            {
                "version": WORKSPACE_VERSION,
                "projects": [_project_payload(item) for item in projects],
            },
        )


def _project_summary(project: WorkspaceProject) -> ProjectQueueSummary:
    root = Path(project.root)
    if not root.is_dir() or not (root / ".git").exists():
        return ProjectQueueSummary(
            project.project_id, project.name, project.root, False,
            schedule_enabled=project.schedule.enabled,
            schedule_interval_minutes=project.schedule.interval_minutes,
            schedule_final_audit=project.schedule.final_audit,
            schedule_notify_windows=project.schedule.notify_windows,
            schedule_notify_telegram=project.schedule.notify_telegram,
            next_run_at_utc=project.schedule.next_run_at_utc,
            last_run_status=project.last_run_status,
            error="Registered Git project is unavailable.",
        )
    try:
        store = HohStateStore(default_state_root(root))
        tasks = store.list_tasks()
        latest = {item.work_item.id: item for item in tasks}
        counts = {
            status: 0 for status in (
                "queued", "running", "review_pending", "rework_required",
                "escalated", "rolled_back", "failed", "done",
            )
        }
        for task in latest.values():
            if task.status in counts:
                counts[task.status] += 1
        schedule = store.schedule()
        selected = schedule.selected
        return ProjectQueueSummary(
            project.project_id,
            project.name,
            project.root,
            True,
            queued=counts["queued"],
            ready=len(schedule.ready),
            waiting=len(schedule.waiting),
            running=counts["running"],
            review_pending=counts["review_pending"],
            rework_required=counts["rework_required"],
            escalated=counts["escalated"],
            rolled_back=counts["rolled_back"],
            failed=counts["failed"],
            done=counts["done"],
            next_task_id=selected.work_item.id if selected else None,
            next_task_title=selected.work_item.title if selected else None,
            schedule_enabled=project.schedule.enabled,
            schedule_interval_minutes=project.schedule.interval_minutes,
            schedule_final_audit=project.schedule.final_audit,
            schedule_notify_windows=project.schedule.notify_windows,
            schedule_notify_telegram=project.schedule.notify_telegram,
            next_run_at_utc=project.schedule.next_run_at_utc,
            last_run_status=project.last_run_status,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return ProjectQueueSummary(
            project.project_id, project.name, project.root, True,
            schedule_enabled=project.schedule.enabled,
            schedule_interval_minutes=project.schedule.interval_minutes,
            schedule_final_audit=project.schedule.final_audit,
            schedule_notify_windows=project.schedule.notify_windows,
            schedule_notify_telegram=project.schedule.notify_telegram,
            next_run_at_utc=project.schedule.next_run_at_utc,
            last_run_status=project.last_run_status,
            error=str(exc),
        )


def workspace_payload(snapshot: WorkspaceSnapshot) -> dict[str, Any]:
    return {"version": snapshot.version, "projects": [_project_payload(item) for item in snapshot.projects]}


def summary_payload(summary: ProjectQueueSummary) -> dict[str, Any]:
    return dict(summary.__dict__)


def _validated_git_root(root: Path) -> Path:
    resolved = root.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f"Project directory does not exist: {resolved}")
    if not (resolved / ".git").exists():
        raise ValueError(f"Project is not a Git repository: {resolved}")
    return resolved


def _find_project(snapshot: WorkspaceSnapshot, project_id: str) -> WorkspaceProject:
    normalized = project_id.strip()
    project = next((item for item in snapshot.projects if item.project_id == normalized), None)
    if project is None:
        raise ValueError(f"Unknown workspace project: {project_id}")
    return project


def _project_from_payload(payload: object) -> WorkspaceProject:
    if not isinstance(payload, dict):
        raise ValueError("Workspace project entries must be objects.")
    schedule_payload = payload.get("schedule") or {}
    if not isinstance(schedule_payload, dict):
        raise ValueError("Workspace project schedule must be an object.")
    return WorkspaceProject(
        project_id=str(payload["project_id"]),
        name=str(payload["name"]),
        root=str(payload["root"]),
        registered_at_utc=str(payload["registered_at_utc"]),
        schedule=ProjectSchedule(
            enabled=bool(schedule_payload.get("enabled", False)),
            interval_minutes=int(schedule_payload.get("interval_minutes", 60)),
            next_run_at_utc=_optional_text(schedule_payload.get("next_run_at_utc")),
            final_audit=bool(schedule_payload.get("final_audit", False)),
            notify_windows=bool(schedule_payload.get("notify_windows", True)),
            notify_telegram=bool(schedule_payload.get("notify_telegram", False)),
        ),
        last_run_at_utc=_optional_text(payload.get("last_run_at_utc")),
        last_run_status=_optional_text(payload.get("last_run_status")),
        last_run_error=_optional_text(payload.get("last_run_error")),
    )


def _project_payload(project: WorkspaceProject) -> dict[str, Any]:
    return {
        "project_id": project.project_id,
        "name": project.name,
        "root": project.root,
        "registered_at_utc": project.registered_at_utc,
        "schedule": dict(project.schedule.__dict__),
        "last_run_at_utc": project.last_run_at_utc,
        "last_run_status": project.last_run_status,
        "last_run_error": project.last_run_error,
    }


def _path_key(path: Path) -> str:
    text = str(path.expanduser().resolve())
    return text.casefold() if os.name == "nt" else text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid UTC timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"UTC timestamp must contain a timezone: {value}")
    return parsed.astimezone(UTC)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
