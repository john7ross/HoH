from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys

from .audit import diagram_source_digest, run_project_audit
from .config import AgentConfig, HarnessConfig, WorkerConfig
from .lifecycle import load_project_spec, materialize_project_plan
from .state import HohStateStore, QueueTask
from .supervisor import Supervisor
from .telegram import JournaledNotifier, StubTelegramNotifier
from .trust import WorkerTrustPolicy
from .verifier import PolicyVerifier
from .worker_adapters import create_worker_adapter


@dataclass(frozen=True)
class SupervisedWorkflowSmokeReport:
    ok: bool
    workflow_status: str
    repository: Path
    state_root: Path
    spec_path: Path
    brief_path: Path
    roadmap_path: Path
    task_paths: tuple[Path, ...]
    queued_task_ids: tuple[str, ...]
    completed_tasks: int
    history_count: int
    commits: tuple[str, ...]
    audit_ready: bool | None
    audit_report_path: Path | None
    notifier_messages: tuple[str, ...]
    blocker_task_id: str | None = None
    blocker_reason: str | None = None
    journal_events: int = 0


def run_supervised_workflow_smoke(
    repository: Path,
    state_root: Path | None = None,
    mode: str = "ready",
) -> SupervisedWorkflowSmokeReport:
    if mode not in {"ready", "blocker"}:
        raise ValueError("workflow smoke mode must be 'ready' or 'blocker'.")

    repository.mkdir(parents=True, exist_ok=True)
    state = state_root or (repository.parent / f"{repository.name}-state")
    raw_notifier = StubTelegramNotifier()
    _init_ready_repository(repository)

    spec_path = repository / "project-spec.json"
    spec_path.write_text(json.dumps(_project_spec_payload(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    spec = load_project_spec(spec_path)
    store = HohStateStore(state)
    notifier = JournaledNotifier(raw_notifier, store.journal)
    materialized = materialize_project_plan(repository, spec, write=True, queue=store)
    store.journal.record(
        "interaction", "supervisor", "project.spec_submitted", recipient="hoh",
        content=json.loads(spec_path.read_text(encoding="utf-8")),
        metadata={"role_profile_path": str(materialized.role_profile_path)},
    )
    _git(repository, "add", "project-spec.json", "docs/hoh", "tasks/hoh", ".hoh/role-profile.json", check=True)
    plan_commit = _commit(repository, "supervisor: materialize project plan")

    config = _smoke_config(mode)
    completed = 0
    commits: list[str] = [plan_commit]
    blocker_task_id: str | None = None
    blocker_reason: str | None = None

    while True:
        task = store.next_queued()
        if task is None:
            break
        result = _run_queue_task(repository, store, task, config, notifier)
        if result["commit"]:
            commits.append(str(result["commit"]))
        if result["blocked"]:
            blocker_task_id = task.work_item.id
            blocker_reason = str(result["error"] or "workflow smoke task blocked")
            _notify_smoke_blocker(notifier, blocker_task_id, blocker_reason)
            return SupervisedWorkflowSmokeReport(
                ok=mode == "blocker" and bool(raw_notifier.messages),
                workflow_status="blocked",
                repository=repository,
                state_root=state,
                spec_path=spec_path,
                brief_path=materialized.brief_path,
                roadmap_path=materialized.roadmap_path,
                task_paths=materialized.task_paths,
                queued_task_ids=materialized.enqueued_task_ids,
                completed_tasks=completed,
                history_count=len(store.history()),
                commits=tuple(commits),
                audit_ready=None,
                audit_report_path=None,
                notifier_messages=tuple(raw_notifier.messages),
                blocker_task_id=blocker_task_id,
                blocker_reason=blocker_reason,
                journal_events=len(store.journal.events()),
            )
        completed += 1

    report = run_project_audit(repository, _final_check_commands())
    audit_record = store.record_audit_report(
        report.to_markdown(),
        ok=report.ok,
        findings_count=len(report.findings),
        command_results_count=len(report.command_results),
    )
    if report.ok:
        notifier.notify_project_ready(
            "HoH project ready for handoff.\n"
            f"Project: {repository}\n"
            f"Audit evidence: {audit_record.report_path}\n"
            f"Verification commands: {len(report.command_results)}\n"
            "Final audit: passed."
        )
        workflow_status = "ready"
    else:
        notifier.notify_audit_failed(
            "HoH final audit failed.\n"
            f"Project: {repository}\n"
            f"Audit evidence: {audit_record.report_path}\n"
            f"Findings: {len(report.findings)}"
        )
        workflow_status = "audit_failed"

    return SupervisedWorkflowSmokeReport(
        ok=mode == "ready" and report.ok and bool(raw_notifier.messages),
        workflow_status=workflow_status,
        repository=repository,
        state_root=state,
        spec_path=spec_path,
        brief_path=materialized.brief_path,
        roadmap_path=materialized.roadmap_path,
        task_paths=materialized.task_paths,
        queued_task_ids=materialized.enqueued_task_ids,
        completed_tasks=completed,
        history_count=len(store.history()),
        commits=tuple(commits),
        audit_ready=report.ok,
        audit_report_path=audit_record.report_path,
        notifier_messages=tuple(raw_notifier.messages),
        journal_events=len(store.journal.events()),
    )


def _run_queue_task(
    repository: Path,
    store: HohStateStore,
    task: QueueTask,
    config: HarnessConfig,
    notifier: JournaledNotifier,
) -> dict[str, object]:
    started_at = _utc_now(repository)
    store.mark_running(task.work_item.id)
    try:
        adapter = create_worker_adapter(
            config.worker,
            event_sink=store.journal.adapter_sink("smoke-worker", task.work_item.id),
        )
        supervisor = Supervisor(
            PolicyVerifier(trust_policy=WorkerTrustPolicy(config.worker_trust_level)),
            notifier=notifier,
            journal=store.journal,
            command_policy=config.verification.policy(),
        )
        if adapter.isolated_worktree:
            result = supervisor.dispatch_work_item_in_attempt(
                repository,
                task.work_item,
                adapter.target,
                adapter.worker,
            )
        else:
            result = supervisor.dispatch_work_item(
                repository,
                task.work_item,
                adapter.target,
                adapter.worker,
            )
    except Exception as exc:
        store.record_failure(task.work_item.id, started_at, str(exc))
        return {"blocked": True, "commit": None, "error": exc}

    store.record_result(task.work_item.id, started_at, result)
    if not result.ok:
        return {"blocked": True, "commit": result.commit, "error": _result_blocker_error(result)}
    return {"blocked": False, "commit": result.commit, "error": None}


def _init_ready_repository(repository: Path) -> None:
    _git(repository, "init", check=True)
    _git(repository, "config", "user.email", "workflow-smoke@example.local", check=True)
    _git(repository, "config", "user.name", "HoH Workflow Smoke", check=True)
    (repository / "README.md").write_text(
        "# HoH supervised workflow smoke\n\n"
        "This disposable project verifies the supervisor-worker-verifier daily flow.\n",
        encoding="utf-8",
    )
    docs = repository / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "architecture.md").write_text(
        "# Architecture\n\n"
        "![C4 component view](hoh-c4-component.png)\n\n"
        "![Main sequence](hoh-sequence.png)\n",
        encoding="utf-8",
    )
    # The audit this smoke project has to satisfy expects PlantUML sources, their
    # committed renders, and a digest file holding the two together. The smoke does
    # not run PlantUML; it only has to produce a project the audit accepts.
    digest_lines: list[str] = []
    for source_name, image_name in (
        ("architecture-c4-component.puml", "hoh-c4-component.png"),
        ("architecture-sequence.puml", "hoh-sequence.png"),
    ):
        source = docs / source_name
        source.write_text("@startuml\n@enduml\n", encoding="utf-8")
        (docs / image_name).write_bytes(b"\x89PNG\r\n\x1a\n")
        digest_lines.append(f"{diagram_source_digest(source)}  {source_name}\n")
    (docs / "diagrams.sha256").write_text("".join(digest_lines), encoding="utf-8")
    _git(repository, "add", "README.md", "docs", check=True)
    _commit(repository, "Initial ready project")


def _project_spec_payload() -> dict:
    return {
        "spec_version": "2.0",
        "id": "workflow-smoke",
        "title": "HoH supervised workflow smoke",
        "goal": "Verify a complete daily supervisor-worker-verifier workflow.",
        "customer": "HoH operator",
        "business_requirements": [
            "The supervisor plan is persisted before worker execution.",
            "Queued worker tasks are explicit and verifiable.",
            "Final readiness produces audit evidence and a customer notification.",
        ],
        "non_functional_requirements": [
            "The worker runs through an isolated attempt worktree.",
            "The supervisor owns all canonical repository commits.",
        ],
        "documentation_requirements": [
            "Project brief and roadmap are Markdown.",
            "Architecture documentation contains C4 and sequence diagrams.",
        ],
        "definition_of_done": [
            "All queued tasks are done.",
            "Final audit is ready=true.",
            "Readiness notification is emitted through the notifier interface.",
        ],
        "constraints": [
            "No external secrets.",
            "No external Telegram API calls.",
        ],
        "open_questions": [
            "None for the deterministic smoke scenario.",
        ],
        "orchestration": {
            "supervisor": {"agent": "codex", "model": "smoke-supervisor"},
            "worker": {"agent": "python", "model": "deterministic-smoke-worker"},
            "critic": None,
            "max_attempts": 2,
        },
        "tasks": [
            {
                "id": "smoke-deliverable",
                "title": "Create smoke deliverable",
                "objective": "Create DELIVERABLE.md with workflow smoke evidence.",
                "acceptance_criteria": ["DELIVERABLE.md exists."],
                "verification_commands": [
                    f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'DELIVERABLE.md\').exists()"'
                ],
                "allowed_paths": ["DELIVERABLE.md"],
                "non_goals": ["Do not edit docs/operations.md."],
            },
            {
                "id": "smoke-ops-doc",
                "title": "Create smoke operations note",
                "objective": "Create docs/operations.md with daily operation evidence.",
                "acceptance_criteria": ["docs/operations.md exists."],
                "verification_commands": [
                    f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'docs/operations.md\').exists()"'
                ],
                "allowed_paths": ["docs/operations.md"],
                "non_goals": ["Do not edit DELIVERABLE.md."],
            },
        ],
    }


def _smoke_config(mode: str) -> HarnessConfig:
    script = _failing_worker_script() if mode == "blocker" else _writing_worker_script()
    return HarnessConfig(
        worker=WorkerConfig(
            type="command",
            command=sys.executable,
            args=("-c", script),
            timeout_seconds=30,
        ),
        agents=(AgentConfig(name="python", command=sys.executable),),
    )


def _writing_worker_script() -> str:
    return (
        "import json, os; "
        "from pathlib import Path; "
        "payload=json.loads(os.environ['HOH_WORK_ITEM_JSON']); "
        "title=payload['title']; objective=payload['objective']; "
        "\nfor p in payload.get('allowed_paths', []):\n"
        "    path=Path(p)\n"
        "    path.parent.mkdir(parents=True, exist_ok=True)\n"
        "    path.write_text('# ' + title + '\\n\\n' + objective + '\\n', encoding='utf-8')\n"
    )


def _failing_worker_script() -> str:
    return (
        "import sys; "
        "print('simulated worker blocker', file=sys.stderr); "
        "raise SystemExit(7)"
    )


def _final_check_commands() -> tuple[str, ...]:
    return (
        f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'DELIVERABLE.md\').exists(); assert Path(\'docs/operations.md\').exists()"',
    )


def _notify_smoke_blocker(notifier: JournaledNotifier, task_id: str, error: str) -> None:
    notifier.notify_user_action_required(
        "HoH queue blocked.\n"
        f"Task: {task_id}\n"
        f"Reason: {error}\n\n"
        "Options:\n"
        "- реши сам: supervisor may choose the safest next action from current evidence.\n"
        "- retry: requeue the failed task after review.\n"
        "- stop: leave queue state unchanged for manual inspection.\n"
        "- свой: provide your own instruction."
    )


def _result_blocker_error(result) -> str:
    findings = result.pre_apply.findings + result.post_apply.findings
    if findings:
        return "; ".join(findings)
    failed_commands = [item for item in result.command_results if not item.ok]
    if failed_commands:
        return "; ".join(f"{item.command} exited {item.return_code}" for item in failed_commands)
    return "Worker result was not accepted."


def _commit(repository: Path, message: str) -> str:
    _git(repository, "commit", "-m", message, check=True)
    return _git(repository, "rev-parse", "--short", "HEAD", check=True).stdout.strip()


def _git(repository: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed


def _utc_now(repository: Path) -> str:
    completed = _git(repository, "show", "-s", "--format=%cI", "HEAD")
    if completed.returncode == 0 and completed.stdout.strip():
        return completed.stdout.strip()
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
