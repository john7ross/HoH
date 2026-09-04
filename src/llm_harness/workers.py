from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .domain import WorkItem, WorkerPatch
from .jobs import WorkerCompletion, WorkerJob


class PatchWorker(Protocol):
    name: str

    def produce_patch(self, repository: Path, work_item: WorkItem) -> WorkerPatch:
        ...


class JobWorker(Protocol):
    name: str

    def run_job(self, repository: Path, job: WorkerJob) -> WorkerCompletion:
        ...


class StubPatchWorker:
    name = "stub-worker"

    def produce_patch(self, repository: Path, work_item: WorkItem) -> WorkerPatch:
        content = (
            "# Harness demo\n"
            "\n"
            f"Work item: {work_item.id}\n"
            f"Objective: {work_item.objective}\n"
        )
        patch = (
            "diff --git a/HARNESS_DEMO.md b/HARNESS_DEMO.md\n"
            "new file mode 100644\n"
            "index 0000000..9daeafb\n"
            "--- /dev/null\n"
            "+++ b/HARNESS_DEMO.md\n"
            "@@ -0,0 +1,4 @@\n"
            f"+{content.splitlines()[0]}\n"
            "+\n"
            f"+{content.splitlines()[2]}\n"
            f"+{content.splitlines()[3]}\n"
        )
        return WorkerPatch(
            worker_name=self.name,
            work_item_id=work_item.id,
            patch=patch,
            notes="Deterministic patch for demo and test execution.",
        )

    def run_job(self, repository: Path, job: WorkerJob) -> WorkerCompletion:
        return WorkerCompletion(
            job_id=job.id,
            callback_token=job.callback_token,
            patch=self.produce_patch(repository, job.work_item),
        )
