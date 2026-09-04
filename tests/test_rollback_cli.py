import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from llm_harness.cli import main
from llm_harness.domain import HarnessRunResult, VerificationReport, WorkItem
from llm_harness.state import HohStateStore


class RollbackCliTests(unittest.TestCase):
    def test_plan_apply_and_history_json_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, state, target = self._project(root)

            plan_stdout = io.StringIO()
            with contextlib.redirect_stdout(plan_stdout):
                plan_code = main(
                    [
                        "rollback-plan",
                        "--project-root",
                        str(repository),
                        "--state-root",
                        str(state),
                        "--task-id",
                        "feature",
                        "--expected-commit",
                        target,
                        "--json",
                    ]
                )
            apply_stdout = io.StringIO()
            with contextlib.redirect_stdout(apply_stdout):
                apply_code = main(
                    [
                        "rollback-apply",
                        "--project-root",
                        str(repository),
                        "--state-root",
                        str(state),
                        "--task-id",
                        "feature",
                        "--expected-commit",
                        target,
                        "--reason",
                        "Production regression",
                        "--check",
                        (
                            f'"{sys.executable}" -c "from pathlib import Path; '
                            "assert not Path('feature.txt').exists()\""
                        ),
                        "--json",
                    ]
                )
            history_stdout = io.StringIO()
            with contextlib.redirect_stdout(history_stdout):
                history_code = main(
                    [
                        "rollback-history",
                        "--project-root",
                        str(repository),
                        "--state-root",
                        str(state),
                        "--json",
                    ]
                )
            handoff_stdout = io.StringIO()
            with contextlib.redirect_stdout(handoff_stdout):
                handoff_code = main(
                    [
                        "queue-run-loop",
                        "--project-root",
                        str(repository),
                        "--state-root",
                        str(state),
                        "--final-audit",
                        "--final-check",
                        "git status --short",
                        "--json",
                    ]
                )

            plan = json.loads(plan_stdout.getvalue())
            applied = json.loads(apply_stdout.getvalue())
            history = json.loads(history_stdout.getvalue())
            handoff = json.loads(handoff_stdout.getvalue())
            self.assertEqual(plan_code, 0, plan_stdout.getvalue())
            self.assertTrue(plan["data"]["eligible"])
            self.assertEqual(plan["message_type"], "rollback.plan")
            self.assertEqual(apply_code, 0, apply_stdout.getvalue())
            self.assertEqual(applied["data"]["status"], "applied")
            self.assertEqual(applied["data"]["rollback"]["target_commit"], plan["data"]["target_commit"])
            self.assertEqual(history_code, 0)
            self.assertEqual(history["message_type"], "rollback.history")
            self.assertEqual(len(history["data"]["rollbacks"]), 1)
            self.assertEqual(handoff_code, 1)
            self.assertEqual(handoff["data"]["status"], "rollback_blocked")
            self.assertEqual(self._git(repository, "status", "--porcelain"), "")

    def test_apply_rejects_wrong_exact_commit_without_git_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, state, _ = self._project(root)
            initial = self._git(repository, "rev-parse", "HEAD^")
            stdout = io.StringIO()
            head_before = self._git(repository, "rev-parse", "HEAD")

            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "rollback-apply",
                        "--project-root",
                        str(repository),
                        "--state-root",
                        str(state),
                        "--task-id",
                        "feature",
                        "--expected-commit",
                        initial,
                        "--reason",
                        "Wrong target",
                        "--check",
                        "git status --short",
                        "--json",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 1)
            self.assertIn("EXPECTED_COMMIT_MISMATCH", payload["error"])
            self.assertEqual(self._git(repository, "rev-parse", "HEAD"), head_before)
            self.assertEqual(self._git(repository, "status", "--porcelain"), "")

    def _project(self, root: Path) -> tuple[Path, Path, str]:
        repository = root / "repo"
        repository.mkdir()
        self._git(repository, "init")
        self._git(repository, "config", "user.email", "rollback@example.local")
        self._git(repository, "config", "user.name", "Rollback CLI Tests")
        (repository / "README.md").write_text("# Rollback CLI\n", encoding="utf-8")
        self._git(repository, "add", "README.md")
        self._git(repository, "commit", "-m", "Initial")
        (repository / "feature.txt").write_text("feature\n", encoding="utf-8")
        self._git(repository, "add", "feature.txt")
        self._git(repository, "commit", "-m", "feature: add feature")
        target = self._git(repository, "rev-parse", "--short", "HEAD")

        state = root / "state"
        store = HohStateStore(state)
        task = WorkItem(
            id="feature",
            title="Feature",
            objective="Create feature.txt.",
            acceptance_criteria=("feature.txt exists.",),
            verification_commands=("git status --short",),
            allowed_paths=("feature.txt",),
        )
        store.enqueue(task)
        store.mark_running(task.id)
        store.record_result(
            task.id,
            "2026-01-01T00:00:00+00:00",
            HarnessRunResult(
                repository=repository,
                work_item_id=task.id,
                commit=target,
                pre_apply=VerificationReport(ok=True),
                post_apply=VerificationReport(ok=True),
                command_results=(),
            ),
        )
        return repository, state, target

    def _git(self, repository: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            self.fail(completed.stderr)
        return completed.stdout.strip()


if __name__ == "__main__":
    unittest.main()
