from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from llm_harness.attempts import (
    AttemptJobWorker,
    collect_attempt_patch,
    create_worktree_attempt,
    remove_worktree_attempt,
    AttemptError,
)
from llm_harness.domain import WorkItem
from llm_harness.jobs import WorkerCompletion, WorkerJob
from llm_harness.supervisor import Supervisor
from llm_harness.targets import LocalAgentTarget
from llm_harness.verifier import PolicyVerifier


class DirectEditWorker:
    name = "direct-edit-worker"

    def run_job(self, repository: Path, job: WorkerJob) -> WorkerCompletion:
        (repository / "HARNESS_DEMO.md").write_text("worker edit\n", encoding="utf-8")
        return WorkerCompletion(
            job_id=job.id,
            callback_token=job.callback_token,
            patch=None,
        )


class CommittingWorker:
    name = "committing-worker"

    def run_job(self, repository: Path, job: WorkerJob) -> WorkerCompletion:
        (repository / "HARNESS_DEMO.md").write_text("worker commit\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "HARNESS_DEMO.md"], cwd=repository, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "worker-owned commit"],
            cwd=repository,
            check=True,
            capture_output=True,
        )
        return WorkerCompletion(job.id, job.callback_token, patch=None)


class WorktreeAttemptTests(unittest.TestCase):
    def test_collect_attempt_patch_from_isolated_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = self._create_repo(Path(tmp) / "repo")
            work_item = self._work_item()
            attempt = create_worktree_attempt(repository, Path(tmp) / "attempts", work_item)
            try:
                (attempt.path / "HARNESS_DEMO.md").write_text("attempt edit\n", encoding="utf-8")
                patch = collect_attempt_patch(attempt, "worker", work_item)
            finally:
                remove_worktree_attempt(attempt)

            self.assertIn("HARNESS_DEMO.md", patch.patch)
            self.assertFalse((repository / "HARNESS_DEMO.md").exists())
            self.assertFalse(attempt.path.exists())

    def test_collected_patch_survives_a_repository_that_stores_crlf(self):
        """A CRLF repository must produce a patch that still applies.

        Reading git in text mode rewrites every CRLF in the diff to LF, and the
        resulting patch then matches no line of the file it was taken from. That
        is invisible on a repository whose blobs are LF, so it only surfaced on a
        macOS checkout with core.autocrlf unset.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp) / "repo"
            repository.mkdir()
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "test@example.local")
            self._git(repository, "config", "user.name", "Harness Test")
            self._git(repository, "config", "core.autocrlf", "false")
            (repository / ".gitignore").write_text(".tmp\n", encoding="utf-8")
            self._git(repository, "add", ".gitignore")
            self._git(repository, "commit", "-m", "Initial commit")
            target = repository / "HARNESS_DEMO.md"
            target.write_bytes(b"first line\r\nsecond line\r\n")
            self._git(repository, "add", "HARNESS_DEMO.md")
            self._git(repository, "commit", "-m", "Seed CRLF content")

            work_item = self._work_item()
            attempt = create_worktree_attempt(repository, Path(tmp) / "attempts", work_item)
            try:
                (attempt.path / "HARNESS_DEMO.md").write_bytes(
                    b"first line\r\nsecond line\r\nthird line\r\n"
                )
                patch = collect_attempt_patch(attempt, "worker", work_item)
            finally:
                remove_worktree_attempt(attempt)

            self.assertIn("\r\n", patch.patch, "the diff lost the carriage returns it described")

            checked = subprocess.run(
                ["git", "apply", "--check"],
                cwd=repository,
                input=patch.patch.encode("utf-8"),
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                checked.returncode,
                0,
                checked.stderr.decode("utf-8", "replace"),
            )

    def test_supervisor_attempt_dispatch_commits_collected_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = self._create_repo(Path(tmp) / "repo")
            work_item = self._work_item()

            result = Supervisor(PolicyVerifier()).dispatch_work_item_in_attempt(
                repository,
                work_item,
                LocalAgentTarget(name="direct", command="direct"),
                DirectEditWorker(),
                attempts_root=Path(tmp) / "attempts",
            )

            self.assertTrue(result.ok)
            self.assertTrue((repository / "HARNESS_DEMO.md").exists())
            self.assertEqual(self._git(repository, "status", "--short"), "")
            self.assertEqual(tuple((Path(tmp) / "attempts").glob("*")), ())

    def test_attempt_worker_rejects_repository_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = self._create_repo(Path(tmp) / "canonical")
            other = self._create_repo(Path(tmp) / "other")
            work_item = self._work_item()
            job = WorkerJob(
                id="job-1",
                work_item=work_item,
                target=LocalAgentTarget(name="direct", command="direct"),
                callback_token="token-1",
            )
            worker = AttemptJobWorker(
                DirectEditWorker(),
                canonical_repository=canonical,
                attempts_root=Path(tmp) / "attempts",
            )

            completion = worker.run_job(other, job)

            self.assertIsNotNone(completion.error)
            self.assertIsNone(completion.patch)

    def test_attempt_worker_rejects_worker_owned_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = self._create_repo(Path(tmp) / "repo")
            work_item = self._work_item()
            job = WorkerJob(
                id="job-commit",
                work_item=work_item,
                target=LocalAgentTarget(name="commit", command="commit"),
                callback_token="token-commit",
            )
            worker = AttemptJobWorker(
                CommittingWorker(),
                canonical_repository=repository,
                attempts_root=Path(tmp) / "attempts",
            )

            completion = worker.run_job(repository, job)

            self.assertIn("changed Git HEAD", completion.error or "")
            self.assertIsNone(completion.patch)
            self.assertFalse((repository / "HARNESS_DEMO.md").exists())
            self.assertEqual(self._git(repository, "status", "--short"), "")

    def test_attempt_creation_requires_clean_canonical_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = self._create_repo(Path(tmp) / "repo")
            (repository / "dirty.txt").write_text("dirty\n", encoding="utf-8")

            with self.assertRaises(AttemptError):
                create_worktree_attempt(repository, Path(tmp) / "attempts", self._work_item())

    def test_attempt_branch_and_path_sanitize_work_item_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = self._create_repo(Path(tmp) / "repo")
            work_item = WorkItem(
                id="../unsafe task",
                title="Unsafe id",
                objective="Sanitize attempt identifiers.",
                acceptance_criteria=("Attempt identifiers are safe.",),
                verification_commands=("git status --short",),
            )
            attempt = create_worktree_attempt(repository, Path(tmp) / "attempts", work_item)
            try:
                self.assertIn("hoh/attempt/unsafe-task/", attempt.branch)
                self.assertEqual(attempt.path.parent, Path(tmp) / "attempts")
                self.assertTrue(attempt.path.name.startswith("unsafe-task-"))
            finally:
                remove_worktree_attempt(attempt)

    def _work_item(self) -> WorkItem:
        return WorkItem(
            id="attempt-task",
            title="Attempt task",
            objective="Collect direct worker edits from isolated worktree.",
            acceptance_criteria=("HARNESS_DEMO.md exists.",),
            verification_commands=(
                f'"{sys.executable}" -c "from pathlib import Path; assert Path(\'HARNESS_DEMO.md\').exists()"',
            ),
            allowed_paths=("HARNESS_DEMO.md",),
        )

    def _create_repo(self, path: Path) -> Path:
        path.mkdir()
        self._git(path, "init")
        self._git(path, "config", "user.email", "test@example.local")
        self._git(path, "config", "user.name", "Harness Test")
        (path / ".gitignore").write_text(".tmp\n", encoding="utf-8")
        self._git(path, "add", ".gitignore")
        self._git(path, "commit", "-m", "Initial commit")
        return path

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
