from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from .durable_io import atomic_write_text
from .domain import WorkItem
from .role_profiles import ProjectRoleProfile, RoleSelection, write_role_profile
from .state import HohStateStore


class ProjectSpecError(ValueError):
    pass


@dataclass(frozen=True)
class ProjectTaskSpec:
    id: str
    title: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    verification_commands: tuple[str, ...]
    allowed_paths: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    priority: int = 0

    def to_work_item(self) -> WorkItem:
        return WorkItem(
            id=self.id,
            title=self.title,
            objective=self.objective,
            acceptance_criteria=self.acceptance_criteria,
            verification_commands=self.verification_commands,
            allowed_paths=self.allowed_paths,
            non_goals=self.non_goals,
            depends_on=self.depends_on,
            priority=self.priority,
        )


@dataclass(frozen=True)
class ProjectSpec:
    id: str
    title: str
    goal: str
    customer: str
    business_requirements: tuple[str, ...]
    definition_of_done: tuple[str, ...]
    tasks: tuple[ProjectTaskSpec, ...]
    non_functional_requirements: tuple[str, ...] = ()
    documentation_requirements: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    spec_version: str = "1.0"
    orchestration: ProjectRoleProfile | None = None


@dataclass(frozen=True)
class ProjectPlanMaterialization:
    brief_path: Path
    roadmap_path: Path
    task_paths: tuple[Path, ...]
    enqueued_task_ids: tuple[str, ...] = ()
    role_profile_path: Path | None = None


def load_project_spec(path: Path) -> ProjectSpec:
    suffix = path.suffix.lower()
    if suffix != ".json":
        raise ProjectSpecError(f"Unsupported project spec extension: {path.suffix}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectSpecError(f"Invalid JSON project spec: {path}") from exc
    if not isinstance(raw, dict):
        raise ProjectSpecError("Project spec must contain a JSON object.")
    return project_spec_from_mapping(raw)


def project_spec_from_mapping(data: dict[str, Any]) -> ProjectSpec:
    """Validate a decoded project spec, including model-produced specs."""
    return _project_spec_from_mapping(data)


def materialize_project_plan(
    project_root: Path,
    spec: ProjectSpec,
    write: bool,
    queue: HohStateStore | None = None,
) -> ProjectPlanMaterialization:
    docs_dir = project_root / "docs" / "hoh"
    tasks_dir = project_root / "tasks" / "hoh"
    brief_path = docs_dir / "project-brief.md"
    roadmap_path = docs_dir / "roadmap.md"
    task_paths = tuple(tasks_dir / f"{index:03d}-{_safe_filename(task.id)}.json" for index, task in enumerate(spec.tasks, 1))

    enqueued: list[str] = []
    profile_path: Path | None = None
    if write:
        docs_dir.mkdir(parents=True, exist_ok=True)
        tasks_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(brief_path, render_project_brief(spec))
        atomic_write_text(roadmap_path, render_project_roadmap(spec))
        if spec.orchestration is not None:
            profile_path = write_role_profile(project_root, spec.orchestration)
        for task, task_path in zip(spec.tasks, task_paths):
            atomic_write_text(
                task_path,
                json.dumps(_task_to_json(task), indent=2, ensure_ascii=False) + "\n",
            )
        if queue is not None:
            queued = queue.enqueue_many(
                tuple(
                    (task.to_work_item(), str(task_path))
                    for task, task_path in zip(spec.tasks, task_paths)
                )
            )
            enqueued.extend(item.work_item.id for item in queued)

    return ProjectPlanMaterialization(
        brief_path=brief_path,
        roadmap_path=roadmap_path,
        task_paths=task_paths,
        enqueued_task_ids=tuple(enqueued),
        role_profile_path=profile_path,
    )


def render_project_brief(spec: ProjectSpec) -> str:
    sections = [
        f"# {spec.title} — Project brief",
        "",
        f"- Project id: `{spec.id}`",
        f"- Customer: {spec.customer}",
        "",
        "## Goal",
        "",
        spec.goal,
        "",
        "## Business requirements",
        "",
        *_bullets(spec.business_requirements),
        "",
        "## Non-functional requirements",
        "",
        *_bullets_or_none(spec.non_functional_requirements),
        "",
        "## Constraints",
        "",
        *_bullets_or_none(spec.constraints),
        "",
        "## Open questions",
        "",
        *_bullets_or_none(spec.open_questions),
        "",
        "## Orchestration roles",
        "",
        *_orchestration_lines(spec.orchestration),
        "",
    ]
    return "\n".join(sections)


def render_project_roadmap(spec: ProjectSpec) -> str:
    sections = [
        f"# {spec.title} — Roadmap",
        "",
        "## Definition of done",
        "",
        *_bullets(spec.definition_of_done),
        "",
        "## Documentation requirements",
        "",
        *_bullets_or_none(spec.documentation_requirements),
        "",
        "## Atomic tasks",
        "",
    ]
    for index, task in enumerate(spec.tasks, 1):
        sections.extend(
            [
                f"### {index}. {task.title}",
                "",
                f"- Task id: `{task.id}`",
                f"- Objective: {task.objective}",
                "- Acceptance criteria:",
                *_indented_bullets(task.acceptance_criteria),
                "- Verification commands:",
                *_indented_bullets(task.verification_commands),
                "- Allowed paths:",
                *_indented_bullets(
                    task.allowed_paths
                    or ("No allowed paths declared; the deterministic gate will reject any patch.",)
                ),
                "- Non-goals:",
                *_indented_bullets(task.non_goals or ("No explicit non-goals.",)),
                f"- Priority: {task.priority}",
                "- Depends on:",
                *_indented_bullets(task.depends_on or ("No dependencies.",)),
                "",
            ]
        )
    return "\n".join(sections)


def _project_spec_from_mapping(data: dict[str, Any]) -> ProjectSpec:
    spec_version = str(data.get("spec_version", "1.0"))
    orchestration = _orchestration_from_mapping(data.get("orchestration"))
    if spec_version == "2.0" and orchestration is None:
        raise ProjectSpecError("Project spec v2 requires an orchestration role selection from the customer brief.")
    if spec_version not in {"1.0", "2.0"}:
        raise ProjectSpecError(f"Unsupported project spec version: {spec_version}")
    tasks = tuple(_task_from_mapping(item) for item in _required_list_of_objects(data, "tasks"))
    if len({task.id for task in tasks}) != len(tasks):
        raise ProjectSpecError("Project spec tasks must have unique ids.")
    _validate_task_graph(tasks)
    return ProjectSpec(
        id=_required_string(data, "id"),
        title=_required_string(data, "title"),
        goal=_required_string(data, "goal"),
        customer=_required_string(data, "customer"),
        business_requirements=_required_string_tuple(data, "business_requirements"),
        definition_of_done=_required_string_tuple(data, "definition_of_done"),
        tasks=tasks,
        non_functional_requirements=_optional_string_tuple(data, "non_functional_requirements"),
        documentation_requirements=_optional_string_tuple(data, "documentation_requirements"),
        constraints=_optional_string_tuple(data, "constraints"),
        open_questions=_optional_string_tuple(data, "open_questions"),
        spec_version=spec_version,
        orchestration=orchestration,
    )


def _orchestration_from_mapping(value: Any) -> ProjectRoleProfile | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProjectSpecError("Project spec orchestration must be an object.")
    critic_value = value.get("critic")
    return ProjectRoleProfile(
        supervisor=_role_selection(value.get("supervisor"), "supervisor"),
        worker=_role_selection(value.get("worker"), "worker"),
        critic=_role_selection(critic_value, "critic") if critic_value is not None else None,
        max_attempts=int(value.get("max_attempts", 3)),
    )


def _role_selection(value: Any, label: str) -> RoleSelection:
    if not isinstance(value, dict):
        raise ProjectSpecError(f"Project spec orchestration.{label} must be an object.")
    agent = value.get("agent")
    model = value.get("model")
    driver = value.get("driver")
    if not isinstance(agent, str) or not agent.strip():
        raise ProjectSpecError(f"Project spec orchestration.{label}.agent must be a non-empty string.")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ProjectSpecError(f"Project spec orchestration.{label}.model must be a non-empty string or null.")
    if driver is not None and (not isinstance(driver, str) or not driver.strip()):
        raise ProjectSpecError(f"Project spec orchestration.{label}.driver must be a non-empty string or null.")
    return RoleSelection(
        agent.strip(),
        model.strip() if isinstance(model, str) else None,
        driver.strip() if isinstance(driver, str) else None,
    )


def _task_from_mapping(data: dict[str, Any]) -> ProjectTaskSpec:
    return ProjectTaskSpec(
        id=_required_string(data, "id"),
        title=_required_string(data, "title"),
        objective=_required_string(data, "objective"),
        acceptance_criteria=_required_string_tuple(data, "acceptance_criteria"),
        verification_commands=_required_string_tuple(data, "verification_commands"),
        allowed_paths=_optional_string_tuple(data, "allowed_paths"),
        non_goals=_optional_string_tuple(data, "non_goals"),
        depends_on=_optional_string_tuple(data, "depends_on"),
        priority=_optional_integer(data, "priority"),
    )


def _task_to_json(task: ProjectTaskSpec) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "objective": task.objective,
        "acceptance_criteria": list(task.acceptance_criteria),
        "verification_commands": list(task.verification_commands),
        "allowed_paths": list(task.allowed_paths),
        "non_goals": list(task.non_goals),
        "depends_on": list(task.depends_on),
        "priority": task.priority,
    }


def _validate_task_graph(tasks: tuple[ProjectTaskSpec, ...]) -> None:
    task_ids = {task.id for task in tasks}
    for task in tasks:
        unknown = [dependency for dependency in task.depends_on if dependency not in task_ids]
        if unknown:
            raise ProjectSpecError(
                f"Task {task.id} has unknown dependencies: {', '.join(unknown)}."
            )
        if len(set(task.depends_on)) != len(task.depends_on):
            raise ProjectSpecError(f"Task {task.id} contains duplicate dependencies.")

    graph = {task.id: task.depends_on for task in tasks}
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visited:
            return
        if task_id in visiting:
            cycle = visiting[visiting.index(task_id):] + [task_id]
            raise ProjectSpecError(f"Project task dependency cycle: {' -> '.join(cycle)}.")
        visiting.append(task_id)
        for dependency in graph[task_id]:
            visit(dependency)
        visiting.pop()
        visited.add(task_id)

    for task in tasks:
        visit(task.id)


def _required_list_of_objects(data: dict[str, Any], key: str) -> tuple[dict[str, Any], ...]:
    value = data.get(key)
    if not isinstance(value, list) or not value:
        raise ProjectSpecError(f"Project spec field '{key}' must contain at least one object.")
    objects: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ProjectSpecError(f"Project spec field '{key}' must contain objects.")
        objects.append(item)
    return tuple(objects)


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProjectSpecError(f"Project spec field '{key}' must be a non-empty string.")
    return value.strip()


def _required_string_tuple(data: dict[str, Any], key: str) -> tuple[str, ...]:
    values = _optional_string_tuple(data, key)
    if not values:
        raise ProjectSpecError(f"Project spec field '{key}' must contain at least one item.")
    return values


def _optional_string_tuple(data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ProjectSpecError(f"Project spec field '{key}' must be a list of strings.")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ProjectSpecError(f"Project spec field '{key}' must be a list of non-empty strings.")
        items.append(item.strip())
    return tuple(items)


def _optional_integer(data: dict[str, Any], key: str) -> int:
    value = data.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProjectSpecError(f"Project spec field '{key}' must be an integer.")
    return value


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return safe or "task"


def _bullets(items: tuple[str, ...]) -> list[str]:
    return [f"- {item}" for item in items]


def _bullets_or_none(items: tuple[str, ...]) -> list[str]:
    return _bullets(items) if items else ["- None."]


def _indented_bullets(items: tuple[str, ...]) -> list[str]:
    return [f"  - {item}" for item in items]


def _orchestration_lines(profile: ProjectRoleProfile | None) -> list[str]:
    if profile is None:
        return ["- Legacy project spec: roles are resolved from existing runtime configuration."]
    critic = (
        f"{profile.critic.agent} / {profile.critic.model or 'agent default'} / "
        f"driver {profile.critic.driver or 'agent catalog'}"
        if profile.critic
        else "Disabled — Supervisor closes only after deterministic verification."
    )
    return [
        f"- Supervisor: {profile.supervisor.agent} / {profile.supervisor.model or 'agent default'}",
        f"- Worker: {profile.worker.agent} / {profile.worker.model or 'agent default'} / "
        f"driver {profile.worker.driver or 'agent catalog'}",
        f"- Critic: {critic}",
        f"- Maximum worker attempts: {profile.max_attempts}",
        "- Role selection was captured in the customer brief; no manual TOML editing is required.",
    ]
