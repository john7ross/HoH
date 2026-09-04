from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
from threading import Lock
from uuid import uuid4

from .domain import WorkerPatch, WorkItem
from .git_ops import decode_process_output
from .jobs import WorkerCompletion, WorkerJob
from .workers import JobWorker


class AttemptError(RuntimeError):
    pass


_WORKTREE_ADMIN_LOCK = Lock()


@dataclass(frozen=True)
class WorktreeAttempt:
    repository: Path
    path: Path
    branch: str
    base_commit: str


def create_worktree_attempt(
    repository: Path,
    attempts_root: Path,
    work_item: WorkItem,
) -> WorktreeAttempt:
    ensure_clean = _git(repository, "status", "--porcelain")
    if ensure_clean.stdout.strip():
        # Name the paths: verification commands routinely leave build output such as
        # __pycache__ in the canonical repository, and "must be clean" alone does not
        # tell the operator what to gitignore or remove.
        dirty = tuple(line.strip() for line in ensure_clean.stdout.splitlines() if line.strip())
        shown = ", ".join(dirty[:10]) + (f" (+{len(dirty) - 10} more)" if len(dirty) > 10 else "")
        raise AttemptError(
            "Canonical repository must be clean before creating a worker attempt. "
            f"Uncommitted or untracked paths: {shown}"
        )

    attempts_root.mkdir(parents=True, exist_ok=True)
    base = _git(repository, "rev-parse", "HEAD")
    if base.returncode != 0 or not base.stdout.strip():
        raise AttemptError(base.stderr.strip() or "git rev-parse HEAD failed")
    suffix = uuid4().hex[:12]
    safe_id = _safe_ref_component(work_item.id)
    branch = f"hoh/attempt/{safe_id}/{suffix}"
    path = attempts_root / f"{safe_id}-{suffix}"

    with _WORKTREE_ADMIN_LOCK:
        created = _git(repository, "worktree", "add", "-b", branch, str(path), "HEAD")
    if created.returncode != 0:
        raise AttemptError(created.stderr.strip() or "git worktree add failed")

    return WorktreeAttempt(
        repository=repository,
        path=path,
        branch=branch,
        base_commit=base.stdout.strip(),
    )


def collect_attempt_patch(attempt: WorktreeAttempt, worker_name: str, work_item: WorkItem) -> WorkerPatch:
    staged = _git(attempt.path, "add", "-A")
    if staged.returncode != 0:
        raise AttemptError(staged.stderr.strip() or "git add -A failed in attempt worktree")

    diff = _git(attempt.path, "diff", "--cached", "--binary")
    if diff.returncode != 0:
        raise AttemptError(diff.stderr.strip() or "git diff failed")
    return WorkerPatch(
        worker_name=worker_name,
        work_item_id=work_item.id,
        patch=diff.stdout,
        notes=f"Collected from isolated worktree {attempt.path}",
    )


def remove_worktree_attempt(attempt: WorktreeAttempt, delete_branch: bool = True) -> None:
    with _WORKTREE_ADMIN_LOCK:
        removed = _git(attempt.repository, "worktree", "remove", "--force", str(attempt.path))
        if removed.returncode != 0 and attempt.path.exists():
            shutil.rmtree(attempt.path, ignore_errors=True)
        if delete_branch:
            _git(attempt.repository, "branch", "-D", attempt.branch)


class AttemptJobWorker:
    def __init__(
        self,
        inner: JobWorker,
        canonical_repository: Path,
        attempts_root: Path | None = None,
        cleanup: bool = True,
    ) -> None:
        self.inner = inner
        self.canonical_repository = canonical_repository
        self.attempts_root = attempts_root or (canonical_repository.parent / ".hoh-attempts")
        self.cleanup = cleanup
        self.name = f"attempt-{inner.name}"

    def run_job(self, repository: Path, job: WorkerJob) -> WorkerCompletion:
        if repository.resolve() != self.canonical_repository.resolve():
            return WorkerCompletion(
                job_id=job.id,
                callback_token=job.callback_token,
                patch=None,
                error="Attempt worker repository does not match canonical repository.",
            )

        attempt = create_worktree_attempt(
            self.canonical_repository,
            self.attempts_root,
            job.work_item,
        )
        try:
            inner_job = WorkerJob(
                id=job.id,
                work_item=job.work_item,
                target=job.target,
                callback_token=job.callback_token,
                status=job.status,
                created_at_utc=job.created_at_utc,
            )
            completion = self.inner.run_job(attempt.path, inner_job)
            if completion.error:
                return completion
            head = _git(attempt.path, "rev-parse", "HEAD")
            if head.returncode != 0 or head.stdout.strip() != attempt.base_commit:
                return WorkerCompletion(
                    job_id=job.id,
                    callback_token=job.callback_token,
                    patch=None,
                    error=(
                        "Worker changed Git HEAD inside the isolated attempt. "
                        "Workers may edit files but must not commit; HoH owns all commits."
                    ),
                )
            patch = collect_attempt_patch(attempt, self.inner.name, job.work_item)
            return WorkerCompletion(
                job_id=job.id,
                callback_token=job.callback_token,
                patch=patch,
            )
        finally:
            if self.cleanup:
                remove_worktree_attempt(attempt)


def _git(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Read git in binary and decode by hand.

    Text mode applies universal-newline translation, so every CRLF in
    "git diff --cached" arrives as LF. In a repository that stores CRLF content
    the collected patch then matches no line of the file it came from, and the
    task dies later with "patch does not apply".
    """
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        capture_output=True,
        check=False,
    )
    return subprocess.CompletedProcess(
        completed.args,
        completed.returncode,
        decode_process_output(completed.stdout),
        decode_process_output(completed.stderr),
    )


def _safe_ref_component(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return safe or "work-item"
