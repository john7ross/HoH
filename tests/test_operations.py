from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from llm_harness.domain import CommandResult, HarnessRunResult, VerificationReport, WorkItem
from llm_harness.operations import (
    OperationContext,
    OperationLedger,
    ReconciliationBlockedError,
    _patch_file_evidence,
    result_to_json,
)
from llm_harness.state import HohStateStore


class OperationReconciliationTests(unittest.TestCase):
    def test_patch_evidence_reads_an_lf_patch_on_every_platform(self) -> None:
        """Text-mode stdin rewrites the patch's newlines on Windows, so no hunk matches."""
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp)
            target = repository / "src" / "module.py"
            target.parent.mkdir(parents=True)
            target.write_bytes("\n".join(("alpha", "beta", "gamma", "")).encode("utf-8"))
            patch = "\n".join(
                (
                    "diff --git a/src/module.py b/src/module.py",
                    "--- a/src/module.py",
                    "+++ b/src/module.py",
                    "@@ -1,3 +1,3 @@",
                    " alpha",
                    "-beta",
                    "+BETA",
                    " gamma",
                    "",
                )
            )

            baseline, expected = _patch_file_evidence(repository, patch, ("src/module.py",))

            self.assertIsNotNone(baseline["src/module.py"])
            self.assertIsNotNone(expected["src/module.py"])
            self.assertNotEqual(
                baseline["src/module.py"]["sha256"],
                expected["src/module.py"]["sha256"],
            )

    def test_ledger_omits_raw_patch_and_redacts_recovery_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, store = self._queued_project(Path(tmp), "redaction")
            patch = (
                "diff --git a/secret.txt b/secret.txt\n"
                "new file mode 100644\n"
                "index 0000000..cb281c6\n"
                "--- /dev/null\n"
                "+++ b/secret.txt\n"
                "@@ -0,0 +1 @@\n"
                "+HOH_API_KEY=plain-secret\n"
            )
            context = OperationContext.create(
                store.root,
                "2026-01-01T00:00:00+00:00",
            )
            record = OperationLedger(repository).begin_task(
                context,
                task_id="crash-task",
                patch=patch,
                expected_changed_files=("secret.txt",),
            )
            result_payload = result_to_json(
                HarnessRunResult(
                    repository=repository,
                    work_item_id="crash-task",
                    commit=None,
                    pre_apply=VerificationReport(
                        ok=True,
                        findings=("Bearer abc.def.ghi",),
                    ),
                    post_apply=VerificationReport(ok=True),
                    command_results=(
                        CommandResult(
                            command="verify",
                            return_code=0,
                            stdout="HOH_TOKEN=plain-secret",
                            stderr="",
                        ),
                    ),
                )
            )

            serialized_record = json.dumps(record)
            serialized_result = json.dumps(result_payload)
            self.assertNotIn("patch_text", record)
            self.assertNotIn("plain-secret", serialized_record)
            self.assertNotIn("plain-secret", serialized_result)
            self.assertIn("<redacted>", serialized_result)

    def test_crash_before_apply_is_classified_and_guardedly_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, store = self._queued_project(Path(tmp), "before-apply")

            completed = self._crash_queue(repository, store, "task.before_apply")
            report = OperationLedger(repository).reconcile()
            item = report.blocking[0]

            self.assertEqual(completed.returncode, 91)
            self.assertEqual(item.classification, "not_started")
            self.assertEqual(self._git(repository, "status", "--porcelain"), "")
            resolved = OperationLedger(repository).guarded_resolve(
                item.operation_id, "cancel-intent"
            )
            self.assertTrue(resolved.terminal)

    def test_crash_after_apply_blocks_worker_until_exact_patch_restore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, store = self._queued_project(Path(tmp), "after-apply")

            completed = self._crash_queue(repository, store, "task.after_apply")
            ledger = OperationLedger(repository)
            item = ledger.reconcile().blocking[0]
            blocked = self._run_queue(repository, store)

            self.assertEqual(completed.returncode, 91)
            self.assertEqual(item.classification, "patch_applied")
            self.assertNotEqual(self._git(repository, "status", "--porcelain"), "")
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("reconciliation_blocked", blocked.stdout)
            exact_content = (repository / "HARNESS_DEMO.md").read_bytes()
            (repository / "HARNESS_DEMO.md").write_bytes(
                exact_content + b"unexpected\n"
            )
            self.assertEqual(
                ledger.reconcile().blocking[0].classification,
                "contradictory",
            )
            with self.assertRaises(ReconciliationBlockedError):
                ledger.guarded_resolve(item.operation_id, "restore-patch")
            self.assertIn(
                "unexpected",
                (repository / "HARNESS_DEMO.md").read_text(encoding="utf-8"),
            )
            (repository / "HARNESS_DEMO.md").write_bytes(exact_content)
            resolved = ledger.guarded_resolve(item.operation_id, "restore-patch")
            self.assertTrue(resolved.terminal)
            self.assertEqual(self._git(repository, "status", "--porcelain"), "")

    def test_crash_after_commit_auto_completes_state_without_rerunning_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, store = self._queued_project(Path(tmp), "after-commit")

            completed = self._crash_queue(repository, store, "task.after_commit")
            head_after_crash = self._git(repository, "rev-parse", "HEAD")
            report = OperationLedger(repository).reconcile()
            recoveries = [
                self._start_queue(repository, store),
                self._start_queue(repository, store),
            ]
            outputs = [process.communicate(timeout=30) for process in recoveries]

            self.assertEqual(completed.returncode, 91)
            self.assertEqual(
                report.blocking[0].classification,
                "commit_exists_state_missing",
            )
            self.assertTrue(
                all('"status": "empty"' in stdout for stdout, _ in outputs),
                outputs,
            )
            self.assertEqual(self._git(repository, "rev-parse", "HEAD"), head_after_crash)
            self.assertEqual(len(store.history()), 1)
            self.assertEqual(store.latest_task("crash-task").status, "done")
            self.assertTrue(OperationLedger(repository).reconcile().ok)

    def test_crash_before_and_after_state_are_idempotently_finished(self) -> None:
        for point in ("task.before_state", "task.after_state"):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as tmp:
                repository, store = self._queued_project(Path(tmp), point)

                completed = self._crash_queue(repository, store, point)
                recovered = OperationLedger(repository).reconcile(auto_complete_state=True)
                repeated = OperationLedger(repository).reconcile(auto_complete_state=True)

                self.assertEqual(completed.returncode, 91)
                self.assertTrue(recovered.ok)
                self.assertTrue(repeated.ok)
                self.assertEqual(len(store.history()), 1)
                self.assertEqual(store.latest_task("crash-task").status, "done")

    def test_corrupted_operation_blocks_canonical_mutation_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, store = self._queued_project(Path(tmp), "corrupt")
            ledger = OperationLedger(repository)
            ledger.root.mkdir(parents=True)
            (ledger.root / "broken.json").write_text("{", encoding="utf-8")

            report = ledger.reconcile()
            completed = self._run_queue(repository, store)

            self.assertEqual(report.blocking[0].classification, "corrupted")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("reconciliation_blocked", completed.stdout)
            self.assertEqual(self._git(repository, "rev-list", "--count", "HEAD"), "1")

    def test_rollback_crash_after_commit_auto_completes_exact_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, store, target = self._completed_project(root)
            code = (
                "import sys\n"
                "from pathlib import Path\n"
                "from llm_harness.rollback import apply_rollback\n"
                "from llm_harness.state import HohStateStore\n"
                "repo,state,target=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]\n"
                "apply_rollback(repo,HohStateStore(state),'rollback-task',target,"
                "'Crash recovery test',('git status --short',))\n"
            )
            environment = self._environment()
            environment["HOH_ENABLE_TEST_CRASH_INJECTION"] = "1"
            environment["HOH_TEST_CRASH_POINT"] = "rollback.after_commit"

            crashed = subprocess.run(
                [sys.executable, "-c", code, str(repository), str(store.root), target],
                cwd=Path(__file__).parents[1],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            head = self._git(repository, "rev-parse", "HEAD")
            before = OperationLedger(repository).reconcile()
            after = OperationLedger(repository).reconcile(auto_complete_state=True)

            self.assertEqual(crashed.returncode, 91)
            self.assertEqual(
                before.blocking[0].classification,
                "commit_exists_state_missing",
            )
            self.assertTrue(after.ok)
            self.assertEqual(store.latest_task("rollback-task").status, "rolled_back")
            self.assertEqual(store.rollback_records()[0].rollback_commit, head)

    def _queued_project(self, root: Path, label: str) -> tuple[Path, HohStateStore]:
        repository = root / "repo"
        repository.mkdir()
        self._git(repository, "init")
        self._git(repository, "config", "user.email", "operations@example.local")
        self._git(repository, "config", "user.name", "Operation Tests")
        (repository / ".gitignore").write_text(".tmp\n", encoding="utf-8")
        self._git(repository, "add", ".gitignore")
        self._git(repository, "commit", "-m", "Initial")
        store = HohStateStore(root / f"state-{label}")
        store.enqueue(
            WorkItem(
                id="crash-task",
                title="Crash recovery",
                objective="Create the deterministic stub artifact.",
                acceptance_criteria=("HARNESS_DEMO.md exists.",),
                verification_commands=(
                    f'"{sys.executable}" -c "from pathlib import Path; '
                    "assert Path('HARNESS_DEMO.md').exists()\"",
                ),
                allowed_paths=("HARNESS_DEMO.md",),
            )
        )
        (store.root / "harness.test.json").write_text(
            json.dumps({"worker": {"type": "stub"}}),
            encoding="utf-8",
        )
        return repository, store

    def _completed_project(self, root: Path) -> tuple[Path, HohStateStore, str]:
        repository = root / "repo"
        repository.mkdir()
        self._git(repository, "init")
        self._git(repository, "config", "user.email", "operations@example.local")
        self._git(repository, "config", "user.name", "Operation Tests")
        (repository / "README.md").write_text("# Operations\n", encoding="utf-8")
        self._git(repository, "add", "README.md")
        self._git(repository, "commit", "-m", "Initial")
        (repository / "feature.txt").write_text("feature\n", encoding="utf-8")
        self._git(repository, "add", "feature.txt")
        self._git(repository, "commit", "-m", "Feature")
        target = self._git(repository, "rev-parse", "HEAD")
        store = HohStateStore(root / "rollback-state")
        task = WorkItem(
            id="rollback-task",
            title="Rollback task",
            objective="Create feature.",
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
        return repository, store, target

    def _crash_queue(
        self,
        repository: Path,
        store: HohStateStore,
        point: str,
    ) -> subprocess.CompletedProcess[str]:
        environment = self._environment()
        environment["HOH_ENABLE_TEST_CRASH_INJECTION"] = "1"
        environment["HOH_TEST_CRASH_POINT"] = point
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "llm_harness",
                "queue-run-next",
                "--project-root",
                str(repository),
                "--state-root",
                str(store.root),
                "--config",
                str(store.root / "harness.test.json"),
                "--timeout",
                "5",
                "--json",
            ],
            cwd=Path(__file__).parents[1],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )

    def _run_queue(
        self,
        repository: Path,
        store: HohStateStore,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "llm_harness",
                "queue-run-next",
                "--project-root",
                str(repository),
                "--state-root",
                str(store.root),
                "--config",
                str(store.root / "harness.test.json"),
                "--timeout",
                "5",
                "--json",
            ],
            cwd=Path(__file__).parents[1],
            env=self._environment(),
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )

    def _start_queue(
        self,
        repository: Path,
        store: HohStateStore,
    ) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "llm_harness",
                "queue-run-next",
                "--project-root",
                str(repository),
                "--state-root",
                str(store.root),
                "--config",
                str(store.root / "harness.test.json"),
                "--timeout",
                "5",
                "--json",
            ],
            cwd=Path(__file__).parents[1],
            env=self._environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
        return environment

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
