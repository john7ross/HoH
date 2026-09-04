from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from llm_harness.domain import WorkItem
from llm_harness.jobs import InMemoryJobStore, JobStatus
from llm_harness.supervisor import Supervisor
from llm_harness.targets import LocalAgentTarget
from llm_harness.verifier import PolicyVerifier
from llm_harness.workers import StubPatchWorker


class JobFlowTests(unittest.TestCase):
    def test_supervisor_dispatches_job_and_accepts_completion_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp)
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "test@example.local")
            self._git(repository, "config", "user.name", "Harness Test")
            (repository / ".gitignore").write_text(".tmp\n", encoding="utf-8")
            self._git(repository, "add", ".gitignore")
            self._git(repository, "commit", "-m", "Initial commit")

            work_item = WorkItem(
                id="job-1",
                title="Create demo artifact from callback",
                objective="Create deterministic artifact through job callback.",
                acceptance_criteria=("HARNESS_DEMO.md exists.",),
                verification_commands=(
                    f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'HARNESS_DEMO.md\').exists()"',
                ),
                allowed_paths=("HARNESS_DEMO.md",),
            )
            jobs = InMemoryJobStore()

            result = Supervisor(PolicyVerifier(), jobs=jobs).dispatch_work_item(
                repository,
                work_item,
                LocalAgentTarget(name="stub-agent", command="stub"),
                StubPatchWorker(),
            )

            self.assertTrue(result.ok)
            self.assertIsNotNone(result.commit)
            completion = jobs.completions()[0]
            self.assertEqual(jobs.get(completion.job_id).status, JobStatus.COMPLETED)

    def _git(self, repository: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)
        return completed.stdout


if __name__ == "__main__":
    unittest.main()
