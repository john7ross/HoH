from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from llm_harness.domain import WorkItem, WorkerPatch
from llm_harness.supervisor import Supervisor
from llm_harness.verifier import PolicyVerifier
from llm_harness.workers import StubPatchWorker


class StaticPatchWorker:
    name = "static-worker"

    def __init__(self, patch: str) -> None:
        self.patch = patch

    def produce_patch(self, repository: Path, work_item: WorkItem) -> WorkerPatch:
        return WorkerPatch(
            worker_name=self.name,
            work_item_id=work_item.id,
            patch=self.patch,
        )


class SupervisorFlowTests(unittest.TestCase):
    def test_supervisor_applies_worker_patch_runs_checks_and_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp)
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "test@example.local")
            self._git(repository, "config", "user.name", "Harness Test")
            (repository / ".gitignore").write_text(".tmp\n", encoding="utf-8")
            self._git(repository, "add", ".gitignore")
            self._git(repository, "commit", "-m", "Initial commit")

            work_item = WorkItem(
                id="task-1",
                title="Create demo artifact",
                objective="Create a deterministic artifact through a worker patch.",
                acceptance_criteria=("HARNESS_DEMO.md exists.",),
                verification_commands=(
                    f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'HARNESS_DEMO.md\').exists()"',
                ),
                allowed_paths=("HARNESS_DEMO.md",),
            )

            result = Supervisor(PolicyVerifier()).execute_work_item(
                repository,
                work_item,
                StubPatchWorker(),
            )

            self.assertTrue(result.ok)
            self.assertIsNotNone(result.commit)
            self.assertTrue((repository / "HARNESS_DEMO.md").exists())
            log = self._git(repository, "log", "--oneline", "-1")
            self.assertIn("task-1: Create demo artifact", log)

    def test_supervisor_commits_only_worker_patch_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp)
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "test@example.local")
            self._git(repository, "config", "user.name", "Harness Test")
            (repository / ".gitignore").write_text(".tmp\n", encoding="utf-8")
            self._git(repository, "add", ".gitignore")
            self._git(repository, "commit", "-m", "Initial commit")
            (repository / "UNRELATED.txt").write_text("do not commit\n", encoding="utf-8")

            patch = (
                "diff --git a/HARNESS_DEMO.md b/HARNESS_DEMO.md\n"
                "new file mode 100644\n"
                "index 0000000..9daeafb\n"
                "--- /dev/null\n"
                "+++ b/HARNESS_DEMO.md\n"
                "@@ -0,0 +1 @@\n"
                "+worker change\n"
            )
            work_item = WorkItem(
                id="task-2",
                title="Commit only scoped patch",
                objective="Do not stage unrelated files.",
                acceptance_criteria=("HARNESS_DEMO.md exists.",),
                verification_commands=(
                    f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'HARNESS_DEMO.md\').exists()"',
                ),
                allowed_paths=("HARNESS_DEMO.md",),
            )

            result = Supervisor(PolicyVerifier()).execute_work_item(
                repository,
                work_item,
                StaticPatchWorker(patch),
            )

            self.assertTrue(result.ok)
            committed_files = self._git(repository, "show", "--name-only", "--format=", "HEAD").splitlines()
            self.assertEqual(committed_files, ["HARNESS_DEMO.md"])
            self.assertIn("?? UNRELATED.txt", self._git(repository, "status", "--short"))

    def test_supervisor_reverts_patch_when_verification_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp)
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "test@example.local")
            self._git(repository, "config", "user.name", "Harness Test")
            (repository / ".gitignore").write_text(".tmp\n", encoding="utf-8")
            self._git(repository, "add", ".gitignore")
            self._git(repository, "commit", "-m", "Initial commit")

            patch = (
                "diff --git a/HARNESS_DEMO.md b/HARNESS_DEMO.md\n"
                "new file mode 100644\n"
                "index 0000000..9daeafb\n"
                "--- /dev/null\n"
                "+++ b/HARNESS_DEMO.md\n"
                "@@ -0,0 +1 @@\n"
                "+worker change\n"
            )
            work_item = WorkItem(
                id="task-3",
                title="Rollback failed verification",
                objective="Patch should not remain after failed checks.",
                acceptance_criteria=("Failed verification leaves clean worktree.",),
                verification_commands=(f'"{sys.executable}" -c "raise SystemExit(7)"',),
                allowed_paths=("HARNESS_DEMO.md",),
            )

            result = Supervisor(PolicyVerifier()).execute_work_item(
                repository,
                work_item,
                StaticPatchWorker(patch),
            )

            self.assertFalse(result.ok)
            self.assertFalse((repository / "HARNESS_DEMO.md").exists())
            self.assertEqual(self._git(repository, "status", "--short"), "")

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
