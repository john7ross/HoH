from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
from uuid import uuid4

from .attempts import collect_attempt_patch, create_worktree_attempt, remove_worktree_attempt
from .coordination import CoordinationConfig, LockContendedError, repository_execution_lease
from .command_policy import OperatorCommandPolicy
from .domain import CommandResult, WorkItem
from .git_ops import (
    GitError,
    apply_patch,
    commit_changed_files,
    commit_is_ancestor,
    commit_parents,
    commit_staged,
    ensure_git_repository,
    repository_is_clean,
    resolve_commit,
    restore_paths_to_head,
    revert_commit_no_commit,
    run_verification_commands,
    stage_paths,
)
from .state import HohStateStore, RollbackRecord, RunRecord, StateStoreError
from .operations import OperationLedger, crash_point, rollback_to_json
from .verifier import changed_files_from_patch


class RollbackError(RuntimeError):
    pass


@dataclass(frozen=True)
class RollbackBlocker:
    code: str
    message: str
    task_id: str | None = None


@dataclass(frozen=True)
class RollbackPlan:
    work_item_id: str
    run_id: str | None
    target_commit: str | None
    changed_files: tuple[str, ...]
    downstream_task_ids: tuple[str, ...]
    blockers: tuple[RollbackBlocker, ...]

    @property
    def eligible(self) -> bool:
        return not self.blockers and self.run_id is not None and self.target_commit is not None


def plan_rollback(
    repository: Path,
    store: HohStateStore,
    work_item_id: str,
    expected_commit: str | None = None,
) -> RollbackPlan:
    ensure_git_repository(repository)
    blockers: list[RollbackBlocker] = []
    try:
        task = store.latest_task(work_item_id)
    except StateStoreError as exc:
        return RollbackPlan(
            work_item_id=work_item_id,
            run_id=None,
            target_commit=None,
            changed_files=(),
            downstream_task_ids=(),
            blockers=(RollbackBlocker("TASK_NOT_FOUND", str(exc), work_item_id),),
        )

    run = _latest_successful_run(store, work_item_id)
    target_commit: str | None = None
    changed_files: tuple[str, ...] = ()
    if task.status != "done":
        blockers.append(
            RollbackBlocker(
                "TASK_NOT_DONE",
                f"Task must be done before rollback; current status is {task.status}.",
                work_item_id,
            )
        )
    if run is None or run.commit is None:
        blockers.append(
            RollbackBlocker(
                "SUCCESSFUL_RUN_NOT_FOUND",
                "No successful HoH run with a commit was found for the task.",
                work_item_id,
            )
        )
    else:
        try:
            target_commit = resolve_commit(repository, run.commit)
            if task.last_commit is None or resolve_commit(repository, task.last_commit) != target_commit:
                blockers.append(
                    RollbackBlocker(
                        "LATEST_COMMIT_MISMATCH",
                        "Latest task state does not point to the latest successful run commit.",
                        work_item_id,
                    )
                )
            if expected_commit is not None and resolve_commit(repository, expected_commit) != target_commit:
                blockers.append(
                    RollbackBlocker(
                        "EXPECTED_COMMIT_MISMATCH",
                        "Expected commit does not match the recorded task commit.",
                        work_item_id,
                    )
                )
            if not commit_is_ancestor(repository, target_commit):
                blockers.append(
                    RollbackBlocker(
                        "TARGET_NOT_ANCESTOR",
                        "Recorded task commit is not an ancestor of HEAD.",
                        work_item_id,
                    )
                )
            if len(commit_parents(repository, target_commit)) != 1:
                blockers.append(
                    RollbackBlocker(
                        "UNSUPPORTED_COMMIT_SHAPE",
                        "Rollback supports only non-root, non-merge commits with exactly one parent.",
                        work_item_id,
                    )
                )
            changed_files = commit_changed_files(repository, target_commit)
            if not changed_files:
                blockers.append(
                    RollbackBlocker(
                        "TARGET_HAS_NO_CHANGES",
                        "Recorded commit has no changed files to revert.",
                        work_item_id,
                    )
                )
        except GitError as exc:
            blockers.append(RollbackBlocker("GIT_EVIDENCE_INVALID", str(exc), work_item_id))

    if not repository_is_clean(repository):
        blockers.append(
            RollbackBlocker(
                "CANONICAL_REPOSITORY_DIRTY",
                "Canonical repository must be clean before rollback.",
            )
        )

    active = [
        item
        for item in store.list_tasks()
        if item.status in {"running", "review_pending", "rework_required", "escalated"}
    ]
    for item in active:
        blockers.append(
            RollbackBlocker(
                "ACTIVE_TASK",
                f"Active or unresolved task blocks rollback: {item.work_item.id}={item.status}.",
                item.work_item.id,
            )
        )

    downstream = store.downstream_tasks(work_item_id)
    downstream_ids = tuple(item.work_item.id for item in downstream)
    blocking_downstream_statuses = {
        "done",
        "running",
        "review_pending",
        "rework_required",
        "escalated",
    }
    for item in downstream:
        if item.status in blocking_downstream_statuses:
            blockers.append(
                RollbackBlocker(
                    "DOWNSTREAM_TASK_BLOCKS_ROLLBACK",
                    f"Downstream task must be remediated first: {item.work_item.id}={item.status}.",
                    item.work_item.id,
                )
            )

    if target_commit is not None:
        prior = next(
            (
                item
                for item in store.rollback_records()
                if item.ok and item.target_commit == target_commit
            ),
            None,
        )
        if prior is not None:
            blockers.append(
                RollbackBlocker(
                    "ALREADY_ROLLED_BACK",
                    f"Commit was already rolled back by {prior.rollback_commit}.",
                    work_item_id,
                )
            )

    return RollbackPlan(
        work_item_id=work_item_id,
        run_id=run.run_id if run is not None else None,
        target_commit=target_commit,
        changed_files=changed_files,
        downstream_task_ids=downstream_ids,
        blockers=tuple(blockers),
    )


def apply_rollback(
    repository: Path,
    store: HohStateStore,
    work_item_id: str,
    expected_commit: str,
    reason: str,
    verification_commands: tuple[str, ...],
    timeout_seconds: float = 120.0,
    coordination: CoordinationConfig = CoordinationConfig(),
) -> RollbackRecord:
    lease = repository_execution_lease(repository, coordination)
    try:
        with lease.hold(
            f"rollback.apply:{work_item_id}",
            command=f"rollback task {work_item_id}",
        ):
            OperationLedger(repository).assert_no_blocking()
            return _apply_rollback_locked(
                repository,
                store,
                work_item_id,
                expected_commit,
                reason,
                verification_commands,
                timeout_seconds,
            )
    except LockContendedError as exc:
        raise RollbackError(f"Repository execution lease is busy: {exc}") from exc


def _apply_rollback_locked(
    repository: Path,
    store: HohStateStore,
    work_item_id: str,
    expected_commit: str,
    reason: str,
    verification_commands: tuple[str, ...],
    timeout_seconds: float,
) -> RollbackRecord:
    normalized_reason = _normalize_reason(reason)
    if timeout_seconds <= 0:
        raise RollbackError("Rollback timeout must be greater than zero.")
    if not verification_commands or any(not command.strip() for command in verification_commands):
        raise RollbackError("At least one non-empty post-rollback verification command is required.")

    try:
        expected_full = resolve_commit(repository, expected_commit)
    except GitError as exc:
        raise RollbackError(str(exc)) from exc
    prior = next(
        (
            item
            for item in store.rollback_records()
            if item.ok and item.work_item_id == work_item_id and item.target_commit == expected_full
        ),
        None,
    )
    if prior is not None:
        return prior

    plan = plan_rollback(repository, store, work_item_id, expected_full)
    if not plan.eligible or plan.target_commit is None or plan.run_id is None:
        details = "; ".join(f"{item.code}: {item.message}" for item in plan.blockers)
        raise RollbackError(details or "Rollback plan is not eligible.")

    work_item = WorkItem(
        id=f"rollback-{work_item_id}",
        title=f"Rollback {work_item_id}",
        objective=f"Revert exact HoH commit {plan.target_commit}.",
        acceptance_criteria=("Operator-provided rollback verification commands pass.",),
        verification_commands=verification_commands,
        allowed_paths=plan.changed_files,
        non_goals=("Do not modify files outside the target commit.",),
    )
    attempt = create_worktree_attempt(
        repository,
        repository.parent / ".hoh-rollback-attempts",
        work_item,
    )
    command_results: tuple[CommandResult, ...] = ()
    try:
        revert_commit_no_commit(attempt.path, plan.target_commit)
        command_results = run_verification_commands(
            attempt.path,
            verification_commands,
            timeout_seconds=timeout_seconds,
            policy=OperatorCommandPolicy(),
        )
        if not all(item.ok for item in command_results):
            error = "Rollback verification failed in isolated worktree."
            record = _rollback_record(plan, normalized_reason, False, command_results, error=error)
            store.record_rollback(record)
            raise RollbackError(error)
        patch = collect_attempt_patch(attempt, "supervisor-rollback", work_item)
        actual_changed_files = changed_files_from_patch(patch.patch)
        if set(actual_changed_files) != set(plan.changed_files):
            error = (
                "Rollback patch scope differs from the exact target commit: "
                f"expected={','.join(plan.changed_files)} actual={','.join(actual_changed_files)}"
            )
            record = _rollback_record(plan, normalized_reason, False, command_results, error=error)
            store.record_rollback(record)
            raise RollbackError(error)
    except (GitError, StateStoreError) as exc:
        record = _rollback_record(plan, normalized_reason, False, command_results, error=str(exc))
        store.record_rollback(record)
        raise RollbackError(str(exc)) from exc
    finally:
        remove_worktree_attempt(attempt)

    revalidated = plan_rollback(repository, store, work_item_id, plan.target_commit)
    if not revalidated.eligible:
        error = "Rollback preconditions changed after isolated verification: " + "; ".join(
            f"{item.code}: {item.message}" for item in revalidated.blockers
        )
        record = _rollback_record(plan, normalized_reason, False, command_results, error=error)
        store.record_rollback(record)
        raise RollbackError(error)

    canonical_applied = False
    committed = False
    operation_id = str(uuid4())
    ledger = OperationLedger(repository)
    ledger.begin_rollback(
        operation_id,
        state_root=store.root,
        task_id=work_item_id,
        run_id=plan.run_id,
        target_commit=plan.target_commit,
        patch=patch.patch,
        expected_changed_files=plan.changed_files,
        reason=normalized_reason,
    )
    crash_point("rollback.before_apply")
    try:
        apply_patch(repository, patch.patch)
        canonical_applied = True
        ledger.phase(operation_id, "patch_applied")
        crash_point("rollback.after_apply")
        canonical_results = run_verification_commands(
            repository,
            verification_commands,
            timeout_seconds=timeout_seconds,
            policy=OperatorCommandPolicy(),
        )
        command_results = canonical_results
        if not all(item.ok for item in canonical_results):
            restore_paths_to_head(repository, plan.changed_files)
            canonical_applied = False
            error = "Rollback verification failed after canonical patch application."
            record = _rollback_record(plan, normalized_reason, False, canonical_results, error=error)
            store.record_rollback(record)
            ledger.terminal(operation_id, "reverted", error)
            raise RollbackError(error)
        provisional = _rollback_record(
            plan,
            normalized_reason,
            True,
            canonical_results,
            rollback_id=operation_id,
        )
        ledger.phase(
            operation_id,
            "verified",
            rollback_record=rollback_to_json(provisional),
        )
        stage_paths(repository, plan.changed_files)
        short_commit = commit_staged(
            repository,
            (
                f"rollback({work_item_id}): revert {plan.target_commit[:12]} - {normalized_reason}"
                f"\n\nHoH-Operation: {operation_id}"
            ),
        )
        if short_commit is None:
            raise RollbackError("Rollback produced no commit.")
        committed = True
        rollback_commit = resolve_commit(repository, short_commit)
        crash_point("rollback.after_commit")
        record = _rollback_record(
            plan,
            normalized_reason,
            True,
            command_results,
            rollback_commit=rollback_commit,
            rollback_id=operation_id,
        )
        ledger.phase(
            operation_id,
            "commit_created",
            commit=rollback_commit,
            rollback_record=rollback_to_json(record),
        )
    except GitError as exc:
        if canonical_applied and not committed:
            try:
                restore_paths_to_head(repository, plan.changed_files)
            except GitError as restore_exc:
                exc = GitError(f"{exc}; canonical restore also failed: {restore_exc}")
        record = _rollback_record(plan, normalized_reason, False, command_results, error=str(exc))
        store.record_rollback(record)
        if canonical_applied and not committed:
            ledger.terminal(operation_id, "reverted", str(exc))
        raise RollbackError(str(exc)) from exc

    crash_point("rollback.before_state")
    stored = store.complete_rollback(record)
    ledger.phase(operation_id, "state_recorded")
    crash_point("rollback.after_state")
    ledger.terminal(operation_id, "complete", "Rollback commit and StateStore evidence are durable.")
    return stored


def _latest_successful_run(store: HohStateStore, work_item_id: str) -> RunRecord | None:
    return next(
        (
            run
            for run in reversed(store.history())
            if run.work_item_id == work_item_id and run.ok and run.commit is not None
        ),
        None,
    )


def _rollback_record(
    plan: RollbackPlan,
    reason: str,
    ok: bool,
    command_results: tuple[CommandResult, ...],
    *,
    rollback_commit: str | None = None,
    error: str | None = None,
    rollback_id: str | None = None,
) -> RollbackRecord:
    if plan.run_id is None or plan.target_commit is None:
        raise RollbackError("Cannot record rollback without exact run and commit evidence.")
    return RollbackRecord(
        rollback_id=rollback_id or str(uuid4()),
        created_at_utc=datetime.now(UTC).isoformat(),
        work_item_id=plan.work_item_id,
        run_id=plan.run_id,
        target_commit=plan.target_commit,
        rollback_commit=rollback_commit,
        reason=reason,
        ok=ok,
        changed_files=plan.changed_files,
        command_results=command_results,
        error=error,
    )


def _normalize_reason(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    if not normalized:
        raise RollbackError("Rollback reason must be non-empty.")
    if len(normalized) > 200:
        raise RollbackError("Rollback reason must be at most 200 characters.")
    return normalized
