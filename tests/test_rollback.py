from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import unittest

from llm_harness.coordination import CoordinationConfig
from llm_harness.domain import HarnessRunResult, VerificationReport, WorkItem
from llm_harness.rollback import RollbackError, apply_rollback, plan_rollback
from llm_harness.state import HohStateStore
from llm_harness.supervisor_protocol import build_supervisor_status


class RollbackPolicyTests(unittest.TestCase):
    def test_live_repository_lease_blocks_rollback_before_git_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, task, target = self._completed_task(root)
            ready = root / "ready"
            code = (
                "import sys,time\n"
                "from pathlib import Path\n"
                "from llm_harness.coordination import repository_execution_lease\n"
                "repo,ready=Path(sys.argv[1]),Path(sys.argv[2])\n"
                "lease=repository_execution_lease(repo)\n"
                "lease.acquire('test.hold',command='rollback contention holder')\n"
                "ready.write_text('ready',encoding='utf-8')\n"
                "time.sleep(30)\n"
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
            holder = subprocess.Popen(
                [sys.executable, "-c", code, str(repository), str(ready)],
                cwd=Path(__file__).parents[1],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not ready.exists():
                    if time.monotonic() >= deadline:
                        self.fail("Timed out waiting for repository lease holder.")
                    time.sleep(0.01)
                head_before = self._git(repository, "rev-parse", "HEAD")
                with self.assertRaisesRegex(RollbackError, "execution lease is busy"):
                    apply_rollback(
                        repository,
                        store,
                        task.id,
                        target,
                        "Must remain blocked",
                        ("git status --short",),
                        coordination=CoordinationConfig(
                            execution_lock_timeout_seconds=0.1,
                            poll_interval_seconds=0.01,
                        ),
                    )
                self.assertEqual(self._git(repository, "rev-parse", "HEAD"), head_before)
                self.assertEqual(store.rollback_records(), ())
            finally:
                holder.terminate()
                holder.communicate(timeout=5)

    def test_plan_and_apply_exact_recorded_commit_then_repeat_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, task, target = self._completed_task(root)
            check = (
                f'"{sys.executable}" -c "from pathlib import Path; '
                "assert not Path('feature.txt').exists()\"",
            )

            plan = plan_rollback(repository, store, task.id, target)
            record = apply_rollback(
                repository,
                store,
                task.id,
                target,
                "Production regression",
                check,
            )
            repeated = apply_rollback(
                repository,
                store,
                task.id,
                target,
                "Production regression",
                check,
            )

            self.assertTrue(plan.eligible, plan.blockers)
            self.assertEqual(plan.target_commit, self._git(repository, "rev-parse", target))
            self.assertEqual(plan.changed_files, ("feature.txt",))
            self.assertTrue(record.ok)
            self.assertEqual(repeated, record)
            self.assertEqual(store.latest_task(task.id).status, "rolled_back")
            self.assertEqual(store.rollback_records(), (record,))
            self.assertFalse((repository / "feature.txt").exists())
            self.assertEqual(self._git(repository, "status", "--porcelain"), "")
            self.assertEqual(self._git(repository, "rev-list", "--count", "HEAD"), "3")
            self.assertIn("rollback(feature)", self._git(repository, "log", "-1", "--format=%s"))

    def test_plan_blocks_dirty_repository_and_expected_commit_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, task, target = self._completed_task(root)
            initial = self._git(repository, "rev-parse", f"{target}^")
            (repository / "dirty.txt").write_text("dirty\n", encoding="utf-8")

            plan = plan_rollback(repository, store, task.id, initial)

            self.assertFalse(plan.eligible)
            self.assertEqual(
                {item.code for item in plan.blockers},
                {"EXPECTED_COMMIT_MISMATCH", "CANONICAL_REPOSITORY_DIRTY"},
            )

    def test_plan_blocks_completed_transitive_downstream_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, task, target = self._completed_task(root)
            downstream = WorkItem(
                id="dependent",
                title="Dependent",
                objective="Depend on feature.",
                acceptance_criteria=("dependent.txt exists.",),
                verification_commands=("git status --short",),
                allowed_paths=("dependent.txt",),
                depends_on=(task.id,),
            )
            (repository / "dependent.txt").write_text("dependent\n", encoding="utf-8")
            self._git(repository, "add", "dependent.txt")
            self._git(repository, "commit", "-m", "dependent: add dependent")
            dependent_commit = self._git(repository, "rev-parse", "--short", "HEAD")
            store.enqueue(downstream)
            store.mark_running(downstream.id)
            store.record_result(
                downstream.id,
                "2026-01-01T00:00:00+00:00",
                self._accepted_result(repository, downstream.id, dependent_commit),
            )

            plan = plan_rollback(repository, store, task.id, target)

            self.assertFalse(plan.eligible)
            blocker = next(
                item for item in plan.blockers if item.code == "DOWNSTREAM_TASK_BLOCKS_ROLLBACK"
            )
            self.assertEqual(blocker.task_id, downstream.id)
            self.assertEqual(plan.downstream_task_ids, (downstream.id,))

    def test_failed_isolated_verification_records_evidence_without_touching_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, task, target = self._completed_task(root)
            failing_check = (
                f'"{sys.executable}" -c "from pathlib import Path; '
                "assert Path('feature.txt').exists()\"",
            )
            head_before = self._git(repository, "rev-parse", "HEAD")

            with self.assertRaisesRegex(RollbackError, "isolated worktree"):
                apply_rollback(
                    repository,
                    store,
                    task.id,
                    target,
                    "Unsafe rollback check",
                    failing_check,
                )

            self.assertEqual(self._git(repository, "rev-parse", "HEAD"), head_before)
            self.assertEqual(self._git(repository, "status", "--porcelain"), "")
            self.assertTrue((repository / "feature.txt").exists())
            self.assertEqual(store.latest_task(task.id).status, "done")
            records = store.rollback_records()
            self.assertEqual(len(records), 1)
            self.assertFalse(records[0].ok)
            self.assertIsNone(records[0].rollback_commit)
            self.assertFalse(records[0].command_results[0].ok)

    def test_failed_canonical_verification_restores_only_target_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, task, target = self._completed_task(root)
            worktree_only_check = (
                f'"{sys.executable}" -c "from pathlib import Path; '
                "assert Path('.git').is_file()\"",
            )
            head_before = self._git(repository, "rev-parse", "HEAD")

            with self.assertRaisesRegex(RollbackError, "canonical patch"):
                apply_rollback(
                    repository,
                    store,
                    task.id,
                    target,
                    "Canonical verification guard",
                    worktree_only_check,
                )

            self.assertEqual(self._git(repository, "rev-parse", "HEAD"), head_before)
            self.assertEqual(self._git(repository, "status", "--porcelain"), "")
            self.assertTrue((repository / "feature.txt").exists())
            self.assertEqual(store.latest_task(task.id).status, "done")
            records = store.rollback_records()
            self.assertEqual(len(records), 1)
            self.assertFalse(records[0].ok)
            self.assertIn("canonical", records[0].error or "")

    def test_queued_downstream_remains_blocked_after_successful_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, task, target = self._completed_task(root)
            downstream = WorkItem(
                id="queued-dependent",
                title="Queued dependent",
                objective="Wait for feature.",
                acceptance_criteria=("Dependent completes.",),
                verification_commands=("git status --short",),
                allowed_paths=("dependent.txt",),
                depends_on=(task.id,),
            )
            store.enqueue(downstream)

            record = apply_rollback(
                repository,
                store,
                task.id,
                target,
                "Disable regressed feature",
                (
                    f'"{sys.executable}" -c "from pathlib import Path; '
                    "assert not Path('feature.txt').exists()\"",
                ),
            )
            schedule = store.schedule()

            self.assertTrue(record.ok)
            self.assertIsNone(schedule.selected)
            self.assertEqual(schedule.waiting[0].task.work_item.id, downstream.id)
            self.assertEqual(schedule.waiting[0].blockers[0].status, "rolled_back")

    def test_corrected_replacement_lifecycle_clears_rolled_back_status_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, task, target = self._completed_task(root)
            apply_rollback(
                repository,
                store,
                task.id,
                target,
                "Disable regressed feature",
                (
                    f'"{sys.executable}" -c "from pathlib import Path; '
                    "assert not Path('feature.txt').exists()\"",
                ),
            )
            (repository / "feature.txt").write_text("corrected\n", encoding="utf-8")
            self._git(repository, "add", "feature.txt")
            self._git(repository, "commit", "-m", "feature: corrected replacement")
            corrected_commit = self._git(repository, "rev-parse", "--short", "HEAD")
            store.enqueue(task, source="corrected replacement")
            store.mark_running(task.id)
            store.record_result(
                task.id,
                "2026-01-02T00:00:00+00:00",
                self._accepted_result(repository, task.id, corrected_commit),
            )

            report = build_supervisor_status(repository, store.root)

            self.assertEqual(report.rolled_back, 0)
            self.assertEqual(report.done, 1)
            self.assertTrue(report.ok)

    def _completed_task(self, root: Path) -> tuple[Path, HohStateStore, WorkItem, str]:
        repository = root / "repo"
        repository.mkdir()
        self._git(repository, "init")
        self._git(repository, "config", "user.email", "rollback@example.local")
        self._git(repository, "config", "user.name", "Rollback Tests")
        (repository / "README.md").write_text("# Rollback\n", encoding="utf-8")
        self._git(repository, "add", "README.md")
        self._git(repository, "commit", "-m", "Initial")

        task = WorkItem(
            id="feature",
            title="Feature",
            objective="Create feature.txt.",
            acceptance_criteria=("feature.txt exists.",),
            verification_commands=("git status --short",),
            allowed_paths=("feature.txt",),
        )
        (repository / "feature.txt").write_text("feature\n", encoding="utf-8")
        self._git(repository, "add", "feature.txt")
        self._git(repository, "commit", "-m", "feature: add feature")
        target = self._git(repository, "rev-parse", "--short", "HEAD")
        store = HohStateStore(root / "state")
        store.enqueue(task)
        store.mark_running(task.id)
        store.record_result(
            task.id,
            "2026-01-01T00:00:00+00:00",
            self._accepted_result(repository, task.id, target),
        )
        return repository, store, task, target

    def _accepted_result(
        self,
        repository: Path,
        task_id: str,
        commit: str,
    ) -> HarnessRunResult:
        return HarnessRunResult(
            repository=repository,
            work_item_id=task_id,
            commit=commit,
            pre_apply=VerificationReport(ok=True),
            post_apply=VerificationReport(ok=True),
            command_results=(),
        )

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
