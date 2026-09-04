from __future__ import annotations

from pathlib import Path

from .coordination import CoordinationConfig, repository_execution_lease
from .domain import HarnessRunResult, SemanticVerificationReport, WorkItem
from .command_policy import CommandPolicy
from .git_ops import apply_patch, commit_staged, ensure_git_repository, revert_patch, run_verification_commands, stage_paths
from .jobs import InMemoryJobStore, WorkerCompletion
from .journal import InteractionJournal
from .targets import ExecutionTarget
from .telegram import Notifier, StubTelegramNotifier
from .verifier import PolicyVerifier, changed_files_from_patch, documentation_paths
from .model_providers import ModelProviderError
from .model_runtime import ModelSemanticVerifier
from .operations import OperationContext, OperationLedger, crash_point, result_to_json
from .workers import JobWorker, PatchWorker


class Supervisor:
    def __init__(
        self,
        verifier: PolicyVerifier,
        notifier: Notifier | None = None,
        jobs: InMemoryJobStore | None = None,
        journal: InteractionJournal | None = None,
        semantic_verifier: ModelSemanticVerifier | None = None,
        coordination: CoordinationConfig = CoordinationConfig(),
        command_policy: CommandPolicy | None = None,
    ) -> None:
        self.verifier = verifier
        self.notifier = notifier or StubTelegramNotifier()
        self.jobs = jobs or InMemoryJobStore()
        self.journal = journal
        self.semantic_verifier = semantic_verifier
        self.coordination = coordination
        self.command_policy = command_policy or CommandPolicy()

    def execute_work_item(
        self,
        repository: Path,
        work_item: WorkItem,
        worker: PatchWorker,
    ) -> HarnessRunResult:
        ensure_git_repository(repository)

        worker_patch = worker.produce_patch(repository, work_item)
        return self.execute_worker_completion(
            repository,
            work_item,
            WorkerCompletion(
                job_id="sync",
                callback_token="sync",
                patch=worker_patch,
            ),
        )

    def dispatch_work_item(
        self,
        repository: Path,
        work_item: WorkItem,
        target: ExecutionTarget,
        worker: JobWorker,
        operation_context: OperationContext | None = None,
    ) -> HarnessRunResult:
        ensure_git_repository(repository)
        job = self.jobs.create(work_item, target)
        dispatched = self.jobs.mark_dispatched(job.id)
        self._record(
            "interaction", "supervisor", "worker.task_dispatched", recipient=worker.name,
            content={
                "repository": str(repository),
                "target": {"name": target.name, "type": str(target.target_type)},
                "task": _work_item_trace(work_item),
            },
            task_id=work_item.id, correlation_id=job.id,
        )
        completion = worker.run_job(repository, dispatched)
        completed = self.jobs.complete(completion)
        if completed.status.value == "failed":
            self._record(
                "tool_result",
                worker.name,
                "worker.failed",
                recipient="supervisor",
                content={
                    "error": completion.error,
                    "error_kind": completion.error_kind,
                    "retryable": completion.retryable,
                },
                task_id=work_item.id,
                correlation_id=job.id,
            )
            self.notifier.notify_user_action_required(completion.error or "Worker job failed.")
            return HarnessRunResult(
                repository=repository,
                work_item_id=work_item.id,
                commit=None,
                pre_apply=self.verifier.verify_after_apply(work_item, False, False),
                post_apply=self.verifier.verify_after_apply(work_item, False, False),
                command_results=(),
            )
        return self.execute_worker_completion(
            repository, work_item, completion, operation_context=operation_context
        )

    def collect_worker_completion(
        self,
        repository: Path,
        work_item: WorkItem,
        target: ExecutionTarget,
        worker: JobWorker,
    ) -> WorkerCompletion:
        ensure_git_repository(repository)
        job = self.jobs.create(work_item, target)
        dispatched = self.jobs.mark_dispatched(job.id)
        self._record(
            "interaction", "supervisor", "worker.task_dispatched", recipient=worker.name,
            content={
                "repository": str(repository),
                "target": {"name": target.name, "type": str(target.target_type)},
                "task": _work_item_trace(work_item),
            },
            task_id=work_item.id, correlation_id=job.id,
        )
        completion = worker.run_job(repository, dispatched)
        self.jobs.complete(completion)
        return completion

    def collect_worker_completion_in_attempt(
        self,
        repository: Path,
        work_item: WorkItem,
        target: ExecutionTarget,
        worker: JobWorker,
        attempts_root: Path | None = None,
    ) -> WorkerCompletion:
        from .attempts import AttemptJobWorker

        return self.collect_worker_completion(
            repository,
            work_item,
            target,
            AttemptJobWorker(worker, repository, attempts_root),
        )

    def dispatch_work_item_in_attempt(
        self,
        repository: Path,
        work_item: WorkItem,
        target: ExecutionTarget,
        worker: JobWorker,
        attempts_root: Path | None = None,
        operation_context: OperationContext | None = None,
    ) -> HarnessRunResult:
        from .attempts import AttemptJobWorker

        return self.dispatch_work_item(
            repository,
            work_item,
            target,
            AttemptJobWorker(worker, repository, attempts_root),
            operation_context=operation_context,
        )

    def execute_worker_completion(
        self,
        repository: Path,
        work_item: WorkItem,
        completion: WorkerCompletion,
        operation_context: OperationContext | None = None,
    ) -> HarnessRunResult:
        lease = repository_execution_lease(repository, self.coordination)
        with lease.hold(
            f"supervisor.execute_worker_completion:{work_item.id}",
            command=f"supervisor task {work_item.id}",
        ):
            ledger = OperationLedger(repository)
            ledger.assert_no_blocking()
            return self._execute_worker_completion_locked(
                repository, work_item, completion, ledger, operation_context
            )

    def _execute_worker_completion_locked(
        self,
        repository: Path,
        work_item: WorkItem,
        completion: WorkerCompletion,
        ledger: OperationLedger,
        operation_context: OperationContext | None,
    ) -> HarnessRunResult:
        if completion.patch is None:
            self.notifier.notify_user_action_required(completion.error or "Worker returned no patch.")
            failed = self.verifier.verify_after_apply(work_item, False, False)
            return HarnessRunResult(
                repository=repository,
                work_item_id=work_item.id,
                commit=None,
                pre_apply=failed,
                post_apply=failed,
                command_results=(),
            )

        worker_patch = completion.patch
        self._record(
            "artifact", worker_patch.worker_name, "worker.patch_returned", recipient="supervisor",
            content={"patch": worker_patch.patch, "notes": worker_patch.notes},
            task_id=work_item.id, correlation_id=completion.job_id,
        )
        changed_files = changed_files_from_patch(worker_patch.patch)
        pre_apply = self.verifier.verify_patch_before_apply(work_item, worker_patch)
        self._record(
            "decision", "deterministic-verifier", "patch.policy_checked", recipient="supervisor",
            content={"ok": pre_apply.ok, "findings": pre_apply.findings, "changed_files": changed_files},
            task_id=work_item.id, correlation_id=completion.job_id,
        )
        if not pre_apply.ok:
            self.notifier.notify_user_action_required(
                "Verifier rejected worker patch before apply: " + "; ".join(pre_apply.findings)
            )
            return HarnessRunResult(
                repository=repository,
                work_item_id=work_item.id,
                commit=None,
                pre_apply=pre_apply,
                post_apply=self.verifier.verify_after_apply(work_item, False, False),
                command_results=(),
            )

        if operation_context is not None:
            ledger.begin_task(
                operation_context,
                task_id=work_item.id,
                patch=worker_patch.patch,
                expected_changed_files=changed_files,
            )
            crash_point("task.before_apply")
        apply_patch(repository, worker_patch.patch)
        if operation_context is not None:
            ledger.phase(operation_context.operation_id, "patch_applied")
            crash_point("task.after_apply")
        self._record(
            "tool_call", "supervisor", "git.apply", content={"changed_files": changed_files},
            task_id=work_item.id, correlation_id=completion.job_id,
        )
        command_results = run_verification_commands(
            repository, work_item.verification_commands, policy=self.command_policy
        )
        for command_result in command_results:
            self._record(
                "tool_result", "supervisor", "verification.command", recipient="deterministic-verifier",
                content={
                    "command": command_result.command,
                    "return_code": command_result.return_code,
                    "stdout": command_result.stdout,
                    "stderr": command_result.stderr,
                },
                task_id=work_item.id, correlation_id=completion.job_id,
            )
        commands_ok = all(result.ok for result in command_results)
        # Measured from the patch itself. Searching the acceptance criteria for the word
        # "documentation" only reported what the task asked for, never what happened.
        documentation_updated = bool(documentation_paths(changed_files))
        post_apply = self.verifier.verify_after_apply(work_item, commands_ok, documentation_updated)

        if not post_apply.ok:
            revert_patch(repository, worker_patch.patch)
            if operation_context is not None:
                ledger.terminal(
                    operation_context.operation_id,
                    "reverted",
                    "Deterministic verification rejected the applied patch.",
                )
            self.notifier.notify_user_action_required(
                "Verifier rejected applied result: " + "; ".join(post_apply.findings)
            )
            return HarnessRunResult(
                repository=repository,
                work_item_id=work_item.id,
                commit=None,
                pre_apply=pre_apply,
                post_apply=post_apply,
                command_results=command_results,
            )

        semantic_verification = None
        if self.semantic_verifier is not None:
            try:
                semantic_verification = self.semantic_verifier.verify(
                    work_item,
                    worker_patch,
                    command_results,
                )
            except (ModelProviderError, ValueError) as exc:
                semantic_verification = SemanticVerificationReport(
                    decision="fail",
                    summary="Semantic verification could not produce trustworthy evidence.",
                    findings=(str(exc),),
                )
            self._record(
                "decision",
                "semantic-verifier",
                "patch.semantic_checked",
                recipient="supervisor",
                content=_semantic_trace(semantic_verification),
                task_id=work_item.id,
                correlation_id=completion.job_id,
            )
            if not semantic_verification.ok:
                revert_patch(repository, worker_patch.patch)
                if operation_context is not None:
                    ledger.terminal(
                        operation_context.operation_id,
                        "reverted",
                        "Semantic verification rejected the applied patch.",
                    )
                self.notifier.notify_user_action_required(
                    "Semantic verifier blocked the applied result: "
                    + semantic_verification.summary
                )
                return HarnessRunResult(
                    repository=repository,
                    work_item_id=work_item.id,
                    commit=None,
                    pre_apply=pre_apply,
                    post_apply=post_apply,
                    command_results=command_results,
                    semantic_verification=semantic_verification,
                )

        provisional = HarnessRunResult(
            repository=repository,
            work_item_id=work_item.id,
            commit=None,
            pre_apply=pre_apply,
            post_apply=post_apply,
            command_results=command_results,
            semantic_verification=semantic_verification,
        )
        if operation_context is not None:
            ledger.phase(
                operation_context.operation_id,
                "verified",
                result=result_to_json(provisional),
            )
        stage_paths(repository, changed_files)
        message = f"{work_item.id}: {work_item.title}"
        if operation_context is not None:
            message += f"\n\nHoH-Operation: {operation_context.operation_id}"
        commit = commit_staged(repository, message)
        if operation_context is not None:
            crash_point("task.after_commit")
            full_commit = None
            if commit is not None:
                from .git_ops import resolve_commit

                full_commit = resolve_commit(repository, commit)
            ledger.phase(
                operation_context.operation_id,
                "commit_created",
                commit=full_commit,
            )
        self._record(
            "tool_result", "supervisor", "git.commit", content={"commit": commit, "changed_files": changed_files},
            task_id=work_item.id, correlation_id=completion.job_id,
        )
        result = HarnessRunResult(
            repository=repository,
            work_item_id=work_item.id,
            commit=commit,
            pre_apply=pre_apply,
            post_apply=post_apply,
            command_results=command_results,
            semantic_verification=semantic_verification,
        )
        return result

    def _record(self, event_type, actor, action, **kwargs) -> None:
        if self.journal is not None:
            self.journal.record(event_type, actor, action, **kwargs)


def _work_item_trace(work_item: WorkItem) -> dict[str, object]:
    return {
        "id": work_item.id,
        "title": work_item.title,
        "objective": work_item.objective,
        "acceptance_criteria": list(work_item.acceptance_criteria),
        "verification_commands": list(work_item.verification_commands),
        "allowed_paths": list(work_item.allowed_paths),
        "non_goals": list(work_item.non_goals),
        "correction_instructions": list(work_item.correction_instructions),
        "correction_decision_id": work_item.correction_decision_id,
    }


def _semantic_trace(report: SemanticVerificationReport) -> dict[str, object]:
    evidence = report.evidence
    return {
        "decision": report.decision,
        "summary": report.summary,
        "findings": list(report.findings),
        "evidence": (
            {
                "provider": evidence.provider,
                "model": evidence.model,
                "endpoint": evidence.endpoint,
                "request_id": evidence.request_id,
                "attempts": evidence.attempts,
                "latency_ms": evidence.latency_ms,
                "input_tokens": evidence.input_tokens,
                "output_tokens": evidence.output_tokens,
                "total_tokens": evidence.total_tokens,
                "request_sha256": evidence.request_sha256,
                "response_sha256": evidence.response_sha256,
            }
            if evidence is not None
            else None
        ),
    }
