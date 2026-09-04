from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from .domain import WorkerPatch, WorkItem
from .targets import ExecutionTarget


class JobStatus(StrEnum):
    CREATED = "created"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class WorkerJob:
    id: str
    work_item: WorkItem
    target: ExecutionTarget
    callback_token: str
    status: JobStatus = JobStatus.CREATED
    created_at_utc: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class WorkerCompletion:
    job_id: str
    callback_token: str
    patch: WorkerPatch | None
    error: str | None = None
    error_kind: str | None = None
    retryable: bool = False


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, WorkerJob] = {}
        self._completions: dict[str, WorkerCompletion] = {}

    def create(self, work_item: WorkItem, target: ExecutionTarget) -> WorkerJob:
        job = WorkerJob(
            id=str(uuid4()),
            work_item=work_item,
            target=target,
            callback_token=str(uuid4()),
        )
        self._jobs[job.id] = job
        return job

    def mark_dispatched(self, job_id: str) -> WorkerJob:
        job = self.get(job_id)
        dispatched = WorkerJob(
            id=job.id,
            work_item=job.work_item,
            target=job.target,
            callback_token=job.callback_token,
            status=JobStatus.DISPATCHED,
            created_at_utc=job.created_at_utc,
        )
        self._jobs[job_id] = dispatched
        return dispatched

    def complete(self, completion: WorkerCompletion) -> WorkerJob:
        job = self.get(completion.job_id)
        if completion.callback_token != job.callback_token:
            raise ValueError("Invalid callback token.")
        status = JobStatus.FAILED if completion.error else JobStatus.COMPLETED
        completed = WorkerJob(
            id=job.id,
            work_item=job.work_item,
            target=job.target,
            callback_token=job.callback_token,
            status=status,
            created_at_utc=job.created_at_utc,
        )
        self._jobs[job.id] = completed
        self._completions[job.id] = completion
        return completed

    def get(self, job_id: str) -> WorkerJob:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise KeyError(f"Unknown worker job: {job_id}") from exc

    def completion_for(self, job_id: str) -> WorkerCompletion | None:
        return self._completions.get(job_id)

    def completions(self) -> tuple[WorkerCompletion, ...]:
        return tuple(self._completions.values())
