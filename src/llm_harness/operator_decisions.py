from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .state import HohStateStore, QueueTask, StateStoreError
from .operations import OperationLedger


@dataclass(frozen=True)
class OperatorDecisionResult:
    command: str
    ok: bool
    message: str
    task_id: str | None = None
    should_continue: bool = False


SUPPORTED_COMMANDS_HELP = (
    "Supported commands: /status, /queue, /stale, /retry <task-id>, "
    "/recover <task-id> [reason], /continue, /stop."
)


def handle_operator_command(
    store: HohStateStore,
    text: str,
    stale_minutes: float = 60.0,
    repository: Path | None = None,
) -> OperatorDecisionResult:
    with store.transaction("operator.command"):
        raw_text = text
        command, arguments = _parse_command(text)
        try:
            result = _execute_command(store, command, arguments, stale_minutes, repository)
        except StateStoreError as exc:
            result = OperatorDecisionResult(
                command=command,
                ok=False,
                message=str(exc),
                task_id=_first_arg(arguments),
            )
        store.record_operator_event(
            command=result.command,
            ok=result.ok,
            message=result.message,
            raw_text=raw_text,
            task_id=result.task_id,
        )
        return result


def _execute_command(
    store: HohStateStore,
    command: str,
    arguments: tuple[str, ...],
    stale_minutes: float,
    repository: Path | None,
) -> OperatorDecisionResult:
    if command == "status":
        result = _status(store, stale_minutes)
        if repository is not None:
            pending = len(OperationLedger(repository).reconcile().blocking)
            result = OperatorDecisionResult(
                command=result.command,
                ok=result.ok and pending == 0,
                message=f"{result.message} Reconciliation pending={pending}.",
            )
        return result
    if command == "queue":
        return _queue(store)
    if command == "stale":
        return _stale(store, stale_minutes)
    if command == "retry":
        task_id = _require_task_id(command, arguments)
        task = store.requeue_failed(task_id)
        return OperatorDecisionResult(
            command=command,
            ok=True,
            message=f"Task {task.work_item.id} requeued from failed status.",
            task_id=task.work_item.id,
        )
    if command == "recover":
        task_id = _require_task_id(command, arguments)
        if repository is not None:
            ledger = OperationLedger(repository)
            blocking_ids = {
                item.operation_id for item in ledger.reconcile().blocking
            }
            if any(
                record.get("operation_id") in blocking_ids
                and record.get("task_id") == task_id
                for record in ledger.records()
            ):
                raise StateStoreError(
                    f"Task {task_id} has unresolved canonical operation evidence; reconcile it first."
                )
        reason = " ".join(arguments[1:]).strip() or "Recovered by operator command."
        task = store.recover_running(task_id, reason)
        return OperatorDecisionResult(
            command=command,
            ok=True,
            message=f"Task {task.work_item.id} recovered to queued status.",
            task_id=task.work_item.id,
        )
    if command == "continue":
        return OperatorDecisionResult(
            command=command,
            ok=True,
            message="Continue requested. Run queue-run-loop to resume queued work.",
            should_continue=True,
        )
    if command == "stop":
        return OperatorDecisionResult(
            command=command,
            ok=True,
            message="Stop acknowledged. Nothing was changed; the task stays exactly as it is.",
        )
    if command == "delegate":
        return OperatorDecisionResult(
            command=command,
            ok=False,
            message=(
                "HoH does not decide on your behalf: an escalation is exactly the case where the "
                f"evidence is not conclusive. Choose one explicitly. {SUPPORTED_COMMANDS_HELP}"
            ),
        )
    return OperatorDecisionResult(
        command=command,
        ok=False,
        message=(
            "Unrecognised operator command. The text was recorded in the operator event log as a "
            f"note, and no queue state was changed. {SUPPORTED_COMMANDS_HELP}"
        ),
    )


def _status(store: HohStateStore, stale_minutes: float) -> OperatorDecisionResult:
    tasks = store.list_tasks()
    schedule = store.schedule()
    counts = {
        status: 0
        for status in (
            "queued",
            "running",
            "review_pending",
            "rework_required",
            "escalated",
            "done",
            "failed",
            "rolled_back",
        )
    }
    for task in tasks:
        counts[task.status] += 1
    stale_count = len(store.stale_running_tasks(stale_minutes))
    return OperatorDecisionResult(
        command="status",
        ok=True,
        message=(
            f"Queue status: queued={counts['queued']} running={counts['running']} "
            f"ready={len(schedule.ready)} waiting={len(schedule.waiting)} "
            f"done={counts['done']} failed={counts['failed']} "
            f"rolled_back={counts['rolled_back']} stale={stale_count}."
        ),
    )


def _queue(store: HohStateStore) -> OperatorDecisionResult:
    tasks = store.list_tasks()
    schedule = store.schedule()
    readiness = {
        item.task.work_item.id: item
        for item in (*schedule.ready, *schedule.waiting)
    }
    if not tasks:
        message = "Queue is empty."
    else:
        message = "Queue:\n" + "\n".join(
            _task_line(task, readiness.get(task.work_item.id))
            for task in tasks
        )
    return OperatorDecisionResult(command="queue", ok=True, message=message)


def _stale(store: HohStateStore, stale_minutes: float) -> OperatorDecisionResult:
    tasks = store.stale_running_tasks(stale_minutes)
    if not tasks:
        message = "No stale running tasks."
    else:
        message = "Stale running tasks:\n" + "\n".join(_task_line(task) for task in tasks)
    return OperatorDecisionResult(command="stale", ok=True, message=message)


def _parse_command(text: str) -> tuple[str, tuple[str, ...]]:
    normalized = text.strip()
    if not normalized:
        return "unknown", ()
    if normalized.casefold() == "реши сам":
        return "delegate", ()
    parts = normalized.split()
    command = parts[0].removeprefix("/").casefold()
    aliases = {
        "q": "queue",
        "recover-running": "recover",
        "resume": "continue",
    }
    return aliases.get(command, command), tuple(parts[1:])


def _require_task_id(command: str, arguments: tuple[str, ...]) -> str:
    task_id = _first_arg(arguments)
    if not task_id:
        raise StateStoreError(f"{command} requires a task id.")
    return task_id


def _first_arg(arguments: tuple[str, ...]) -> str | None:
    return arguments[0] if arguments else None


def _task_line(task: QueueTask, readiness=None) -> str:
    scheduler = ""
    if readiness is not None:
        blockers = ",".join(
            f"{item.task_id}={item.status}"
            for item in readiness.blockers
        )
        scheduler = f", ready={readiness.ready}, blockers={blockers}"
    return (
        f"- {task.work_item.id}: status={task.status}, attempts={task.attempts}, "
        f"priority={task.work_item.priority}{scheduler}, "
        f"commit={task.last_commit or ''}, error={task.last_error or ''}"
    )
